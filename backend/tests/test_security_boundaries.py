import asyncio
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.rate_limit import SlidingWindowLimiter
from app.services.request_limits import RequestLimitsMiddleware


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_RATE_LIMIT_BACKEND", "local")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_strict_catalog_and_old_persisted_tasks_cannot_use_answers(monkeypatch, tmp_path):
    from app.agent.runtime import AgentRuntime
    from app.benchmarks.catalog import _build_execution_config
    from app.domain.schemas import CreateTaskRequest
    from app.infra.store import InMemoryStore
    monkeypatch.setenv("RESEARCHFORGE_STRICT_BENCHMARKS", "1")
    get_settings.cache_clear()
    config = _build_execution_config("coding_fix_001", tmp_path, {"patch": "answer", "retry_patch": "retry"})
    assert "patch" not in config and "retry_patch" not in config
    task = InMemoryStore().create_task(CreateTaskRequest(title="repair", goal="fix", execution_config={"patch": "old answer", "retry_patch": "old retry"}))
    assert AgentRuntime._select_patch(task, None) == (None, "unavailable")
    assert AgentRuntime._select_retry_patch(task, None, "old answer", "execution_config") == (None, "unavailable")


def test_limiter_cleanup_accepts_new_keys():
    limiter = SlidingWindowLimiter()
    limiter._events.update({str(i): deque([0]) for i in range(10001)})
    assert limiter.allow("first", 1)[0]
    assert limiter.allow("new", 1)[0]


def test_get_auth_limit_ignores_rotating_credentials(monkeypatch):
    from app.main import app
    from app.services import request_limits
    monkeypatch.setenv("RESEARCHFORGE_AUTH_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setattr(request_limits, "request_limiter", SlidingWindowLimiter())
    get_settings.cache_clear()
    with TestClient(app) as client:
        assert client.get("/api/v1/auth/config", headers={"X-API-Key": "one"}).status_code == 200
        assert client.get("/api/v1/auth/config", headers={"X-API-Key": "two"}).status_code == 200
        response = client.get("/api/v1/auth/config", headers={"X-API-Key": "three"})
        assert response.status_code == 429 and int(response.headers["retry-after"]) > 0


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_actual_stream_body_is_bounded(monkeypatch, headers):
    monkeypatch.setenv("RESEARCHFORGE_MAX_REQUEST_BODY_BYTES", "5")
    get_settings.cache_clear()
    async def run():
        reached = []
        sent = []
        async def app(scope, receive, send):
            reached.append(True)
        messages = iter([{"type": "http.request", "body": b"abc", "more_body": True},
                         {"type": "http.request", "body": b"def", "more_body": False}])
        async def receive():
            return next(messages)
        async def send(message):
            sent.append(message)
        await RequestLimitsMiddleware(app)({"type": "http", "path": "/api/v1/tasks", "method": "POST", "headers": headers, "client": ("stream-test", 123)}, receive, send)
        assert not reached
        assert sent[0]["status"] == 413
    asyncio.run(run())


def test_csrf_is_bound_to_session(monkeypatch):
    from starlette.requests import Request
    from app.services.authentication import create_csrf_token, csrf_valid
    monkeypatch.setenv("RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET", "test-secret")
    token = create_csrf_token("session-one")
    def request(session):
        return Request({"type": "http", "headers": [(b"cookie", f"researchforge_user_id={session}; researchforge_csrf={token}".encode()), (b"x-csrf-token", token.encode())]})
    assert csrf_valid(request("session-one"))
    assert not csrf_valid(request("session-two"))


def test_api_responses_include_browser_security_headers(monkeypatch):
    from app.main import app

    monkeypatch.setenv("RESEARCHFORGE_API_KEY", "security-header-test")
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_health_probe_contracts_are_available():
    from app.main import app

    with TestClient(app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code in {200, 503}
    assert ready.json()["status"] in {"ready", "not_ready"}


@pytest.mark.parametrize("path", ["tests/test_value.py", ".env", "nested/.env", "../escape.py", "source/../tests/check.py"])
def test_patch_policy_protects_unprefixed_headers(tmp_path, path):
    from app.domain.schemas import PolicyVersion
    from app.policy.engine import default_policy_engine
    decision = default_policy_engine.evaluate_tool("file.write_patch", {
        "patch": f"--- {path}\n+++ {path}\n@@ -1 +1 @@\n-old\n+new\n",
    }, PolicyVersion(), str(tmp_path))
    assert not decision.allowed
    assert decision.reason in {"PROTECTED_PATH", "PATH_OUTSIDE_REPO"}


def test_patch_policy_rejects_symlinks_and_malformed_hunks(tmp_path):
    from app.domain.schemas import PolicyVersion
    from app.policy.engine import default_policy_engine
    patches = [
        "--- a/code.py\n+++ b/code.py\n@@\n-old\n+new\n",
        "diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+../secret\n",
    ]
    for patch in patches:
        assert not default_policy_engine.evaluate_tool("file.write_patch", {"patch": patch}, PolicyVersion(), str(tmp_path)).allowed


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_REDIS_URL"), reason="requires isolated Redis")
def test_redis_limit_is_atomic_across_replicas():
    from app.services.rate_limit import RedisWindowLimiter
    replicas = [RedisWindowLimiter(os.environ["RESEARCHFORGE_TEST_REDIS_URL"]) for _ in range(3)]
    key = "test-replicas:" + uuid4().hex
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda index: replicas[index % 3].allow(key, 7, 3), range(30)))
        assert sum(allowed for allowed, _ in results) == 7
        assert all(allowed or retry > 0 for allowed, retry in results)
    finally:
        for replica in replicas:
            replica.client.close()
