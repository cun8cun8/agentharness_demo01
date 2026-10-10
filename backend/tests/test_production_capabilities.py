import asyncio
import os
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.domain.schemas import CreateMemoryItemRequest, CreateTaskRequest, CreateRepositoryConnectionRequest, ReleaseGateResponse, UpdateUserRequest
from app.infra.records import StoreConflictError, flatten_snapshot, inflate_records, merge_record
from app.infra.store import InMemoryStore
from app.agent.critic import apply_model_verdict
from app.services import knowledge
from app.services.rate_limit import SlidingWindowLimiter


@pytest.fixture(autouse=True)
def settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_record_merge_preserves_independent_changes_and_rejects_overlap():
    base = {"status": "queued", "metrics": {"tokens": 10, "cost": 1}}
    local = deepcopy(base)
    local["metrics"]["tokens"] = 20
    remote = deepcopy(base)
    remote["status"] = "running"
    assert merge_record(base, local, remote) == {"status": "running", "metrics": {"tokens": 20, "cost": 1}}
    remote["metrics"]["tokens"] = 30
    with pytest.raises(StoreConflictError):
        merge_record(base, local, remote)


def test_release_gate_record_uses_its_job_identity_when_flattened():
    gate = ReleaseGateResponse(
        evaluation_run_id="eval_release",
        agent_strategy_id="repair_with_critic_v3",
        passed=True,
        status="candidate",
        job_id="job_release_gate",
    )
    snapshot = {"release_gates": [gate.model_dump(mode="json")]}
    flattened = flatten_snapshot(snapshot)
    assert flattened[("release_gates", "job_release_gate")]["status"] == "candidate"
    assert inflate_records(flattened)["release_gates"] == [gate.model_dump(mode="json")]


def test_sliding_window_limiter_returns_retry_after():
    limiter = SlidingWindowLimiter()
    assert limiter.allow("test", 1)[0]
    allowed, retry_after = limiter.allow("test", 1)
    assert not allowed
    assert retry_after >= 1


def test_persisted_strategy_and_subscriptions_survive_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_PATH", str(tmp_path / "store.json"))
    store = InMemoryStore()
    store.strategies["repair_with_critic_v3"].runtime_config["retry"] = False
    task = store.create_task(CreateTaskRequest(title="persist", goal="verify"))
    run = store.create_run(task.id, "repair_with_critic_v3", "policy_default_v1", None)
    queue = store.subscribe_events(run.id)
    store.refresh()
    assert queue in store.event_subscribers[run.id]
    assert store.strategies["repair_with_critic_v3"].runtime_config["retry"] is False
    assert inflate_records(flatten_snapshot(store._snapshot_data()))["events"][run.id][0]["event_type"] == "run.created"


def test_production_auth_cannot_impersonate_and_logout_revokes(monkeypatch):
    from app.main import app
    from app.services.github_oauth import create_session_cookie
    monkeypatch.setenv("RESEARCHFORGE_ENV", "production")
    monkeypatch.setenv("RESEARCHFORGE_API_KEY", "production-test-admin-secret")
    monkeypatch.setenv("RESEARCHFORGE_GITHUB_OAUTH_STATE_SECRET", "production-test-session-secret")
    get_settings.cache_clear()
    with TestClient(app, base_url="https://testserver") as client:
        assert client.get("/api/v1/tasks", headers={"X-User-ID": "user_admin"}).status_code == 401
        assert client.get("/api/v1/auth/session").status_code == 401
        assert client.get("/api/v1/tasks", headers={"X-API-Key": "production-test-admin-secret"}).status_code == 200
        client.cookies.set("researchforge_user_id", "invalid-session")
        assert client.get("/api/v1/tasks").status_code == 401
        assert client.get("/api/v1/auth/session", headers={"X-API-Key": "production-test-admin-secret"}).status_code == 200
        client.cookies.clear()
        token = create_session_cookie("user_admin")
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/api/v1/tasks", headers=headers).status_code == 200
        assert client.post("/api/v1/auth/logout", headers={**headers, "Origin": "https://untrusted.invalid"}).status_code == 403
        assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
        assert client.get("/api/v1/tasks", headers=headers).status_code == 401
    # Restore the cached settings before the fixture restores process env.
    monkeypatch.setenv("RESEARCHFORGE_ENV", "local")
    monkeypatch.delenv("RESEARCHFORGE_API_KEY", raising=False)
    get_settings.cache_clear()


