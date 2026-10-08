from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter

import httpx

from app.config import get_settings
from app.services.secrets import resolve_secret


def documents(store, workspace_id: str) -> list[dict]:
    result = []
    for item in store.list_memory_items(workspace_id=workspace_id, status="active"):
        result.append({"id": item.id, "kind": "memory", "title": item.summary, "content": item.summary + "\n" + json.dumps(item.detail_json, ensure_ascii=False), "task_id": item.task_id})
    papers = {item.id: item for item in store.list_research_evidence(workspace_id=workspace_id)}
    for brief in store.list_research_briefs(workspace_id=workspace_id):
        result.append({"id": brief.id, "kind": "brief", "title": brief.question, "content": brief.report, "task_id": brief.task_id})
        papers.update({paper.id: paper for paper in brief.papers if paper.workspace_id == workspace_id})
    for paper in papers.values():
        result.append({"id": paper.id, "kind": "paper", "title": paper.title, "content": paper.abstract + "\n" + "\n".join(paper.evidence_snippets), "url": paper.url})
    return result


def chunks_for(docs: list[dict], workspace_id: str) -> list[dict]:
    chunks = []
    for doc in docs:
        text = (doc["title"] + "\n" + doc["content"]).strip()
        for start in range(0, len(text), 1200):
            content = text[start:start + 1500]
            identity = f"{workspace_id}:{doc['id']}:{start}"
            chunks.append({"id": hashlib.sha256(identity.encode()).hexdigest(), "workspace_id": workspace_id, "source_id": doc["id"], "kind": doc["kind"], "title": doc["title"], "content": content})
    return chunks


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.network_enabled:
        raise ValueError("NETWORK_DISABLED")
    if not settings.embedding_model or not settings.embedding_base_url:
        raise ValueError("EMBEDDING_NOT_CONFIGURED")
    key = resolve_secret(settings.embedding_api_key_env)
    if not key:
        raise ValueError("EMBEDDING_CREDENTIAL_MISSING")
    response = httpx.post(settings.embedding_base_url.rstrip("/") + "/embeddings", json={"model": settings.embedding_model, "input": texts}, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    response.raise_for_status()
    data = sorted(response.json()["data"], key=lambda item: item["index"])
    if [item["index"] for item in data] != list(range(len(texts))):
        raise ValueError("EMBEDDING_RESPONSE_INVALID")
    vectors = [item["embedding"] for item in data]
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or not 1 <= next(iter(dimensions)) <= 4096:
        raise ValueError("EMBEDDING_DIMENSIONS_INVALID")
    if any(not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector) or not any(vector) for vector in vectors):
        raise ValueError("EMBEDDING_VECTOR_INVALID")
    return vectors


def _connection():
    import psycopg
    if not get_settings().postgres_dsn:
        raise ValueError("POSTGRES_DSN_REQUIRED")
    return psycopg.connect(get_settings().postgres_dsn)


def _ensure_schema(conn):
    conn.execute("create extension if not exists vector")
    conn.execute("""create table if not exists knowledge_chunks (
        id text primary key, workspace_id text not null, source_id text not null,
        model text not null, body jsonb not null, embedding vector not null
    )""")
    conn.execute("create index if not exists knowledge_chunks_workspace on knowledge_chunks(workspace_id, model)")


def rebuild_index(store, workspace_id: str, graph: bool = False) -> dict:
    docs = documents(store, workspace_id)
    chunks = chunks_for(docs, workspace_id)
    backend = get_settings().knowledge_backend
    if backend == "pgvector":
        from psycopg.types.json import Jsonb
        vectors = []
        for start in range(0, len(chunks), 32):
            vectors.extend(embed([item["content"] for item in chunks[start:start + 32]]))
        with _connection() as conn:
            _ensure_schema(conn)
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", ("knowledge:" + workspace_id,))
            conn.execute("delete from knowledge_chunks where workspace_id=%s", (workspace_id,))
            for chunk, vector in zip(chunks, vectors, strict=True):
                conn.execute("insert into knowledge_chunks values (%s,%s,%s,%s,%s,%s::vector)", (chunk["id"], workspace_id, chunk["source_id"], get_settings().embedding_model, Jsonb(chunk), json.dumps(vector)))
    elif backend != "local":
        raise ValueError("KNOWLEDGE_BACKEND_UNSUPPORTED")
    graph_count = sync_graph(store, workspace_id) if graph else None
    store.add_audit_log(action="knowledge.index", resource_type="workspace", resource_id=workspace_id, decision="completed", detail_json={"workspace_id": workspace_id, "backend": backend, "chunks": len(chunks)})
    return {"backend": backend, "documents": len(docs), "chunks": len(chunks), "graph_nodes": graph_count}


