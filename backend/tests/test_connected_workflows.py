import asyncio
import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.domain.schemas import CreateMemoryItemRequest, CreateResearchBriefRequest, ModelInvokeResponse, PolicyVersion, ResearchPaper
from app.infra.store import InMemoryStore
from app.research.workflow import run_research_workflow
from app.research.notebooks import ExecuteNotebookRequest, execute_notebook
from app.services.extensions import deliver_webhook, invoke_mcp
from app.services.git_workflow import PublishPatchRequest, git, preview_patch, publish_patch
from app.services import knowledge


@pytest.fixture(autouse=True)
def settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_real_mcp_stdio_round_trip_and_allowlist():
    config = {"transport": "stdio", "command": sys.executable, "args": [str(Path(__file__).parent / "fixtures" / "mcp_math_server.py")], "allowed_tools": ["add"]}
    async def exercise():
        tools = await invoke_mcp(config, "tools/list", {})
        assert any(item["name"] == "add" for item in tools["tools"])
        result = await invoke_mcp(config, "tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}})
        assert not result["isError"]
        assert "5" in str(result["content"])
        with pytest.raises(ValueError, match="MCP_TOOL_NOT_ALLOWED"):
            await invoke_mcp(config, "tools/call", {"name": "delete"})
    asyncio.run(exercise())


def test_webhook_delivers_signed_body(monkeypatch):
    received = {}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            received["signature"] = self.headers["X-ResearchForge-Signature"]
            self.send_response(204)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("RESEARCHFORGE_NETWORK_ENABLED", "1")
    monkeypatch.setenv("RF_TEST_HOOK_SECRET", "local-hook-secret")
    try:
        result = deliver_webhook({"url": f"http://127.0.0.1:{server.server_port}", "secret_env": "RF_TEST_HOOK_SECRET"}, "run.completed", {"run_id": "r1"}, "d1")
        assert result["status_code"] == 204
        assert received["signature"] == "sha256=" + hmac.new(b"local-hook-secret", received["body"], hashlib.sha256).hexdigest()
        assert json.loads(received["body"])["payload"]["run_id"] == "r1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_research_graph_uses_model_plan_and_rejects_unknown_citations():
    paper = ResearchPaper(id="p1", title="Evidence", abstract="Measured result")
    plan = {"hypotheses": [{"id": "h1", "statement": "A testable claim", "rationale": "Measured result", "evidence_paper_ids": ["p1"]}], "experiments": [{"id": "e1", "title": "Control experiment", "method": "Compare treatment to baseline", "success_metric": "mean difference"}]}
    def model_call(request, papers):
        return ModelInvokeResponse(provider="configured", model_name="test", output_text=json.dumps(plan)), "test"
    result = run_research_workflow(CreateResearchBriefRequest(question="Claim?"), model_call, [paper])
    assert result["plan"].hypotheses[0].statement == "A testable claim"
    plan["hypotheses"][0]["evidence_paper_ids"] = ["invented"]
    with pytest.raises(ValueError, match="RESEARCH_PLAN_INVALID"):
        run_research_workflow(CreateResearchBriefRequest(question="Claim?"), model_call, [paper])


def test_custom_notebook_executes_kernel_and_records_cell_failure():
    result = execute_notebook(ExecuteNotebookRequest(cells=["print(parameters['number'] * 2)"], parameters={"number": 21}))
    assert result["status"] == "completed", result
    assert "42" in str(result["cells"][1]["outputs"])
    failed = execute_notebook(ExecuteNotebookRequest(cells=["raise ValueError('expected failure')"]))
    assert failed["status"] == "failed"
    assert any(item.get("output_type") == "error" for item in failed["cells"][1]["outputs"])


def test_notebook_api_result_download_and_retry(monkeypatch):
    import nbformat
    from fastapi.testclient import TestClient
    from app.api import notebooks
    from app.main import app
    from app.infra.store import store

    brief = store.create_research_brief(CreateResearchBriefRequest(question="Notebook route acceptance"))
    results = iter([
        {"status": "failed", "cells": [dict(nbformat.v4.new_code_cell("print(42)"))], "metrics": {"exit_code": 1}},
        {"status": "completed", "cells": [dict(nbformat.v4.new_code_cell("print(42)"))], "metrics": {"exit_code": 0}},
    ])
    monkeypatch.setattr(notebooks, "execute_notebook", lambda request: next(results))
    with TestClient(app) as client:
        created = client.post(f"/api/v1/research/briefs/{brief.id}/execute", json={"cells": ["print(42)"]})
        assert created.status_code == 200, created.text
        notebook = created.json()
        assert notebook["status"] == "failed"
        detail = client.get(f"/api/v1/research/notebook-runs/{notebook['id']}")
        assert detail.json()["id"] == notebook["id"]
        download = client.get(f"/api/v1/research/notebook-runs/{notebook['id']}/download")
        assert download.status_code == 200
        nbformat.validate(nbformat.reads(download.text, as_version=4))
        retried = client.post(f"/api/v1/jobs/{notebook['job_id']}/retry")
        assert retried.status_code == 200, retried.text
        assert retried.json()["result"]["status"] == "completed"
        assert retried.json()["job"]["retry_of_job_id"] == notebook["job_id"]


def test_git_publish_bundle_leaves_original_repo_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    source = tmp_path / "source"
    source.mkdir()
    git(["init", "-b", "main"], source)
    (source / "code.py").write_text("value = 1\n", encoding="utf-8")
    git(["add", "code.py"], source)
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@local", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@local"}
    git(["-c", "commit.gpgsign=false", "commit", "-m", "initial"], source, env=env)
    revision = git(["rev-parse", "HEAD"], source)
    run = SimpleNamespace(id="run_publish_test", metrics={"source_revision": revision, "source_clean": True})
    repository = SimpleNamespace(default_branch="main", provider="git", url=None)
    request = PublishPatchRequest(run_id=run.id, branch="researchforge/test", title="Fix value")
    patch = "--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    result = publish_patch(repository, run, PolicyVersion(), patch, request, source)
    assert Path(result["bundle_path"]).is_file()
    assert git(["rev-parse", "HEAD"], source) == revision
    assert (source / "code.py").read_text() == "value = 1\n"
    imported = tmp_path / "imported"
    git(["clone", result["bundle_path"], str(imported)])
    git(["checkout", "researchforge/test"], imported)
    assert (imported / "code.py").read_text() == "value = 2\n"


