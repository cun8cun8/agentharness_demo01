from __future__ import annotations

import asyncio

from app.config import get_settings


async def probe_dependencies(store) -> dict:
    settings = get_settings()
    checks = []

    async def check(name, enabled, probe):
        if not enabled:
            checks.append({"name": name, "status": "disabled"})
            return
        try:
            await asyncio.wait_for(asyncio.to_thread(probe), timeout=12)
            checks.append({"name": name, "status": "ok"})
        except Exception as exc:
            # Connection errors can contain credentials; expose only the exception type.
            checks.append({"name": name, "status": "failed", "error": type(exc).__name__})

    def postgres():
        import psycopg
        with psycopg.connect(settings.postgres_dsn, connect_timeout=5) as conn:
            conn.execute("SELECT 1").fetchone()
            if settings.knowledge_backend == "pgvector":
                if not conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'").fetchone():
                    raise RuntimeError("PGVECTOR_NOT_INSTALLED")

    def redis():
        from redis import Redis
        with Redis.from_url(settings.redis_url, socket_timeout=5, socket_connect_timeout=5) as client:
            client.ping()

    def artifacts():
        if not store.artifact_blob_store.probe().get("ready"):
            raise RuntimeError("ARTIFACT_STORE_UNAVAILABLE")

    def neo4j():
        from neo4j import GraphDatabase
        from app.services.secrets import resolve_secret
        with GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, resolve_secret(settings.neo4j_password_env)), connection_timeout=5) as driver:
            driver.verify_connectivity()

    await check("postgres", settings.store_backend == "postgres", postgres)
    await check("redis", settings.job_queue_backend == "redis" or settings.rate_limit_backend == "redis", redis)
    await check("artifacts", store.artifact_blob_store.enabled, artifacts)
    await check("neo4j", bool(settings.neo4j_uri), neo4j)
    if settings.event_bus_backend in {"kafka", "redpanda"}:
        from aiokafka.admin import AIOKafkaAdminClient
        client = AIOKafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers, request_timeout_ms=5000)
        try:
            await asyncio.wait_for(client.start(), 8)
            await asyncio.wait_for(client.list_topics(), 8)
            checks.append({"name": "kafka", "status": "ok"})
        except Exception as exc:
            checks.append({"name": "kafka", "status": "failed", "error": type(exc).__name__})
        finally:
            await client.close()
    return {"status": "partial" if any(item["status"] == "failed" for item in checks) else "ok", "checks": checks,
            "coverage": "dependency_connectivity_only"}