def _terms(text: str) -> Counter:
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    return Counter(words)


def search(store, workspace_id: str, query: str, limit: int) -> list[dict]:
    docs = documents(store, workspace_id)
    visible_ids = {doc["id"] for doc in docs}
    if get_settings().knowledge_backend == "pgvector":
        vector = embed([query])[0]
        with _connection() as conn:
            _ensure_schema(conn)
            rows = conn.execute("""select body, 1 - (embedding <=> %s::vector) as score
                from knowledge_chunks where workspace_id=%s and model=%s
                and source_id = any(%s) and vector_dims(embedding)=%s
                order by embedding <=> %s::vector limit %s""", (json.dumps(vector), workspace_id, get_settings().embedding_model, list(visible_ids), len(vector), json.dumps(vector), limit)).fetchall()
        return [{**body, "score": float(score)} for body, score in rows]
    query_terms = _terms(query)
    results = []
    for chunk in chunks_for(docs, workspace_id):
        terms = _terms(chunk["content"])
        common = sum(min(count, terms.get(term, 0)) for term, count in query_terms.items())
        if common:
            results.append({**chunk, "score": round(common / max(1, sum(query_terms.values())), 4)})
    return sorted(results, key=lambda item: (-item["score"], item["id"]))[:limit]


def local_graph(store, workspace_id: str) -> dict:
    docs = documents(store, workspace_id)
    nodes = {doc["id"]: {"id": doc["id"], "kind": doc["kind"], "title": doc["title"]} for doc in docs}
    edges = []
    for doc in docs:
        task_id = doc.get("task_id")
        if task_id:
            task = store.get_task(task_id)
            if task and task.workspace_id == workspace_id:
                nodes[task_id] = {"id": task_id, "kind": "task", "title": task.title}
                edges.append({"source": task_id, "target": doc["id"], "relation": "produced"})
    for brief in store.list_research_briefs(workspace_id=workspace_id):
        for citation in brief.citations:
            if citation.get("paper_id") in nodes:
                edges.append({"source": brief.id, "target": citation["paper_id"], "relation": "cites"})
    return {"nodes": list(nodes.values()), "edges": edges}


def _driver():
    from neo4j import GraphDatabase
    settings = get_settings()
    password = os.environ.get(settings.neo4j_password_env)
    if not settings.neo4j_uri or not password:
        raise ValueError("NEO4J_NOT_CONFIGURED")
    return GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, password), connection_timeout=10)


def sync_graph(store, workspace_id: str) -> int:
    graph = local_graph(store, workspace_id)
    def replace(tx):
        tx.run("MERGE (w:ResearchForgeWorkspace {id:$workspace}) SET w.updated_at=datetime()", workspace=workspace_id).consume()
        tx.run("MATCH (n:ResearchForgeNode {workspace_id:$workspace}) DETACH DELETE n", workspace=workspace_id).consume()
        tx.run("UNWIND $nodes AS row CREATE (n:ResearchForgeNode {workspace_id:$workspace, id:row.id, kind:row.kind, title:row.title})", workspace=workspace_id, nodes=graph["nodes"]).consume()
        tx.run("UNWIND $edges AS row MATCH (a:ResearchForgeNode {workspace_id:$workspace, id:row.source}), (b:ResearchForgeNode {workspace_id:$workspace, id:row.target}) CREATE (a)-[:RELATED {relation:row.relation}]->(b)", workspace=workspace_id, edges=graph["edges"]).consume()
    with _driver() as driver, driver.session() as session:
        session.run("CREATE CONSTRAINT rf_workspace_id IF NOT EXISTS FOR (w:ResearchForgeWorkspace) REQUIRE w.id IS UNIQUE").consume()
        session.execute_write(replace)
    return len(graph["nodes"])


def graph(store, workspace_id: str) -> dict:
    if not get_settings().neo4j_uri:
        return local_graph(store, workspace_id)
    visible = {node["id"] for node in local_graph(store, workspace_id)["nodes"]}
    with _driver() as driver, driver.session() as session:
        nodes = [dict(row["n"]) for row in session.run("MATCH (n:ResearchForgeNode {workspace_id:$workspace}) WHERE n.id IN $ids RETURN n LIMIT 1000", workspace=workspace_id, ids=list(visible))]
        edges = [dict(row) for row in session.run("MATCH (a:ResearchForgeNode {workspace_id:$workspace})-[r:RELATED]->(b:ResearchForgeNode {workspace_id:$workspace}) WHERE a.id IN $ids AND b.id IN $ids RETURN a.id AS source, b.id AS target, r.relation AS relation LIMIT 5000", workspace=workspace_id, ids=[n["id"] for n in nodes])]
    return {"nodes": nodes, "edges": edges}