def test_git_publish_preview_validates_without_creating_side_effects(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    source = tmp_path / "source"
    source.mkdir()
    git(["init", "-b", "main"], source)
    (source / "code.py").write_text("value = 1\n", encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@local", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@local"}
    git(["add", "code.py"], source)
    git(["-c", "commit.gpgsign=false", "commit", "-m", "initial"], source, env=env)
    revision = git(["rev-parse", "HEAD"], source)
    run = SimpleNamespace(id="run_preview_test", metrics={"source_revision": revision, "source_clean": True})
    repository = SimpleNamespace(default_branch="main", provider="git", url=None)
    request = PublishPatchRequest(run_id=run.id, branch="researchforge/preview", title="Preview value")
    patch = "--- a/code.py\n+++ b/code.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    result = preview_patch(repository, run, PolicyVersion(), patch, request, source)
    assert result["policy_allowed"] is True
    assert result["changed_files"] == 1
    assert result["changed_lines"] == 2
    assert result["side_effects"] == []
    assert git(["rev-parse", "HEAD"], source) == revision
    assert not (tmp_path / "published").exists()


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_POSTGRES_DSN"), reason="requires pgvector")
def test_pgvector_reindex_deletes_archived_and_respects_workspace(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_POSTGRES_DSN", os.environ["RESEARCHFORGE_TEST_POSTGRES_DSN"])
    monkeypatch.setenv("RESEARCHFORGE_KNOWLEDGE_BACKEND", "pgvector")
    monkeypatch.setenv("RESEARCHFORGE_EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setattr(knowledge, "embed", lambda texts: [[1.0, 0.0, 0.0] for _ in texts])
    store = InMemoryStore()
    item = store.create_memory_item(CreateMemoryItemRequest(key="indexed", summary="indexed evidence"))
    assert knowledge.rebuild_index(store, "workspace_default")["chunks"] == 1
    assert knowledge.search(store, "workspace_default", "evidence", 5)[0]["source_id"] == item.id
    assert knowledge.search(store, "different_workspace", "evidence", 5) == []
    item.status = "archived"
    assert knowledge.search(store, "workspace_default", "evidence", 5) == []
    assert knowledge.rebuild_index(store, "workspace_default")["chunks"] == 0