def test_model_critic_veto_and_malformed_response():
    review = {"accepted": True, "score": 1, "checks": {}, "observation": "ok"}
    result = apply_model_verdict(deepcopy(review), '{"accepted": false, "score": 0.2, "reasons": ["regression"]}')
    assert result["accepted"] is False
    assert result["model_verdict"]["reasons"] == ["regression"]
    assert not apply_model_verdict(deepcopy(review), '{"accepted": "true"}')["accepted"]
    review["accepted"] = False
    assert not apply_model_verdict(review, '{"accepted": true, "score": 1}')["accepted"]


def test_model_critic_normalizes_ten_point_scores():
    review = {"accepted": True, "score": 1.0, "checks": {}}

    result = apply_model_verdict(
        review,
        '{"accepted": true, "score": "9.5/10", "reasons": ["tests pass"]}',
    )

    assert result["accepted"] is True
    assert result["score"] == 0.95
    assert result["checks"]["model_verdict_valid"] is True

    percentage = apply_model_verdict(
        {"accepted": True, "score": 1.0, "checks": {}},
        '{"accepted": true, "score": 100, "reasons": ["all checks pass"]}',
    )
    assert percentage["accepted"] is True
    assert percentage["score"] == 1.0


def test_production_disables_fixture_patches_and_local_sandbox(monkeypatch, tmp_path):
    from app.agent.runtime import AgentRuntime
    from app.services.sandbox_runner import SandboxRunner, SandboxUnavailable

    monkeypatch.setenv("RESEARCHFORGE_AUTH_MODE", "production")
    monkeypatch.setenv("RESEARCHFORGE_ALLOW_MOCK_MODELS", "0")
    get_settings.cache_clear()
    local = InMemoryStore()
    task = local.create_task(CreateTaskRequest(title="Fix date parser", goal="repair", execution_config={"task_kind": "date"}))
    assert AgentRuntime._select_patch(task, None) == (None, "unavailable")
    assert AgentRuntime._select_patch(task, "model diff") == ("model diff", "model")
    with pytest.raises(SandboxUnavailable, match="PRODUCTION_SANDBOX_ISOLATION_REQUIRED"):
        SandboxRunner().run(["python", "--version"], cwd=tmp_path, workspace_root=tmp_path, timeout_seconds=5)


def test_knowledge_filters_workspace_and_archived_memory():
    store = InMemoryStore()
    item = store.create_memory_item(CreateMemoryItemRequest(key="repair", summary="日期解析边界错误", detail_json={"file": "date.py"}))
    assert knowledge.search(store, "workspace_default", "日期", 5)[0]["source_id"] == item.id
    assert knowledge.search(store, "other_workspace", "日期", 5) == []
    item.status = "archived"
    assert knowledge.search(store, "workspace_default", "日期", 5) == []


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_POSTGRES_DSN"), reason="requires isolated PostgreSQL")
def test_postgres_two_writers_conflict_recovery_and_cancel(monkeypatch):
    from app.infra.record_store import PostgresRecordStore
    monkeypatch.setenv("RESEARCHFORGE_PERSISTENCE", "1")
    monkeypatch.setenv("RESEARCHFORGE_STORE_BACKEND", "postgres")
    monkeypatch.setenv("RESEARCHFORGE_POSTGRES_DSN", os.environ["RESEARCHFORGE_TEST_POSTGRES_DSN"])
    get_settings.cache_clear()
    first = PostgresRecordStore()
    second = PostgresRecordStore()
    try:
        repository = first.create_repository_connection(CreateRepositoryConnectionRequest(
            name="fresh typed repository", provider="local", local_path="/tmp/acceptance"))
        assert second.get_repository_connection(repository.id).workspace_id == "workspace_default"
        left = first.create_task(CreateTaskRequest(title="writer one", goal="persist"))
        right = second.create_task(CreateTaskRequest(title="writer two", goal="persist"))
        first.refresh()
        second.refresh()
        assert first.get_task(right.id) is not None
        assert second.get_task(left.id) is not None
        first.get_task(left.id).title = "first edit"
        first._persist()
        second.get_task(left.id).title = "conflicting edit"
        with pytest.raises(StoreConflictError):
            second._persist()
        assert second.get_task(left.id).title == "first edit"
        run = first.create_run(left.id, "repair_baseline_v1", "policy_default_v1", None)
        second.refresh()
        second.request_cancel(run.id)
        assert first.is_cancel_requested(run.id)
        first.update_run(run.id, metrics={"probe": True})
        second.refresh()
        assert second.is_cancel_requested(run.id)
        assert second.get_task(right.id) is not None
        audit_marker = "postgres-audit-cross-worker"
        first.add_audit_log(
            action="test.cross_worker",
            resource_type="test",
            resource_id=audit_marker,
            decision="allow",
        )
        assert any(
            item.resource_id == audit_marker
            for item in second.list_audit_logs(action="test.cross_worker")
        )
    finally:
        first._engine.dispose()
        second._engine.dispose()
