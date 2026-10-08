import json
import os
from io import BytesIO
from uuid import uuid4
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.domain.schemas import RunPhase, RunStatus
from app.main import app
from app.infra.store import WorkspaceQuotaExceeded, store


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_responses_include_correlated_request_headers() -> None:
    request_id = "smoke-request-20260925"
    response = client.get("/health", headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id
    assert float(response.headers["x-process-time-ms"]) >= 0
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_system_diagnostics_redact_connection_credentials() -> None:
    previous = {
        key: os.environ.get(key)
        for key in (
            "RESEARCHFORGE_REDIS_URL",
            "RESEARCHFORGE_POSTGRES_DSN",
            "RESEARCHFORGE_MODEL_BASE_URL",
            "RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL",
        )
    }
    os.environ["RESEARCHFORGE_REDIS_URL"] = "redis://:redis-secret@example.test:6379/0"
    os.environ["RESEARCHFORGE_POSTGRES_DSN"] = "postgresql://user:pg-secret@example.test:5432/researchforge"
    os.environ["RESEARCHFORGE_MODEL_BASE_URL"] = "https://model.test/v1?api_key=model-secret"
    os.environ["RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL"] = "https://storage.test/?token=storage-secret"
    get_settings.cache_clear()
    try:
        config = client.get("/api/v1/system/config")
        assert config.status_code == 200
        payload = config.json()
        assert "redis-secret" not in payload["redis_url"]
        assert payload["redis_url"] == "redis://example.test:6379/0"
        assert "pg-secret" not in payload["postgres_dsn"]
        assert payload["postgres_dsn"] == "postgresql://example.test:5432/researchforge"
        assert payload["model_gateway_base_url"] == "https://model.test/v1?api_key=%2A%2A%2Aredacted%2A%2A%2A"
        assert payload["artifact_store_endpoint_url"] == "https://storage.test/?token=%2A%2A%2Aredacted%2A%2A%2A"
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_api_errors_include_machine_readable_shape() -> None:
    response = client.get("/api/v1/tasks/not-a-real-task")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"] == "Task not found"
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["message"] == "Task not found"
    assert body["error"]["details"]["path"] == "/api/v1/tasks/not-a-real-task"
    assert body["error"]["details"]["request_id"] == response.headers["x-request-id"]

    validation_response = client.post("/api/v1/tasks", json={})
    assert validation_response.status_code == 422
    validation_body = validation_response.json()
    assert validation_body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(validation_body["detail"], list)


def test_optional_api_key_protects_api_without_affecting_health() -> None:
    previous = os.environ.get("RESEARCHFORGE_API_KEY")
    os.environ["RESEARCHFORGE_API_KEY"] = "test-api-key"
    get_settings.cache_clear()
    try:
        health_response = client.get("/health")
        assert health_response.status_code == 200

        rejected = client.get("/api/v1/tasks")
        assert rejected.status_code == 401
        assert rejected.json()["error"]["code"] == "UNAUTHENTICATED"

        accepted = client.get("/api/v1/tasks", headers={"X-API-Key": "test-api-key"})
        assert accepted.status_code == 200
        session = client.get(
            "/api/v1/auth/session",
            headers={"X-API-Key": "test-api-key"},
        )
        assert session.status_code == 200
        cookie_access = client.get("/api/v1/tasks")
        assert cookie_access.status_code == 200
    finally:
        client.cookies.clear()
        if previous is None:
            os.environ.pop("RESEARCHFORGE_API_KEY", None)
        else:
            os.environ["RESEARCHFORGE_API_KEY"] = previous
        get_settings.cache_clear()


def test_metrics_token_allows_production_prometheus_scrape() -> None:
    keys = [
        "RESEARCHFORGE_ENV",
        "RESEARCHFORGE_AUTH_MODE",
        "RESEARCHFORGE_METRICS_TOKEN",
        "RESEARCHFORGE_METRICS_TOKEN_ENV",
    ]
    previous = {key: os.environ.get(key) for key in keys}
    client.cookies.clear()
    os.environ.update(
        {
            "RESEARCHFORGE_ENV": "production",
            "RESEARCHFORGE_AUTH_MODE": "production",
            "RESEARCHFORGE_METRICS_TOKEN": "prometheus-test-token",
        }
    )
    os.environ.pop("RESEARCHFORGE_METRICS_TOKEN_ENV", None)
    get_settings.cache_clear()
    try:
        rejected = client.get("/api/v1/telemetry/metrics")
        assert rejected.status_code == 401
        accepted = client.get(
            "/api/v1/telemetry/metrics",
            headers={"Authorization": "Bearer prometheus-test-token"},
        )
        assert accepted.status_code == 200
        assert "researchforge_runs_total" in accepted.text
    finally:
        client.cookies.clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_api_key_can_be_resolved_from_a_secret_file(tmp_path) -> None:
    secret_file = tmp_path / "api-key"
    secret_file.write_text("file-backed-test-key\n", encoding="utf-8")
    keys = ["RESEARCHFORGE_API_KEY", "RESEARCHFORGE_API_KEY_ENV", "RF_TEST_API_KEY"]
    previous = {key: os.environ.get(key) for key in keys}
    client.cookies.clear()
    os.environ.pop("RESEARCHFORGE_API_KEY", None)
    os.environ["RESEARCHFORGE_API_KEY_ENV"] = "RF_TEST_API_KEY"
    os.environ["RF_TEST_API_KEY"] = f"file://{secret_file.resolve()}"
    get_settings.cache_clear()
    try:
        config = client.get("/api/v1/auth/config")
        assert config.status_code == 200
        assert config.json()["api_key_enabled"] is True
        accepted = client.get("/api/v1/tasks", headers={"X-API-Key": "file-backed-test-key"})
        assert accepted.status_code == 200
    finally:
        client.cookies.clear()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


def test_rbac_enforces_viewer_read_only_access() -> None:
    suffix = uuid4().hex[:8]
    user_response = client.post(
        "/api/v1/users",
        json={
            "id": f"user_viewer_{suffix}",
            "workspace_id": "workspace_default",
            "email": f"viewer-{suffix}@researchforge.local",
            "name": "只读测试成员",
            "role": "viewer",
            "status": "active",
        },
    )
    assert user_response.status_code == 200
    headers = {"x-user-id": user_response.json()["id"]}

    read_response = client.get("/api/v1/tasks", headers=headers)
    assert read_response.status_code == 200

    invalid_session = client.get(
        "/api/v1/auth/session",
        headers={"x-user-id": f"missing_{suffix}"},
    )
    assert invalid_session.status_code == 401
    assert invalid_session.json()["error"]["code"] == "UNAUTHENTICATED"

    write_response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={
            "title": "只读成员不应创建任务",
            "goal": "验证 RBAC。",
        },
    )
    assert write_response.status_code == 403
    assert write_response.json()["error"]["code"] == "FORBIDDEN"

    model_write_response = client.post(
        "/api/v1/models",
        headers=headers,
        json={
            "id": f"model_viewer_{suffix}",
            "model_name": "mock-viewer",
        },
    )
    assert model_write_response.status_code == 403

    benchmark_write_response = client.post(
        "/api/v1/benchmarks/golden-tasks/seed",
        headers=headers,
    )
    assert benchmark_write_response.status_code == 403


def test_non_admin_cannot_cross_workspace_boundaries() -> None:
    suffix = uuid4().hex[:8]
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={
            "name": f"隔离测试工作区 {suffix}",
            "owner_id": "user_admin",
            "status": "active",
        },
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["id"]

    user_response = client.post(
        "/api/v1/users",
        json={
            "workspace_id": workspace_id,
            "email": f"tenant-{suffix}@researchforge.local",
            "name": "隔离测试操作员",
            "role": "operator",
            "status": "active",
        },
    )
    assert user_response.status_code == 200
    headers = {"x-user-id": user_response.json()["id"]}

    foreign_task_response = client.post(
        "/api/v1/tasks",
        json={
            "workspace_id": "workspace_default",
            "title": f"默认工作区任务 {suffix}",
            "goal": "跨工作区访问测试。",
        },
    )
    assert foreign_task_response.status_code == 200
    foreign_task_id = foreign_task_response.json()["id"]

    own_task_response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={
            "workspace_id": workspace_id,
            "title": f"隔离工作区任务 {suffix}",
            "goal": "验证本工作区任务可访问。",
        },
    )
    assert own_task_response.status_code == 200
    own_task_id = own_task_response.json()["id"]

    listed = client.get("/api/v1/tasks", headers=headers)
    assert listed.status_code == 200
    listed_ids = {item["id"] for item in listed.json()["items"]}
    assert own_task_id in listed_ids
    assert foreign_task_id not in listed_ids

    foreign_get = client.get(f"/api/v1/tasks/{foreign_task_id}", headers=headers)
    assert foreign_get.status_code == 403
    assert foreign_get.json()["error"]["code"] == "FORBIDDEN"

    foreign_filter = client.get(
        "/api/v1/tasks",
        headers=headers,
        params={"workspace_id": "workspace_default"},
    )
    assert foreign_filter.status_code == 403

    foreign_create = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={
            "workspace_id": "workspace_default",
            "title": "不应写入其他工作区",
            "goal": "验证跨工作区写入被拒绝。",
        },
    )
    assert foreign_create.status_code == 403

    own_repository = client.post(
        "/api/v1/integrations/repositories",
        headers=headers,
        json={
            "workspace_id": workspace_id,
            "name": f"隔离仓库 {suffix}",
            "provider": "local",
            "local_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
        },
    )
    assert own_repository.status_code == 200
    own_repository_id = own_repository.json()["id"]
    assert client.get(
        f"/api/v1/integrations/repositories/{own_repository_id}",
        headers=headers,
    ).status_code == 200

    foreign_repository = client.post(
        "/api/v1/integrations/repositories",
        json={
            "workspace_id": "workspace_default",
            "name": f"默认仓库 {suffix}",
            "provider": "local",
            "local_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
        },
    )
    assert foreign_repository.status_code == 200
    foreign_repository_id = foreign_repository.json()["id"]
    assert client.get(
        f"/api/v1/integrations/repositories/{foreign_repository_id}",
        headers=headers,
    ).status_code == 403


def test_workspace_quotas_limit_tasks_and_active_runs() -> None:
    suffix = uuid4().hex[:8]
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={
            "name": f"配额测试工作区 {suffix}",
            "owner_id": "user_admin",
            "max_tasks": 1,
            "max_active_runs": 1,
            "max_daily_cost": 50,
        },
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["id"]

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "workspace_id": workspace_id,
            "title": f"配额任务 {suffix}",
            "goal": "验证工作区配额。",
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    second_task = client.post(
        "/api/v1/tasks",
        json={
            "workspace_id": workspace_id,
            "title": f"超额任务 {suffix}",
            "goal": "应被任务数配额拦截。",
        },
    )
    assert second_task.status_code == 429
    assert second_task.json()["error"]["code"] == "WORKSPACE_TASK_QUOTA_EXCEEDED"

    first_run = store.create_run(
        task_id=task_id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name=None,
    )
    assert first_run.status == RunStatus.QUEUED

    second_run = client.post(f"/api/v1/tasks/{task_id}/runs", json={})
    assert second_run.status_code == 429
    assert second_run.json()["error"]["code"] == "WORKSPACE_ACTIVE_RUN_QUOTA_EXCEEDED"

    usage = client.get(f"/api/v1/workspaces/{workspace_id}/usage")
    assert usage.status_code == 200
    assert usage.json()["usage"]["task_count"] == 1
    assert usage.json()["usage"]["active_run_count"] == 1
    assert usage.json()["remaining"]["tasks"] == 0

    cost_workspace_response = client.post(
        "/api/v1/workspaces",
        json={
            "name": f"成本配额工作区 {suffix}",
            "owner_id": "user_admin",
            "max_daily_cost": 0.00001,
        },
    )
    assert cost_workspace_response.status_code == 200
    cost_workspace_id = cost_workspace_response.json()["id"]
    cost_task_response = client.post(
        "/api/v1/tasks",
        json={
            "workspace_id": cost_workspace_id,
            "title": f"成本配额任务 {suffix}",
            "goal": "验证运行时成本配额。",
        },
    )
    assert cost_task_response.status_code == 200
    cost_run = store.create_run(
        task_id=cost_task_response.json()["id"],
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name=None,
    )
    with pytest.raises(WorkspaceQuotaExceeded) as quota_error:
        store.record_run_usage(cost_run.id, 10, 0.0001)
    assert quota_error.value.code == "WORKSPACE_DAILY_COST_QUOTA_EXCEEDED"


def test_workspace_and_user_management_are_audited() -> None:
    suffix = uuid4().hex[:8]
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={
            "name": f"研发工作区 {suffix}",
            "owner_id": "user_admin",
            "status": "active",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()

    user_response = client.post(
        "/api/v1/users",
        json={
            "workspace_id": workspace["id"],
            "email": f"operator-{suffix}@researchforge.local",
            "name": f"测试操作员 {suffix}",
            "role": "operator",
            "status": "active",
        },
    )
    assert user_response.status_code == 200
    user = user_response.json()

    session_response = client.get("/api/v1/auth/session", headers={"x-user-id": user["id"]})
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["user"]["id"] == user["id"]
    assert session["workspace"]["id"] == workspace["id"]
    assert "runs:write" in session["permissions"]

    updated_user_response = client.patch(
        f"/api/v1/users/{user['id']}",
        json={"role": "viewer", "status": "active"},
    )
    assert updated_user_response.status_code == 200
    assert updated_user_response.json()["role"] == "viewer"

    updated_workspace_response = client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"name": f"已更新研发工作区 {suffix}"},
    )
    assert updated_workspace_response.status_code == 200
    assert updated_workspace_response.json()["name"].startswith("已更新")

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "workspace_id": workspace["id"],
            "type": "coding",
            "title": f"工作区任务 {suffix}",
            "repo_path": None,
            "test_command": "pytest",
            "goal": "验证任务归属工作区。",
        },
    )
    assert task_response.status_code == 200
    assert task_response.json()["workspace_id"] == workspace["id"]
    workspace_tasks = client.get(
        f"/api/v1/tasks?workspace_id={workspace['id']}"
    )
    assert workspace_tasks.status_code == 200
    assert workspace_tasks.json()["total"] == 1
    assert workspace_tasks.json()["items"][0]["workspace_id"] == workspace["id"]
    workspace_stats = client.get(
        f"/api/v1/tasks/stats?workspace_id={workspace['id']}"
    )
    assert workspace_stats.status_code == 200
    assert workspace_stats.json()["task_count"] == 1
    assert workspace_stats.json()["run_count"] == 0

    repository_response = client.post(
        "/api/v1/integrations/repositories",
        json={
            "workspace_id": workspace["id"],
            "name": f"工作区仓库 {suffix}",
            "provider": "local",
            "local_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "default_branch": "main",
        },
    )
    assert repository_response.status_code == 200
    repository = repository_response.json()
    assert repository["workspace_id"] == workspace["id"]
    repository_get = client.get(f"/api/v1/integrations/repositories/{repository['id']}")
    assert repository_get.status_code == 200
    repository_update = client.patch(
        f"/api/v1/integrations/repositories/{repository['id']}",
        json={"name": f"已更新仓库 {suffix}", "status": "inactive"},
    )
    assert repository_update.status_code == 200
    assert repository_update.json()["name"].startswith("已更新")
    repository_audit = client.get(
        f"/api/v1/audit-logs?resource_type=repository_connection&resource_id={repository['id']}"
    )
    assert repository_audit.status_code == 200
    repository_actions = {item["action"] for item in repository_audit.json()["items"]}
    assert {"repository_connection.create", "repository_connection.update"}.issubset(repository_actions)

    users_response = client.get(f"/api/v1/users?workspace_id={workspace['id']}")
    assert users_response.status_code == 200
    assert any(item["id"] == user["id"] for item in users_response.json()["items"])

    workspace_audit = client.get(
        f"/api/v1/audit-logs?resource_type=workspace&resource_id={workspace['id']}"
    )
    assert workspace_audit.status_code == 200
    workspace_actions = {item["action"] for item in workspace_audit.json()["items"]}
    assert {"workspace.create", "workspace.update"}.issubset(workspace_actions)

    user_audit = client.get(f"/api/v1/audit-logs?resource_type=user&resource_id={user['id']}")
    assert user_audit.status_code == 200
    user_actions = {item["action"] for item in user_audit.json()["items"]}
    assert {"user.create", "user.update"}.issubset(user_actions)


def test_run_pause_and_resume_create_a_linked_continuation() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "可暂停运行",
            "repo_path": None,
            "test_command": "pytest",
            "goal": "验证合作式暂停和恢复。",
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]
    queued_run = store.create_run(
        task_id=task_id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    pause_response = client.post(f"/api/v1/runs/{queued_run.id}/pause", json={})
    assert pause_response.status_code == 200
    paused = pause_response.json()
    assert paused["status"] == "paused"
    assert paused["error_summary"] == "PAUSED_BY_USER"
    assert paused["metrics"]["tests_total"] == 0
    assert paused["metrics"]["tests_passed_ratio"] == 0
    artifacts_response = client.get(f"/api/v1/runs/{queued_run.id}/artifacts")
    assert any(item["name"] == "paused-report.md" for item in artifacts_response.json()["items"])

    resume_response = client.post(f"/api/v1/runs/{queued_run.id}/resume")
    assert resume_response.status_code == 200
    resumed = resume_response.json()
    assert resumed["id"] != queued_run.id
    assert resumed["status"] in {"queued", "completed", "failed"}
    repeated_resume_response = client.post(f"/api/v1/runs/{queued_run.id}/resume")
    assert repeated_resume_response.status_code == 200
    assert repeated_resume_response.json()["id"] == resumed["id"]
    resume_audit = client.get(
        f"/api/v1/audit-logs?resource_type=run&resource_id={resumed['id']}&action=run.resume"
    )
    assert resume_audit.status_code == 200
    assert resume_audit.json()["items"][0]["detail_json"]["resume_mode"] == "paused"


def test_paused_agent_job_can_resume_from_queue() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "从队列恢复暂停任务",
            "repo_path": None,
            "test_command": "pytest",
            "goal": "验证 Job 级恢复入口。",
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]
    paused_run = store.create_run(
        task_id=task_id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )
    store.update_run(
        paused_run.id,
        status=RunStatus.PAUSED,
        phase=RunPhase.REPORT,
        error_summary="PAUSED_BY_USER",
    )
    paused_job = store.create_job(
        kind="agent_run",
        resource_id=paused_run.id,
        task_id=task_id,
    )
    store.update_job(paused_job.id, "paused", "PAUSED_BY_USER")

    response = client.post(f"/api/v1/jobs/{paused_job.id}/resume")
    assert response.status_code == 200
    assert response.json()["run"]["id"] != paused_run.id
    assert response.json()["job"]["status"] == "completed"
    repeated_response = client.post(f"/api/v1/jobs/{paused_job.id}/resume")
    assert repeated_response.status_code == 409
    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=job&resource_id={paused_job.id}&action=job.resume"
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["items"]


def test_task_search_and_update_are_audited() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Searchable configuration task",
            "repo_path": None,
            "test_command": "pytest",
            "goal": "Verify searchable task updates.",
            "execution_config": {"task_kind": "date"},
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    search_response = client.get("/api/v1/tasks?query=searchable")
    assert search_response.status_code == 200
    assert any(item["id"] == task_id for item in search_response.json()["items"])

    update_response = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={
            "title": "Updated searchable task",
            "execution_config": {
                "task_kind": "price",
                "source_path": "src/price.py",
            },
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["title"] == "Updated searchable task"
    assert update_response.json()["execution_config"]["task_kind"] == "price"

    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=task&resource_id={task_id}&action=task.update"
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["items"][0]["detail_json"]["changed_fields"] == [
        "execution_config",
        "title",
    ]


def test_task_run_trace_dataset_flow() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Fix date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Fix failing tests.",
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    assert run_response.status_code == 200
    queued_run = run_response.json()
    assert queued_run["status"] in {"queued", "completed"}

    get_run_response = client.get(f"/api/v1/runs/{queued_run['id']}")
    assert get_run_response.status_code == 200
    run = get_run_response.json()
    assert run["status"] == "completed"
    assert run["phase"] == "report"
    assert run["tool_call_count"] >= 4
    assert run["metrics"]["trace_completeness"] == 1.0
    assert run["metrics"]["baseline_tests_passed"] < run["metrics"]["baseline_tests_total"]
    assert run["metrics"]["validation_tests_passed"] == run["metrics"]["tests_passed"]
    assert run["metrics"]["test_passed_delta"] > 0

    auto_dataset = client.get(
        f"/api/v1/datasets/trace-items?use_case=sft_candidate"
    )
    assert auto_dataset.status_code == 200
    assert any(
        item["agent_run_id"] == run["id"] and item["trace_type"] == "SUCCESS_TRACE"
        for item in auto_dataset.json()["items"]
    )

    steps_response = client.get(f"/api/v1/runs/{run['id']}/steps")
    assert steps_response.status_code == 200
    steps = steps_response.json()["items"]
    assert len(steps) >= 6

    artifacts_response = client.get(f"/api/v1/runs/{run['id']}/artifacts")
    assert artifacts_response.status_code == 200
    artifacts = artifacts_response.json()["items"]
    artifact_types = {item["type"] for item in artifacts}
    assert {"diff", "log", "report"}.issubset(artifact_types)
    assert any(item["name"] == "model-assist-planning.json" for item in artifacts)
    assert any(item["name"] == "model-assist-patch-generation.json" for item in artifacts)
    diff_artifact = next(item for item in artifacts if item["type"] == "diff")
    assert diff_artifact["metadata"]["patch_source"] == "task_profile_fallback"
    assert run["metrics"]["model_assist_count"] >= 2
    assert run["total_tokens"] > 0

    files_response = client.get(f"/api/v1/tasks/{task_id}/files")
    assert files_response.status_code == 200
    assert any(item["path"] == "src/date_parser.py" for item in files_response.json()["items"])
    limited_files_response = client.get(f"/api/v1/tasks/{task_id}/files?limit=1")
    assert limited_files_response.status_code == 200
    limited_files = limited_files_response.json()
    assert limited_files["total"] >= len(limited_files["items"])
    assert limited_files["truncated"] is True

    file_response = client.get(f"/api/v1/tasks/{task_id}/files/src/date_parser.py")
    assert file_response.status_code == 200
    assert file_response.json()["path"] == "src/date_parser.py"
    assert "parse_date" in file_response.json()["content"]

    outside_file_response = client.get(
        f"/api/v1/tasks/{task_id}/files/%2E%2E/expected.md"
    )
    assert outside_file_response.status_code == 400

    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=run&resource_id={run['id']}"
    )
    assert audit_response.status_code == 200
    audit_items = audit_response.json()["items"]
    assert any(item["action"] == "run.create" for item in audit_items)
    assert any(item["action"] == "run.finish" for item in audit_items)
    assert any(item["action"] == "model.invoke" for item in audit_items)
    hook_history = client.get(
        "/api/v1/extensions/hooks/dispatches?event_type=run.completed"
    )
    assert hook_history.status_code == 200
    assert any(
        item["payload"].get("run_id") == run["id"]
        for item in hook_history.json()["items"]
    )

    dataset_response = client.post(
        "/api/v1/datasets/trace-items",
        json={
            "agent_run_id": run["id"],
            "quality_label": "good",
            "trace_type": "SUCCESS_TRACE",
            "use_case": "sft_candidate",
            "failure_type": "resolved_test_failure",
            "root_cause": "日期格式解析逻辑缺少常见格式兼容。",
            "agent_error_step_id": steps[0]["id"],
            "human_preferred_action": "保留最小修复并补充回归测试。",
            "usable_for_sft": True,
            "usable_for_preference": True,
            "notes": "来自 smoke test 的完整人工标注。",
        },
    )
    assert dataset_response.status_code == 200
    assert dataset_response.json()["status"] == "candidate"
    trace_item_id = dataset_response.json()["id"]
    assert dataset_response.json()["root_cause"] == "日期格式解析逻辑缺少常见格式兼容。"

    invalid_step_response = client.post(
        "/api/v1/datasets/trace-items",
        json={
            "agent_run_id": run["id"],
            "quality_label": "good",
            "trace_type": "SUCCESS_TRACE",
            "use_case": "sft_candidate",
            "agent_error_step_id": "step_missing_for_trace_validation",
        },
    )
    assert invalid_step_response.status_code == 400
    assert invalid_step_response.json()["detail"] == "TRACE_STEP_NOT_FOUND"

    get_dataset_response = client.get(f"/api/v1/datasets/trace-items/{trace_item_id}")
    assert get_dataset_response.status_code == 200
    assert get_dataset_response.json()["trace_type"] == "SUCCESS_TRACE"
    assert get_dataset_response.json()["agent_error_step_id"] == steps[0]["id"]

    update_dataset_response = client.patch(
        f"/api/v1/datasets/trace-items/{trace_item_id}",
        json={"status": "reviewed", "notes": "Reviewed by smoke test."},
    )
    assert update_dataset_response.status_code == 200
    assert update_dataset_response.json()["status"] == "reviewed"
    assert update_dataset_response.json()["notes"] == "Reviewed by smoke test."

    invalid_trace_status = client.patch(
        f"/api/v1/datasets/trace-items/{trace_item_id}",
        json={"status": "not_a_dataset_status"},
    )
    assert invalid_trace_status.status_code == 400
    assert invalid_trace_status.json()["detail"] == "TRACE_STATUS_INVALID"

    wrong_run_step = client.patch(
        f"/api/v1/datasets/trace-items/{trace_item_id}",
        json={"agent_error_step_id": "step_missing_for_trace_validation"},
    )
    assert wrong_run_step.status_code == 400
    assert wrong_run_step.json()["detail"] == "TRACE_STEP_NOT_FOUND"

    dataset_audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=trace_dataset_item&resource_id={trace_item_id}"
    )
    assert dataset_audit_response.status_code == 200
    dataset_audit_actions = {item["action"] for item in dataset_audit_response.json()["items"]}
    assert {"dataset.create", "dataset.update"}.issubset(dataset_audit_actions)

    context_response = client.get(f"/api/v1/tasks/{task_id}/context")
    assert context_response.status_code == 200
    context = context_response.json()
    assert context["latest_run"]["id"] == run["id"]
    assert any(item["kind"] == "agent_run" for item in context["jobs"])
    assert any(item["id"] == trace_item_id for item in context["trace_items"])
    assert context["memory_recommendations"]

    export_response = client.get("/api/v1/datasets/export")
    assert export_response.status_code == 200
    assert queued_run["id"] in export_response.text
    assert '"schema_version": "trace_dataset_v1"' in export_response.text

    quality_report = client.get("/api/v1/datasets/quality-report")
    assert quality_report.status_code == 200
    report = quality_report.json()["report"]
    assert report["trace_item_count"] >= 1
    assert report["approved_trace_item_count"] >= 0
    assert report["status_distribution"]["reviewed"] >= 1

    training_bundle = client.get("/api/v1/datasets/training-bundle/export?include_pending=true")
    assert training_bundle.status_code == 200
    bundle = training_bundle.json()
    assert bundle["export_metadata"]["schema_version"] == "training_bundle_v1"
    assert bundle["summary"]["sft_record_count"] >= 1
    assert any(
        item["dataset_item"]["id"] == trace_item_id
        for item in bundle["sft_records"]
    )
    export_jobs = client.get("/api/v1/jobs?kind=dataset_export&resource_id=training_bundle_v1")
    assert export_jobs.status_code == 200
    assert export_jobs.json()["items"][0]["result_json"]["schema_version"] == "training_bundle_v1"

    snapshot_response = client.post(
        "/api/v1/datasets/snapshots",
        json={
            "version_name": "smoke_trace_dataset",
            "quality_label": "good",
            "use_case": "sft_candidate",
            "include_preferences": True,
        },
    )
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert trace_item_id in snapshot["trace_item_ids"]
    assert snapshot["item_count"] >= 1

    snapshot_export = client.get(f"/api/v1/datasets/snapshots/{snapshot['id']}/export")
    assert snapshot_export.status_code == 200
    assert '"schema_version": "dataset_snapshot_v1"' in snapshot_export.text

    run_export = client.get(f"/api/v1/runs/{run['id']}/export")
    assert run_export.status_code == 200
    with ZipFile(BytesIO(run_export.content)) as archive:
        assert {"run.json", "steps.json", "tool_calls.json", "artifacts.json"}.issubset(
            set(archive.namelist())
        )


def test_run_auto_routes_model_when_missing() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Fix date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Fix failing tests.",
        },
    )
    task_id = task_response.json()["id"]

    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
        },
    )
    assert run_response.status_code == 200
    assert run_response.json()["model_name"] == "mock-coding-agent"


def test_model_route_prefers_strategy_specific_model() -> None:
    model_response = client.post(
        "/api/v1/models",
        json={
            "id": "model_strategy_trace",
            "provider": "mock",
            "model_name": "mock-trace-agent",
            "role": "coding",
            "context_window": 64000,
            "cost_per_1k_tokens": 0.001,
            "config": {"strategy_ids": ["repair_with_trace_v2"]},
            "status": "active",
        },
    )
    assert model_response.status_code == 200
    update_response = client.post(
        "/api/v1/models",
        json={
            "id": "model_strategy_trace",
            "provider": "mock",
            "model_name": "mock-trace-agent-v2",
            "role": "coding",
            "context_window": 64000,
            "cost_per_1k_tokens": 0.0005,
            "config": {"strategy_ids": ["repair_with_trace_v2", "repair_with_critic_v3"]},
            "status": "active",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["model_name"] == "mock-trace-agent-v2"

    route_response = client.post(
        "/api/v1/models/route",
        json={
            "task_type": "coding",
            "strategy_id": "repair_with_trace_v2",
            "estimated_tokens": 2000,
        },
    )
    assert route_response.status_code == 200
    assert route_response.json()["model_name"] == "mock-trace-agent-v2"
    model_audit = client.get(
        "/api/v1/audit-logs?resource_type=model&resource_id=model_strategy_trace&action=model.upsert"
    )
    assert model_audit.status_code == 200
    assert len(model_audit.json()["items"]) >= 2
    assert model_audit.json()["items"][-1]["detail_json"]["model_name"] == "mock-trace-agent"


def test_model_health_reports_active_models() -> None:
    response = client.get("/api/v1/models/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 4
    assert any(item["model_name"] == "mock-coding-agent" and item["healthy"] for item in payload["items"])
    assert any(item["reason"] == "MOCK_PROVIDER" for item in payload["items"])


def test_tool_policy_preview_is_audited() -> None:
    tools_response = client.get("/api/v1/tools")
    assert tools_response.status_code == 200
    assert any(item["name"] == "file.read" for item in tools_response.json()["items"])

    allow_response = client.post(
        "/api/v1/tools/file.read/policy-preview",
        json={
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "input": {"path": "src/date_parser.py"},
        },
    )
    assert allow_response.status_code == 200
    assert allow_response.json()["allowed"] is True

    protected_response = client.post(
        "/api/v1/tools/file.read/policy-preview",
        json={
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "input": {"path": "config/.env"},
        },
    )
    assert protected_response.status_code == 200
    assert protected_response.json()["allowed"] is False
    assert protected_response.json()["reason"] == "PROTECTED_PATH"

    dangerous_response = client.post(
        "/api/v1/tools/shell.run/policy-preview",
        json={
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "input": {"command": "python -c \"print(1)\" && rm -rf ."},
        },
    )
    assert dangerous_response.status_code == 200
    assert dangerous_response.json()["allowed"] is False
    assert dangerous_response.json()["reason"] == "COMMAND_BLOCKED"

    audit_response = client.get("/api/v1/audit-logs?action=tool.policy_preview")
    assert audit_response.status_code == 200
    assert audit_response.json()["items"]


def test_policy_get_by_id_returns_saved_policy() -> None:
    policy_id = "policy_lookup_v1"
    create_response = client.post(
        "/api/v1/policies",
        json={
            "id": policy_id,
            "name": "查询策略",
            "allowed_tools": ["file.read"],
            "blocked_commands": ["rm -rf"],
            "allowed_commands": ["python"],
            "requires_approval_tools": [],
            "max_steps": 10,
            "max_runtime_seconds": 300,
            "max_patch_files": 2,
            "max_changed_lines": 20,
            "network_enabled": False,
            "status": "active",
        },
    )
    assert create_response.status_code == 200

    response = client.get(f"/api/v1/policies/{policy_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "查询策略"
    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=policy&resource_id={policy_id}&action=policy.upsert"
    )
    assert audit_response.status_code == 200
    assert audit_response.json()["items"][0]["detail_json"]["max_steps"] == 10


def test_seed_golden_tasks_and_run_benchmark() -> None:
    catalog_response = client.get("/api/v1/benchmarks/golden-tasks")
    assert catalog_response.status_code == 200
    first_catalog_item = catalog_response.json()["items"][0]
    assert first_catalog_item["expected_path"].endswith("expected.md")
    assert "Expected Behavior" in first_catalog_item["expected_text"]
    assert "all_tests_pass" in first_catalog_item["success_criteria"]
    assert first_catalog_item["execution_config"]["task_kind"] == "date"
    assert first_catalog_item["execution_config"]["source_path"] == "src/date_parser.py"
    assert first_catalog_item["execution_config"]["patch"].startswith("diff --git")

    seed_response = client.post("/api/v1/benchmarks/golden-tasks/seed")
    assert seed_response.status_code == 200
    task_ids = [item["id"] for item in seed_response.json()["items"][:2]]

    evaluation_response = client.post(
        "/api/v1/evaluations/runs",
        json={
            "benchmark_name": "coding_golden_v1",
            "task_ids": task_ids,
            "agent_strategy_id": "repair_with_critic_v3",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    assert evaluation_response.status_code == 200
    evaluation = evaluation_response.json()
    assert evaluation["status"] == "completed"
    assert evaluation["summary"]["task_count"] == 2
    assert evaluation["summary"]["success_rate"] == 1.0
    assert evaluation["summary"]["avg_tool_call_count"] > 0
    assert evaluation["summary"]["avg_trace_completeness"] > 0
    assert evaluation["summary"]["failure_type_distribution"] == {}
    assert len(evaluation["items"]) == 2
    date_metrics = evaluation["items"][0]["metrics"]
    price_metrics = evaluation["items"][1]["metrics"]
    assert date_metrics["failed_criteria"] == []
    assert "date_empty_or_invalid_returns_none" in date_metrics["met_criteria"]
    assert any(
        item["criterion"] == "date_valid_iso_preserved"
        and item["source"] == "expected.md"
        and item["passed"] is True
        for item in date_metrics["expected_checks"]
    )
    assert "decimal_safe_arithmetic" in price_metrics["met_criteria"]
    assert any(
        item["criterion"] == "no_string_money_return"
        and item["passed"] is True
        for item in price_metrics["expected_checks"]
    )
    evaluation_jobs = client.get("/api/v1/jobs?kind=evaluation")
    assert evaluation_jobs.status_code == 200
    assert any(
        item["result_json"].get("evaluation_run_id") == evaluation["id"]
        and item["result_json"].get("success_rate") == 1.0
        for item in evaluation_jobs.json()["items"]
    )

    report_response = client.get(f"/api/v1/evaluations/runs/{evaluation['id']}/report")
    assert report_response.status_code == 200
    assert "ResearchForge 评测验收报告" in report_response.text
    assert "平均工具调用" in report_response.text
    assert "Golden Task 目录校验" in report_response.text
    assert "策略运行约束" in report_response.text
    assert "任务级验收" in report_response.text
    assert "expected.md 检查明细" in report_response.text
    assert "日期空值/非法值返回 None" in report_response.text
    assert "Diff 风险" in report_response.text


def test_golden_acceptance_endpoint_returns_batch_summary() -> None:
    response = client.post(
        "/api/v1/benchmarks/golden-tasks/acceptance",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "limit": 1,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["acceptance"]["task_count"] == 1
    assert payload["acceptance"]["report_path"].startswith("/api/v1/evaluations/runs/")
    assert payload["acceptance"]["catalog_validation"]["all_have_execution_config"] is True
    assert payload["acceptance"]["items"][0]["criteria_total"] >= 3
    assert payload["acceptance"]["items"][0]["failed_criteria"] == []
    assert payload["acceptance"]["items"][0]["expected_checks"]
    job_response = client.get(f"/api/v1/jobs/{payload['acceptance']['job_id']}")
    assert job_response.status_code == 200
    assert job_response.json()["kind"] == "coding_acceptance"
    assert job_response.json()["result_json"]["passed_count"] == 1
    assert job_response.json()["result_json"]["report_path"].startswith("/api/v1/evaluations/runs/")
    assert job_response.json()["result_json"]["catalog_validation"]["all_have_execution_config"] is True

    validation = client.get("/api/v1/benchmarks/golden-tasks/validation")
    assert validation.status_code == 200
    assert validation.json()["all_valid"] is True
    assert validation.json()["task_count"] >= 10


def test_budget_exceeded_fails_run() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Fix date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Fix failing tests.",
            "budget": {
                "max_steps": 1,
                "max_runtime_seconds": 600,
                "max_tokens": 80000,
                "max_model_cost": 2.0,
                "max_tool_calls": 40,
            },
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    run_id = run_response.json()["id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "BUDGET_EXCEEDED"

    artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()["items"]
    assert any(item["name"] == "failure-report.md" for item in artifacts)

    memory = client.get(f"/api/v1/memory/items?agent_run_id={run_id}")
    assert memory.status_code == 200
    assert memory.json()["items"][0]["key"] == "BUDGET_EXCEEDED"


def test_token_budget_is_enforced_during_run() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Token budget task",
            "repo_path": None,
            "test_command": "pytest",
            "goal": "Exercise the live token budget.",
            "execution_config": {
                "task_kind": "date",
                "usage_estimate": {"step_tokens": 480, "tool_tokens": 720},
            },
            "budget": {
                "max_steps": 20,
                "max_runtime_seconds": 600,
                "max_tokens": 1000,
                "max_model_cost": 2.0,
                "max_tool_calls": 40,
            },
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    run = client.get(f"/api/v1/runs/{run_response.json()['id']}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "BUDGET_EXCEEDED"
    assert run["total_tokens"] > 1000
    assert run["metrics"]["tokens_used"] == run["total_tokens"]


def test_model_cost_budget_is_enforced_during_run() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Model cost budget task",
            "repo_path": None,
            "test_command": "pytest",
            "goal": "Exercise the live model cost budget.",
            "execution_config": {
                "task_kind": "date",
                "usage_estimate": {"step_tokens": 480, "tool_tokens": 720},
            },
            "budget": {
                "max_steps": 20,
                "max_runtime_seconds": 600,
                "max_tokens": 80000,
                "max_model_cost": 0.001,
                "max_tool_calls": 40,
            },
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    run = client.get(f"/api/v1/runs/{run_response.json()['id']}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "BUDGET_EXCEEDED"
    assert run["total_cost"] > 0.001
    assert run["metrics"]["model_cost_used"] == run["total_cost"]


def test_forced_failure_creates_failure_report() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Force failure recovery sample",
            "repo_path": "benchmarks/coding_golden_v1/task_004_force_failure/repo",
            "test_command": "python -m pytest -q",
            "goal": "Force failure after patch to exercise failure reporting.",
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    run_id = run_response.json()["id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "TESTS_STILL_FAILING"
    auto_failure_items = client.get(
        "/api/v1/datasets/trace-items?use_case=failure_case&failure_type=TESTS_STILL_FAILING"
    )
    assert auto_failure_items.status_code == 200
    assert any(item["agent_run_id"] == run_id for item in auto_failure_items.json()["items"])

    artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()["items"]
    assert any(item["name"] == "failure-report.md" for item in artifacts)

    memory = client.get(f"/api/v1/memory/items?agent_run_id={run_id}")
    assert memory.status_code == 200
    assert memory.json()["items"][0]["key"] == "TESTS_STILL_FAILING"

    trace_item = client.post(
        "/api/v1/datasets/trace-items",
        json={
            "agent_run_id": run_id,
            "quality_label": "bad",
            "trace_type": "FAILURE_TRACE",
            "use_case": "failure_case",
            "failure_type": "TESTS_STILL_FAILING",
            "root_cause": "Validation still fails after patch.",
            "human_preferred_action": "重新分析验证日志并生成更小补丁。",
            "usable_for_sft": False,
        },
    )
    assert trace_item.status_code == 200
    trace_item_id = trace_item.json()["id"]

    filtered_items = client.get(
        "/api/v1/datasets/trace-items?failure_type=TESTS_STILL_FAILING&status=candidate"
    )
    assert filtered_items.status_code == 200
    assert any(item["id"] == trace_item_id for item in filtered_items.json()["items"])

    failure_export = client.get(
        "/api/v1/datasets/failure-cases/export?use_case=failure_case&include_unlabeled=false"
    )
    assert failure_export.status_code == 200
    failure_record = json.loads(failure_export.text.splitlines()[0])
    assert failure_record["export_metadata"]["schema_version"] == "failure_case_v1"
    assert failure_record["run"]["error_summary"] == "TESTS_STILL_FAILING"
    assert failure_record["failed_steps"]
    assert failure_record["suggested_action"] == "重新分析验证日志并生成更小补丁。"


def test_task_execution_config_persists_through_snapshot() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Config driven task",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Keep execution config.",
            "execution_config": {
                "task_kind": "date",
                "source_path": "src/date_parser.py",
                "failure_observation": "Custom failure note.",
            },
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    get_task_response = client.get(f"/api/v1/tasks/{task_id}")
    assert get_task_response.status_code == 200
    assert get_task_response.json()["execution_config"]["source_path"] == "src/date_parser.py"


def test_policy_block_stops_patch_step() -> None:
    policy_response = client.post(
        "/api/v1/policies",
        json={
            "id": "policy_no_patch",
            "name": "No Patch Policy",
            "allowed_tools": ["test.run", "git.diff", "report.write"],
            "blocked_commands": [],
            "max_steps": 20,
            "max_runtime_seconds": 600,
            "max_patch_files": 3,
            "max_changed_lines": 80,
            "network_enabled": False,
            "status": "active",
        },
    )
    assert policy_response.status_code == 200

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Fix date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Fix failing tests.",
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_no_patch",
            "model_name": "mock-coding-agent",
        },
    )
    run_id = run_response.json()["id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "POLICY_BLOCKED"

    tool_calls = client.get(f"/api/v1/runs/{run_id}/tool-calls").json()["items"]
    assert any(call["status"] == "policy_blocked" for call in tool_calls)


def test_approval_required_policy_creates_decidable_request() -> None:
    policy_response = client.post(
        "/api/v1/policies",
        json={
            "id": "policy_shell_approval",
            "name": "Shell Approval Policy",
            "allowed_tools": ["file.read", "file.write_patch", "shell.run", "git.diff", "test.run", "report.write"],
            "blocked_commands": [],
            "allowed_commands": ["pytest", "python", "python3", "git"],
            "requires_approval_tools": ["shell.run"],
            "max_steps": 20,
            "max_runtime_seconds": 600,
            "max_patch_files": 3,
            "max_changed_lines": 80,
            "network_enabled": False,
            "status": "active",
        },
    )
    assert policy_response.status_code == 200

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Fix date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Fix failing tests.",
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_with_trace_v2",
            "policy_version_id": "policy_shell_approval",
        },
    )
    run_id = run_response.json()["id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "PRECHECK_FAILED"

    approvals = client.get(f"/api/v1/approvals?run_id={run_id}").json()["items"]
    assert approvals[0]["status"] == "pending"
    assert approvals[0]["reason"] == "APPROVAL_REQUIRED"

    decision = client.post(
        f"/api/v1/approvals/{approvals[0]['id']}/decision",
        json={"decision": "denied", "decided_by": "test", "reason": "Not approved in test"},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "denied"


def test_policy_upsert_overwrites_existing_configuration() -> None:
    policy_id = "policy_custom_form_v1"
    first_response = client.post(
        "/api/v1/policies",
        json={
            "id": policy_id,
            "name": "安全策略初始版",
            "allowed_tools": ["file.read"],
            "blocked_commands": ["rm -rf"],
            "allowed_commands": ["python"],
            "requires_approval_tools": [],
            "protected_read_patterns": [".env"],
            "protected_write_patterns": ["tests/*"],
            "max_steps": 8,
            "max_runtime_seconds": 120,
            "max_patch_files": 1,
            "max_changed_lines": 20,
            "network_enabled": False,
            "status": "draft",
        },
    )
    assert first_response.status_code == 200

    second_response = client.post(
        "/api/v1/policies",
        json={
            "id": policy_id,
            "name": "安全策略更新版",
            "allowed_tools": ["file.read", "file.write_patch", "test.run"],
            "blocked_commands": ["rm -rf", "curl | sh"],
            "allowed_commands": ["pytest", "python", "git"],
            "requires_approval_tools": ["shell.run"],
            "protected_read_patterns": [".env", "*.pem"],
            "protected_write_patterns": ["tests/*", "test_*"],
            "max_steps": 18,
            "max_runtime_seconds": 600,
            "max_patch_files": 3,
            "max_changed_lines": 80,
            "network_enabled": True,
            "status": "active",
        },
    )
    assert second_response.status_code == 200

    policies = client.get("/api/v1/policies").json()["items"]
    policy = next(item for item in policies if item["id"] == policy_id)
    assert policy["name"] == "安全策略更新版"
    assert policy["allowed_tools"] == ["file.read", "file.write_patch", "test.run"]
    assert policy["requires_approval_tools"] == ["shell.run"]
    assert policy["max_steps"] == 18
    assert policy["network_enabled"] is True
    assert policy["status"] == "active"


def test_approved_tool_request_can_resume_run() -> None:
    policy_response = client.post(
        "/api/v1/policies",
        json={
            "id": "policy_shell_approval_resume",
            "name": "Shell Approval Resume Policy",
            "allowed_tools": ["file.read", "file.write_patch", "shell.run", "git.diff", "test.run", "report.write"],
            "blocked_commands": [],
            "allowed_commands": ["pytest", "python", "python3", "git"],
            "requires_approval_tools": ["shell.run"],
            "max_steps": 40,
            "max_runtime_seconds": 600,
            "max_patch_files": 3,
            "max_changed_lines": 80,
            "network_enabled": False,
            "status": "active",
        },
    )
    assert policy_response.status_code == 200

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Approval resume date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "审批 shell 预检查后恢复运行。",
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]

    blocked_run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_with_trace_v2",
            "policy_version_id": "policy_shell_approval_resume",
        },
    )
    assert blocked_run_response.status_code == 200
    blocked_run_id = blocked_run_response.json()["id"]
    blocked_run = client.get(f"/api/v1/runs/{blocked_run_id}").json()
    assert blocked_run["status"] == "failed"
    assert blocked_run["error_summary"] == "PRECHECK_FAILED"

    approvals_response = client.get(f"/api/v1/approvals?run_id={blocked_run_id}")
    assert approvals_response.status_code == 200
    approvals = approvals_response.json()["items"]
    assert approvals[0]["status"] == "pending"
    assert approvals[0]["policy_version_id"] == "policy_shell_approval_resume"

    approval_id = approvals[0]["id"]
    decision = client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "test", "reason": "Allow precheck for resume."},
    )
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"

    resume_response = client.post(f"/api/v1/runs/{blocked_run_id}/resume")
    assert resume_response.status_code == 200
    resumed_run_id = resume_response.json()["id"]
    resumed_run = client.get(f"/api/v1/runs/{resumed_run_id}").json()
    assert resumed_run["status"] == "completed"

    consumed_approval = client.get(f"/api/v1/approvals?run_id={blocked_run_id}").json()["items"][0]
    assert resumed_run_id in consumed_approval["consumed_by_run_ids"]

    resumed_tool_calls = client.get(f"/api/v1/runs/{resumed_run_id}/tool-calls").json()["items"]
    assert any(
        item["tool_name"] == "shell.run" and item["status"] == "success"
        for item in resumed_tool_calls
    )

    consume_audit = client.get(
        f"/api/v1/audit-logs?resource_type=approval_request&resource_id={approval_id}&action=approval.consume"
    )
    assert consume_audit.status_code == 200
    assert consume_audit.json()["items"][0]["detail_json"]["consumed_by_run_id"] == resumed_run_id

    resume_jobs = client.get(f"/api/v1/jobs?kind=agent_run_resume&resource_id={resumed_run_id}")
    assert resume_jobs.status_code == 200
    assert resume_jobs.json()["items"][0]["status"] == "completed"


def test_cancelled_queued_run_and_event_stream() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Fix date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "Fix failing tests.",
        },
    )
    task_id = task_response.json()["id"]

    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={"agent_strategy_id": "repair_baseline_v1"},
    )
    run_id = run_response.json()["id"]
    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] in {"cancelled", "completed"}

    event_response = client.get(f"/api/v1/runs/{run_id}/events")
    assert event_response.status_code == 200
    assert "event: run.created" in event_response.text
    assert "event: run." in event_response.text


def test_cancelled_running_run_is_released_immediately() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Cancel stale running run",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "pytest",
            "goal": "Exercise immediate cancellation of a persisted running run.",
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={"agent_strategy_id": "repair_baseline_v1"},
    )
    run_id = run_response.json()["id"]
    store.update_run(run_id, status=RunStatus.RUNNING)

    cancel_response = client.post(f"/api/v1/runs/{run_id}/cancel")

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


def test_job_summary_cancel_and_retry_for_agent_run() -> None:
    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Retry failed run",
            "repo_path": "benchmarks/coding_golden_v1/task_004_force_failure/repo",
            "test_command": "python -m pytest -q",
            "goal": "Exercise job retry and summary endpoints.",
        },
    )
    task_id = task_response.json()["id"]

    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={"agent_strategy_id": "repair_baseline_v1"},
    )
    run_id = run_response.json()["id"]

    job_response = client.get(f"/api/v1/jobs?kind=agent_run&resource_id={run_id}")
    assert job_response.status_code == 200
    job = job_response.json()["items"][0]
    assert job["status"] == "failed"

    summary_response = client.get("/api/v1/jobs/summary")
    assert summary_response.status_code == 200
    assert summary_response.json()["queue"]["backend"] == "local_background"

    cancel_response = client.post(f"/api/v1/jobs/{job['id']}/cancel", json={"reason": "test"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "failed"

    retry_response = client.post(f"/api/v1/jobs/{job['id']}/retry")
    assert retry_response.status_code == 200
    retry_job = retry_response.json()["job"]
    retry_run = retry_response.json()["run"]
    assert retry_job["retry_of_job_id"] == job["id"]
    assert retry_job["parent_job_id"] == job["id"]
    assert retry_job["metadata"]["retry_count"] == 1
    assert retry_run["status"] == "queued"

    retry_jobs = client.get(f"/api/v1/jobs?kind=agent_run&resource_id={retry_run['id']}")
    assert retry_jobs.status_code == 200
    assert retry_jobs.json()["items"][0]["retry_of_job_id"] == job["id"]
    assert retry_jobs.json()["items"][0]["status"] == "failed"


def test_golden_catalog_comparison_release_gate_and_preference_export() -> None:
    first_seed = client.post("/api/v1/benchmarks/golden-tasks/seed")
    second_seed = client.post("/api/v1/benchmarks/golden-tasks/seed")
    assert first_seed.status_code == 200
    assert second_seed.status_code == 200
    assert first_seed.json()["total"] == 10
    assert [item["id"] for item in first_seed.json()["items"]] == [
        item["id"] for item in second_seed.json()["items"]
    ]
    task_ids = [item["id"] for item in first_seed.json()["items"][:2]]

    comparison_response = client.post(
        "/api/v1/evaluations/compare",
        json={
            "benchmark_name": "coding_golden_v1",
            "task_ids": task_ids,
            "agent_strategy_ids": ["repair_baseline_v1", "repair_with_critic_v3"],
            "baseline_strategy_id": "repair_baseline_v1",
        },
    )
    assert comparison_response.status_code == 200
    comparison = comparison_response.json()
    assert comparison["task_count"] == 2
    assert len(comparison["items"]) == 2
    assert comparison["winner"]["avg_score"] == 100
    assert "avg_tool_call_count" in comparison["items"][0]
    assert "failure_type_distribution" in comparison["items"][0]
    comparison_jobs = client.get("/api/v1/jobs?kind=strategy_comparison")
    assert comparison_jobs.status_code == 200
    assert any(
        item["result_json"].get("winner_strategy_id") == comparison["winner"]["agent_strategy_id"]
        for item in comparison_jobs.json()["items"]
    )
    strategy_memory = client.get(
        "/api/v1/memory/recommendations?memory_type=strategy&query=coding_golden_v1"
    )
    assert strategy_memory.status_code == 200
    assert any(
        item["item"]["key"] == "STRATEGY_COMPARISON_WINNER"
        for item in strategy_memory.json()["items"]
    )

    evaluation_id = comparison["items"][0]["evaluation_run_id"]
    gate_response = client.post(
        "/api/v1/evaluations/release-gate",
        json={"evaluation_run_id": evaluation_id},
    )
    assert gate_response.status_code == 200
    assert gate_response.json()["passed"] is True
    assert gate_response.json()["status"] == "active"
    assert gate_response.json()["release_stage"] == "active"
    assert gate_response.json()["auto_promoted"] is False
    assert gate_response.json()["job_id"]
    gate_job = client.get(f"/api/v1/jobs/{gate_response.json()['job_id']}")
    assert gate_job.status_code == 200
    assert gate_job.json()["kind"] == "release_gate"
    assert gate_job.json()["result_json"]["passed"] is True
    assert gate_job.json()["result_json"]["release_gate_status"] == "active"

    gate_history = client.get("/api/v1/evaluations/release-gates")
    assert gate_history.status_code == 200
    assert gate_history.json()["total"] >= 1

    critic_evaluation_id = next(
        item["evaluation_run_id"]
        for item in comparison["items"]
        if item["agent_strategy_id"] == "repair_with_critic_v3"
    )
    canary_gate_response = client.post(
        "/api/v1/evaluations/release-gate",
        json={
            "evaluation_run_id": critic_evaluation_id,
            "release_stage": "canary",
            "canary_percentage": 20,
        },
    )
    assert canary_gate_response.status_code == 200
    canary_gate = canary_gate_response.json()
    assert canary_gate["passed"] is True
    assert canary_gate["status"] == "canary"
    assert canary_gate["release_stage"] == "canary"
    assert canary_gate["canary_percentage"] == 20
    assert canary_gate["auto_promoted"] is False
    strategy_after_canary = client.get("/api/v1/strategies/repair_with_critic_v3")
    assert strategy_after_canary.status_code == 200
    assert strategy_after_canary.json()["status"] == "candidate"

    blocked_gate_response = client.post(
        "/api/v1/evaluations/release-gate",
        json={
            "evaluation_run_id": critic_evaluation_id,
            "min_avg_score": 101,
            "release_stage": "active",
        },
    )
    assert blocked_gate_response.status_code == 200
    assert blocked_gate_response.json()["passed"] is False
    assert blocked_gate_response.json()["status"] == "blocked"
    strategy_after_blocked = client.get("/api/v1/strategies/repair_with_critic_v3")
    assert strategy_after_blocked.status_code == 200
    assert strategy_after_blocked.json()["status"] == "candidate"

    strategy_release_history = client.get(
        "/api/v1/evaluations/release-gates/strategies/repair_with_critic_v3"
    )
    assert strategy_release_history.status_code == 200
    assert any(item["status"] == "canary" for item in strategy_release_history.json()["items"])
    release_actions = {item["action"] for item in strategy_release_history.json()["events"]}
    assert {"release_gate.stage", "release_gate.rollback"}.issubset(release_actions)

    evaluation_id = comparison["items"][0]["evaluation_run_id"]
    evaluation = client.get(f"/api/v1/evaluations/runs/{evaluation_id}")
    assert evaluation.status_code == 200

    benchmark_evaluation = client.post(
        "/api/v1/evaluations/runs",
        json={
            "benchmark_name": "coding_golden_v1",
            "task_ids": [task_ids[0]],
        },
    ).json()
    chosen_run_id = benchmark_evaluation["items"][0]["agent_run_id"]
    client.post(
        "/api/v1/policies",
        json={
            "id": "policy_no_patch",
            "name": "No Patch Policy",
            "allowed_tools": ["test.run", "git.diff", "report.write"],
            "blocked_commands": [],
            "max_steps": 20,
            "max_runtime_seconds": 600,
            "max_patch_files": 3,
            "max_changed_lines": 80,
            "network_enabled": False,
            "status": "active",
        },
    )
    rejected_task_response = client.post(
        f"/api/v1/tasks/{task_ids[0]}/runs",
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_no_patch",
        },
    )
    rejected_run_id = rejected_task_response.json()["id"]
    preference_response = client.post(
        "/api/v1/datasets/preference-pairs",
        json={
            "chosen_run_id": chosen_run_id,
            "rejected_run_id": rejected_run_id,
            "rationale": "Chosen run contains a complete successful repair trace.",
        },
    )
    assert preference_response.status_code == 200
    preference_audit = client.get(
        f"/api/v1/audit-logs?resource_type=preference_pair&resource_id={preference_response.json()['id']}"
    )
    assert preference_audit.status_code == 200
    assert any(item["action"] == "preference_pair.create" for item in preference_audit.json()["items"])
    same_run_preference = client.post(
        "/api/v1/datasets/preference-pairs",
        json={
            "chosen_run_id": chosen_run_id,
            "rejected_run_id": chosen_run_id,
        },
    )
    assert same_run_preference.status_code == 400
    assert same_run_preference.json()["detail"] == "PREFERENCE_RUNS_MUST_DIFFER"
    export_response = client.get("/api/v1/datasets/preference-pairs/export")
    assert export_response.status_code == 200
    assert preference_response.json()["id"] in export_response.text
    review_response = client.patch(
        f"/api/v1/datasets/preference-pairs/{preference_response.json()['id']}",
        json={"status": "approved"},
    )
    assert review_response.status_code == 200
    assert review_response.json()["status"] == "approved"


def test_memory_items_support_archive_lifecycle() -> None:
    create_response = client.post(
        "/api/v1/memory/items",
        json={
            "scope": "project",
            "memory_type": "project",
            "key": "ARCHIVE_ME",
            "summary": "待归档的项目规范，适用于 archive smoke query。",
        },
    )
    assert create_response.status_code == 200
    item_id = create_response.json()["id"]

    recommendation_response = client.get("/api/v1/memory/recommendations?query=归档 archive smoke")
    assert recommendation_response.status_code == 200
    assert any(
        item["item"]["id"] == item_id and item["score"] > 0
        for item in recommendation_response.json()["items"]
    )

    update_response = client.patch(
        f"/api/v1/memory/items/{item_id}",
        json={"status": "archived"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "archived"

    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=memory_item&resource_id={item_id}"
    )
    assert audit_response.status_code == 200
    assert any(item["action"] == "memory.update" for item in audit_response.json()["items"])


def test_memory_item_get_by_id_and_metadata_update() -> None:
    create_response = client.post(
        "/api/v1/memory/items",
        json={
            "scope": "project",
            "memory_type": "failure",
            "key": "memory_metadata_v1",
            "summary": "初始摘要。",
            "detail_json": {"root_cause": "initial"},
        },
    )
    assert create_response.status_code == 200
    item_id = create_response.json()["id"]

    get_response = client.get(f"/api/v1/memory/items/{item_id}")
    assert get_response.status_code == 200
    assert get_response.json()["key"] == "memory_metadata_v1"

    update_response = client.patch(
        f"/api/v1/memory/items/{item_id}",
        json={
            "scope": "strategy",
            "memory_type": "strategy",
            "key": "memory_metadata_v1_updated",
            "summary": "更新后的摘要。",
            "detail_json": {"root_cause": "updated", "fix": "apply_strategy"},
            "status": "archived",
        },
    )
    assert update_response.status_code == 200
    body = update_response.json()
    assert body["scope"] == "strategy"
    assert body["memory_type"] == "strategy"
    assert body["key"] == "memory_metadata_v1_updated"
    assert body["summary"] == "更新后的摘要。"
    assert body["status"] == "archived"


def test_memory_item_references_must_share_task() -> None:
    first_task = client.post(
        "/api/v1/tasks",
        json={"title": "记忆引用任务一", "goal": "建立记忆引用校验。"},
    ).json()
    second_task = client.post(
        "/api/v1/tasks",
        json={"title": "记忆引用任务二", "goal": "建立记忆引用校验。"},
    ).json()
    run = store.create_run(
        task_id=first_task["id"],
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="mock-coding-agent",
    )

    mismatch = client.post(
        "/api/v1/memory/items",
        json={
            "task_id": second_task["id"],
            "agent_run_id": run.id,
            "key": "mismatched_memory_ref",
            "summary": "不应写入跨任务引用。",
        },
    )
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "Memory references must belong to the same task"

    valid = client.post(
        "/api/v1/memory/items",
        json={
            "task_id": first_task["id"],
            "agent_run_id": run.id,
            "key": "valid_memory_ref",
            "summary": "运行和任务属于同一任务。",
        },
    )
    assert valid.status_code == 200
    memory_id = valid.json()["id"]

    update_mismatch = client.patch(
        f"/api/v1/memory/items/{memory_id}",
        json={"task_id": second_task["id"]},
    )
    assert update_mismatch.status_code == 400
    assert update_mismatch.json()["detail"] == "MEMORY_REFS_MUST_MATCH"


def test_list_endpoints_reject_invalid_pagination() -> None:
    for path in (
        "/api/v1/tasks?limit=0",
        "/api/v1/runs?offset=-1",
        "/api/v1/datasets/trace-items?limit=1001",
        "/api/v1/memory/items?limit=0",
        "/api/v1/audit-logs?offset=-1",
        "/api/v1/models?limit=0",
        "/api/v1/strategies?offset=-1",
        "/api/v1/evaluations/runs?limit=1001",
        "/api/v1/runs/missing/steps?limit=0",
    ):
        response = client.get(path)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_custom_strategy_runtime_config_changes_execution_trace() -> None:
    strategy_response = client.post(
        "/api/v1/strategies",
        json={
            "id": "repair_custom_no_retry_v1",
            "name": "自定义无重试策略",
            "task_type": "coding",
            "description": "用于验证运行时配置由策略实体驱动。",
            "planner_prompt": "创建计划。",
            "repair_prompt": "应用补丁。",
            "runtime_config": {"precheck": True, "retry": False, "critic": False},
            "status": "active",
        },
    )
    assert strategy_response.status_code == 200

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Custom strategy forced failure",
            "repo_path": "benchmarks/coding_golden_v1/task_004_force_failure/repo",
            "test_command": "python -m pytest -q",
            "goal": "Verify strategy runtime switches.",
        },
    )
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_custom_no_retry_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "mock-coding-agent",
        },
    )
    assert run_response.status_code == 200
    run_id = run_response.json()["id"]
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "TESTS_STILL_FAILING"
    steps = client.get(f"/api/v1/runs/{run_id}/steps").json()["items"]
    phases = [step["phase"] for step in steps]
    assert "precheck" in phases
    assert phases.count("analyze_failure") == 1


def test_custom_strategy_critic_thresholds_reject_run() -> None:
    strategy_response = client.post(
        "/api/v1/strategies",
        json={
            "id": "repair_strict_critic_thresholds_v1",
            "name": "严格阈值评审策略",
            "task_type": "coding",
            "description": "用于验证 Critic 阈值由策略配置驱动。",
            "planner_prompt": "创建严格评审计划。",
            "repair_prompt": "应用最小补丁。",
            "critic_prompt": "按策略阈值评审补丁风险。",
            "runtime_config": {
                "precheck": False,
                "retry": False,
                "critic": True,
                "model_gateway": False,
                "max_patch_files": 3,
                "max_changed_lines": 1,
                "allow_test_edits": False,
                "require_diff": True,
                "require_all_tests": True,
            },
            "status": "active",
        },
    )
    assert strategy_response.status_code == 200

    task_response = client.post(
        "/api/v1/tasks",
        json={
            "type": "coding",
            "title": "Strict critic date parser",
            "repo_path": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "test_command": "python -m pytest -q",
            "goal": "验证策略阈值会阻断过大的补丁。",
            "execution_config": {"task_kind": "date"},
        },
    )
    assert task_response.status_code == 200
    task_id = task_response.json()["id"]
    run_response = client.post(
        f"/api/v1/tasks/{task_id}/runs",
        json={
            "agent_strategy_id": "repair_strict_critic_thresholds_v1",
            "policy_version_id": "policy_default_v1",
        },
    )
    assert run_response.status_code == 200
    run = client.get(f"/api/v1/runs/{run_response.json()['id']}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "CRITIC_REJECTED"
    assert run["metrics"]["strategy_critic_enabled"] is True
    assert run["metrics"]["strategy_max_changed_lines"] == 1
    assert run["metrics"]["critic_review_score"] < 1

    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()["items"]
    critic = next(item for item in artifacts if item["name"] == "critic-review.json")
    review = json.loads(critic["content"])
    assert review["checks"]["changed_line_limit"] is False
    assert review["policy"]["max_changed_lines"] == 1


def test_strategy_upsert_overwrites_existing_configuration() -> None:
    strategy_id = "repair_strategy_form_v1"
    first_response = client.post(
        "/api/v1/strategies",
        json={
            "id": strategy_id,
            "name": "策略表单初始版",
            "task_type": "coding",
            "description": "初始版本。",
            "planner_prompt": "创建初始计划。",
            "repair_prompt": "生成初始补丁。",
            "runtime_config": {"precheck": True, "retry": False, "critic": False},
            "tool_selection_policy": {"allowed_tools": ["file.read"]},
            "max_steps": 8,
            "memory_enabled": False,
            "status": "draft",
        },
    )
    assert first_response.status_code == 200

    second_response = client.post(
        "/api/v1/strategies",
        json={
            "id": strategy_id,
            "name": "策略表单更新版",
            "task_type": "research",
            "description": "更新后的版本。",
            "planner_prompt": "创建更新计划。",
            "repair_prompt": "生成更新补丁。",
            "critic_prompt": "检查更新后的风险。",
            "runtime_config": {"precheck": False, "retry": True, "critic": True},
            "tool_selection_policy": {"allowed_tools": ["file.read", "file.write_patch"]},
            "max_steps": 18,
            "memory_enabled": True,
            "status": "candidate",
        },
    )
    assert second_response.status_code == 200

    strategy_response = client.get(f"/api/v1/strategies/{strategy_id}")
    assert strategy_response.status_code == 200
    strategy = strategy_response.json()
    assert strategy["name"] == "策略表单更新版"
    assert strategy["task_type"] == "research"
    assert strategy["description"] == "更新后的版本。"
    assert strategy["planner_prompt"] == "创建更新计划。"
    assert strategy["repair_prompt"] == "生成更新补丁。"
    assert strategy["critic_prompt"] == "检查更新后的风险。"
    assert strategy["runtime_config"]["retry"] is True
    assert strategy["tool_selection_policy"]["allowed_tools"] == ["file.read", "file.write_patch"]
    assert strategy["max_steps"] == 18
    assert strategy["memory_enabled"] is True
    assert strategy["status"] == "candidate"

    strategies = client.get("/api/v1/strategies").json()["items"]
    assert sum(1 for item in strategies if item["id"] == strategy_id) == 1
    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=agent_strategy&resource_id={strategy_id}&action=strategy.upsert"
    )
    assert audit_response.status_code == 200
    assert len(audit_response.json()["items"]) >= 2


def test_strategy_lifecycle_endpoints_are_audited() -> None:
    strategy_id = "repair_lifecycle_test_v1"
    strategy_response = client.post(
        "/api/v1/strategies",
        json={
            "id": strategy_id,
            "name": "生命周期测试策略",
            "task_type": "coding",
            "description": "用于验证策略候选、发布和回滚。",
            "planner_prompt": "创建计划。",
            "repair_prompt": "应用补丁。",
            "runtime_config": {"precheck": False, "retry": False, "critic": False},
            "status": "draft",
        },
    )
    assert strategy_response.status_code == 200

    candidate_response = client.post(
        f"/api/v1/strategies/{strategy_id}/candidate",
        json={"reason": "Ready for benchmark."},
    )
    assert candidate_response.status_code == 200
    assert candidate_response.json()["previous_status"] == "draft"
    assert candidate_response.json()["status"] == "candidate"
    assert candidate_response.json()["job_id"]

    promote_response = client.post(
        f"/api/v1/strategies/{strategy_id}/promote",
        json={"reason": "Manual promotion for smoke test."},
    )
    assert promote_response.status_code == 200
    assert promote_response.json()["previous_status"] == "candidate"
    assert promote_response.json()["status"] == "active"
    assert promote_response.json()["job_id"]

    rollback_response = client.post(
        f"/api/v1/strategies/{strategy_id}/rollback",
        json={"reason": "Rollback after validation."},
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["previous_status"] == "active"
    assert rollback_response.json()["status"] == "rolled_back"

    audit_response = client.get(
        f"/api/v1/audit-logs?resource_type=agent_strategy&resource_id={strategy_id}"
    )
    assert audit_response.status_code == 200
    actions = {item["action"] for item in audit_response.json()["items"]}
    assert {"strategy.candidate", "strategy.promote", "strategy.rollback"}.issubset(actions)
    lifecycle_jobs = client.get(
        f"/api/v1/jobs?kind=strategy_lifecycle&resource_id={strategy_id}"
    )
    assert lifecycle_jobs.status_code == 200
    lifecycle_actions = {
        item["result_json"].get("action")
        for item in lifecycle_jobs.json()["items"]
        if item["resource_id"] == strategy_id
    }
    assert {"strategy.candidate", "strategy.promote", "strategy.rollback"}.issubset(
        lifecycle_actions
    )


def test_standalone_evaluation_records_latest_baseline_regressions() -> None:
    seed_response = client.post("/api/v1/benchmarks/golden-tasks/seed")
    assert seed_response.status_code == 200
    task_id = seed_response.json()["items"][0]["id"]

    baseline_response = client.post(
        "/api/v1/evaluations/runs",
        json={
            "benchmark_name": "coding_golden_v1_regression_test",
            "task_ids": [task_id],
            "agent_strategy_id": "repair_baseline_v1",
        },
    )
    assert baseline_response.status_code == 200
    baseline = baseline_response.json()
    assert baseline["summary"]["regression_count"] == 0

    candidate_response = client.post(
        "/api/v1/evaluations/runs",
        json={
            "benchmark_name": "coding_golden_v1_regression_test",
            "task_ids": [task_id],
            "agent_strategy_id": "repair_with_critic_v3",
        },
    )
    assert candidate_response.status_code == 200
    candidate = candidate_response.json()
    assert candidate["summary"]["baseline_evaluation_id"] == baseline["id"]
    assert candidate["summary"]["regression_count"] == 0
    assert candidate["summary"]["score_delta_vs_baseline"] == round(
        candidate["summary"]["avg_score"] - baseline["summary"]["avg_score"],
        2,
    )

    gate_response = client.post(
        "/api/v1/evaluations/release-gate",
        json={"evaluation_run_id": candidate["id"], "min_avg_score": 100},
    )
    assert gate_response.status_code == 200
    gate = gate_response.json()
    assert gate["checks"]["avg_score"]["actual"] == 100
    assert gate["checks"]["avg_score"]["passed"] is True
    assert gate["checks"]["expected_alignment"]["actual"] == 1.0
    assert gate["checks"]["expected_alignment"]["passed"] is True
    assert "cost_growth_ratio" in gate_response.json()["checks"]

    expected_block_response = client.post(
        "/api/v1/evaluations/release-gate",
        json={"evaluation_run_id": candidate["id"], "min_expected_alignment": 1.01},
    )
    assert expected_block_response.status_code == 200
    expected_block = expected_block_response.json()
    assert expected_block["passed"] is False
    assert expected_block["status"] == "blocked"
    assert expected_block["checks"]["expected_alignment"]["passed"] is False


def test_platform_models_extensions_research_jobs_and_telemetry() -> None:
    session_response = client.get("/api/v1/auth/session")
    assert session_response.status_code == 200
    assert "approvals:decide" in session_response.json()["permissions"]

    repo_response = client.post(
        "/api/v1/integrations/repositories",
        json={
            "name": "Golden Tasks Local",
            "provider": "local",
            "local_path": "benchmarks/coding_golden_v1",
            "default_branch": "main",
        },
    )
    assert repo_response.status_code == 200
    repositories_response = client.get("/api/v1/integrations/repositories?provider=local")
    assert repositories_response.status_code == 200
    assert repositories_response.json()["total"] >= 1
    repository_id = repo_response.json()["id"]

    repository_health = client.get(f"/api/v1/integrations/repositories/{repository_id}/health")
    assert repository_health.status_code == 200
    assert repository_health.json()["path_exists"] is True

    repository_sync = client.post(f"/api/v1/integrations/repositories/{repository_id}/sync")
    assert repository_sync.status_code == 200
    assert repository_sync.json()["status"] in {"completed", "failed"}
    repository_sync_audit = client.get(
        f"/api/v1/audit-logs?resource_type=repository_connection"
        f"&resource_id={repository_id}&action=repository.sync"
    )
    assert repository_sync_audit.status_code == 200
    assert repository_sync_audit.json()["items"]

    remote_repo_response = client.post(
        "/api/v1/integrations/repositories",
        json={
            "name": "Golden Tasks Remote Mirror",
            "provider": "github",
            "url": "benchmarks/coding_golden_v1/task_001_date_parser/repo",
            "default_branch": "main",
        },
    )
    assert remote_repo_response.status_code == 200
    remote_repository_id = remote_repo_response.json()["id"]

    remote_repository_health = client.get(
        f"/api/v1/integrations/repositories/{remote_repository_id}/health"
    )
    assert remote_repository_health.status_code == 200
    assert remote_repository_health.json()["remote_reachable"] is True
    assert remote_repository_health.json()["status"] in {"configured", "healthy"}

    remote_repository_sync = client.post(
        f"/api/v1/integrations/repositories/{remote_repository_id}/sync"
    )
    assert remote_repository_sync.status_code == 200
    assert remote_repository_sync.json()["status"] == "completed"
    assert remote_repository_sync.json()["health"]["path_exists"] is True
    assert remote_repository_sync.json()["health"]["cache_path"]

    model_response = client.get("/api/v1/models?role=coding")
    assert model_response.status_code == 200
    assert model_response.json()["items"][0]["model_name"] == "mock-coding-agent"

    route_response = client.post(
        "/api/v1/models/route",
        json={"task_type": "research", "estimated_tokens": 10000},
    )
    assert route_response.status_code == 200
    assert route_response.json()["model_name"] == "mock-research-agent"

    invoke_response = client.post(
        "/api/v1/models/invoke",
        json={"task_type": "coding", "prompt": "请总结当前系统能力。"},
    )
    assert invoke_response.status_code == 200
    assert invoke_response.json()["fallback_used"] is True
    assert invoke_response.json()["attempts"] == 1
    assert invoke_response.json()["fallback_reason"] == "MOCK_PROVIDER"
    assert "模型" in invoke_response.json()["output_text"]
    model_jobs = client.get("/api/v1/jobs?kind=model_invocation")
    assert model_jobs.status_code == 200
    assert any(
        item["result_json"].get("model_name") == invoke_response.json()["model_name"]
        and item["result_json"].get("fallback_used") is True
        and item["result_json"].get("attempts") == 1
        for item in model_jobs.json()["items"]
    )

    extensions_response = client.get("/api/v1/extensions")
    assert extensions_response.status_code == 200
    assert {item["type"] for item in extensions_response.json()["items"]}.issuperset({"mcp_tool", "skill", "hook"})

    hook_response = client.post(
        "/api/v1/extensions/hooks/dispatch",
        json={"event_type": "run.completed", "payload": {"run_id": "run_test"}},
    )
    assert hook_response.status_code == 200
    assert hook_response.json()["delivered"] >= 1
    assert hook_response.json()["job_id"]
    assert hook_response.json()["record_id"]

    hook_history = client.get("/api/v1/extensions/hooks/dispatches?event_type=run.completed")
    assert hook_history.status_code == 200
    assert any(item["id"] == hook_response.json()["record_id"] for item in hook_history.json()["items"])

    hook_health = client.get("/api/v1/extensions/hook_run_finished/health")
    assert hook_health.status_code == 200
    assert hook_health.json()["healthy"] is True
    assert hook_health.json()["metadata"]["target"] == "audit_log"

    disabled_hook = client.patch(
        "/api/v1/extensions/hook_run_finished/status",
        json={"status": "disabled", "reason": "Temporarily paused for smoke test."},
    )
    assert disabled_hook.status_code == 200
    assert disabled_hook.json()["status"] == "disabled"
    disabled_health = client.get("/api/v1/extensions/hook_run_finished/health")
    assert disabled_health.status_code == 200
    assert disabled_health.json()["healthy"] is False
    assert disabled_health.json()["reason"] == "EXTENSION_DISABLED"
    skipped_hook = client.post(
        "/api/v1/extensions/hooks/dispatch",
        json={"event_type": "run.completed", "payload": {"run_id": "run_test_disabled"}},
    )
    assert skipped_hook.status_code == 200
    assert skipped_hook.json()["delivered"] == 0
    assert skipped_hook.json()["skipped"] == 0
    restored_hook = client.patch(
        "/api/v1/extensions/hook_run_finished/status",
        json={"status": "enabled", "reason": "Restore smoke fixture."},
    )
    assert restored_hook.status_code == 200
    extension_jobs = client.get("/api/v1/jobs?kind=extension_health")
    assert extension_jobs.status_code == 200
    assert any(item["resource_id"] == "hook_run_finished" for item in extension_jobs.json()["items"])

    extension_invoke_response = client.post(
        "/api/v1/extensions/mcp_local_git/invoke",
        json={"action": "inspect", "input": {"repo_path": "benchmarks/coding_golden_v1"}},
    )
    assert extension_invoke_response.status_code == 200
    extension_invoke = extension_invoke_response.json()
    assert extension_invoke["extension_id"] == "mcp_local_git"
    assert extension_invoke["status"] == "completed"
    assert extension_invoke["output"]["git_version"]
    assert extension_invoke["output"]["exit_code"] in {0, 128}
    assert extension_invoke["job_id"]

    extension_invoke_jobs = client.get("/api/v1/jobs?kind=extension_invoke")
    assert extension_invoke_jobs.status_code == 200
    assert any(
        item["result_json"].get("extension_id") == "mcp_local_git"
        and item["result_json"].get("action") == "inspect"
        for item in extension_invoke_jobs.json()["items"]
    )

    brief_response = client.post(
        "/api/v1/research/briefs",
        json={
            "question": "How should coding agents be evaluated?",
            "domain": "agent-eval",
            "max_papers": 3,
        },
    )
    assert brief_response.status_code == 200
    brief = brief_response.json()
    assert len(brief["papers"]) == 3
    assert len(brief["hypotheses"]) == 2
    assert len(brief["experiments"]) == 2
    assert brief["source_summary"]["provider"] == "local"
    assert brief["source_summary"]["model_assist"]["model_name"] == "mock-research-agent"
    assert brief["source_summary"]["model_assist"]["fallback_used"] is True
    brief_audit = client.get(
        f"/api/v1/audit-logs?resource_type=research_brief&resource_id={brief['id']}&action=model.invoke"
    )
    assert brief_audit.status_code == 200
    assert brief_audit.json()["items"][0]["decision"] == "fallback"

    evidence_response = client.post(
        "/api/v1/research/evidence",
        json={
            "title": "人工审核的可靠性证据",
            "domain": "agent-eval",
            "source_type": "manual",
            "content": "人工复核能够发现自动评分遗漏的行为风险。\n建议把失败样本与最终结论保持可追溯关联。",
            "authors": ["测试研究员"],
            "year": 2026,
        },
    )
    assert evidence_response.status_code == 200
    evidence = evidence_response.json()
    assert evidence["source"] == "manual"
    assert evidence["evidence_snippets"]
    assert evidence["source_metadata"]["source_type"] == "manual"
    assert evidence["source_metadata"]["content_char_count"] > 0
    assert evidence["source_metadata"]["snippet_count"] == len(evidence["evidence_snippets"])
    assert evidence["source_metadata"]["job_id"]

    evidence_jobs_response = client.get("/api/v1/jobs?kind=research_evidence_import")
    assert evidence_jobs_response.status_code == 200
    assert any(
        item["result_json"].get("paper_id") == evidence["id"]
        and item["result_json"].get("snippet_count") == len(evidence["evidence_snippets"])
        for item in evidence_jobs_response.json()["items"]
    )

    evidence_list_response = client.get("/api/v1/research/evidence?domain=agent-eval")
    assert evidence_list_response.status_code == 200
    assert any(item["id"] == evidence["id"] for item in evidence_list_response.json()["items"])

    attach_response = client.post(
        f"/api/v1/research/briefs/{brief['id']}/evidence",
        json={"paper_ids": [evidence["id"]]},
    )
    assert attach_response.status_code == 200
    attached_brief = attach_response.json()
    attached_citation = next(
        item for item in attached_brief["citations"] if item["paper_id"] == evidence["id"]
    )
    assert attached_citation["status"] == "pending_review"

    review_response = client.post(
        f"/api/v1/research/briefs/{brief['id']}/citations/review",
        json={
            "paper_id": evidence["id"],
            "status": "approved",
            "note": "证据来源和摘要已核对。",
            "reviewer_id": "smoke-reviewer",
        },
    )
    assert review_response.status_code == 200
    reviewed_brief = review_response.json()
    reviewed_citation = next(
        item for item in reviewed_brief["citations"] if item["paper_id"] == evidence["id"]
    )
    assert reviewed_citation["status"] == "approved"
    assert reviewed_brief["citation_reviews"][-1]["reviewer_id"] == "smoke-reviewer"

    search_response = client.post(
        "/api/v1/research/search",
        json={
            "question": "coding agent evaluation",
            "domain": "agent-eval",
            "max_papers": 2,
        },
    )
    assert search_response.status_code == 200
    assert search_response.json()["total"] == 2

    report_response = client.get(f"/api/v1/research/briefs/{brief['id']}/report")
    assert report_response.status_code == 200
    assert "自动研究简报" in report_response.text
    assert "引用审核" in report_response.text
    assert "已通过" in report_response.text

    notebook_response = client.post(f"/api/v1/research/briefs/{brief['id']}/notebook-runs")
    assert notebook_response.status_code == 200
    notebook = notebook_response.json()
    assert notebook["metrics"]["paper_count"] == 4
    assert notebook["metrics"]["model_assist"] is True
    assert notebook["metrics"]["execution_status"] == "completed"
    assert notebook["metrics"]["execution_exit_code"] == 0
    assert notebook["cells"][3]["source"] == "run_reproducible_experiment()"
    assert notebook["cells"][3]["status"] == "completed"
    assert notebook["cells"][-1]["source"] == "## 模型研究复核"
    assert "模型" in notebook["cells"][-1]["output"]
    notebook_job = client.get(f"/api/v1/jobs/{notebook['job_id']}")
    assert notebook_job.status_code == 200
    assert notebook_job.json()["kind"] == "notebook_run"
    assert notebook_job.json()["status"] == "completed"
    assert notebook_job.json()["result_json"]["notebook_run_id"] == notebook["id"]
    notebook_audit = client.get(
        f"/api/v1/audit-logs?resource_type=notebook_run&resource_id={notebook['id']}&action=model.invoke"
    )
    assert notebook_audit.status_code == 200
    assert notebook_audit.json()["items"][0]["detail_json"]["phase"] == "notebook_review"

    research_benchmark = client.post("/api/v1/research/benchmarks")
    assert research_benchmark.status_code == 200
    assert research_benchmark.json()["summary"]["brief_count"] >= 1
    benchmark_item = next(
        item for item in research_benchmark.json()["items"] if item["brief_id"] == brief["id"]
    )
    assert benchmark_item["citation_count"] == 4
    assert benchmark_item["approved_citation_count"] == 1
    assert benchmark_item["citation_review_rate"] == 0.25

    research_validation = client.get("/api/v1/research/benchmarks/validation")
    assert research_validation.status_code == 200
    assert research_validation.json()["all_valid"] is True
    assert research_validation.json()["task_count"] == 10

    research_acceptance = client.post(
        "/api/v1/research/benchmarks/acceptance",
        json={"limit": 2, "max_papers": 5},
    )
    assert research_acceptance.status_code == 200
    acceptance = research_acceptance.json()["acceptance"]
    assert acceptance["task_count"] == 2
    assert acceptance["passed_count"] == 2
    assert acceptance["all_passed"] is True
    assert acceptance["avg_score"] == 85.0
    assert acceptance["report_path"].endswith(f"{acceptance['job_id']}/report")
    research_job = client.get(f"/api/v1/jobs/{acceptance['job_id']}")
    assert research_job.status_code == 200
    assert research_job.json()["kind"] == "research_acceptance"
    assert research_job.json()["result_json"]["passed_count"] == 2
    research_report = client.get(
        f"/api/v1/research/benchmarks/acceptance/{acceptance['job_id']}/report"
    )
    assert research_report.status_code == 200
    assert "研究验收报告" in research_report.text
    assert "任务明细" in research_report.text
    assert "research_001" in research_report.text

    jobs_response = client.get("/api/v1/jobs")
    assert jobs_response.status_code == 200
    assert "total" in jobs_response.json()

    job_summary = client.get("/api/v1/jobs/summary")
    assert job_summary.status_code == 200
    assert job_summary.json()["queue"]["backend"] == "local_background"
    assert job_summary.json()["queue"]["connected"] is True

    telemetry_response = client.get("/api/v1/telemetry/summary")
    assert telemetry_response.status_code == 200
    telemetry = telemetry_response.json()
    assert telemetry["research_brief_count"] >= 1
    assert telemetry["research_evidence_count"] >= 1
    assert telemetry["notebook_run_count"] >= 1
    assert telemetry["notebook_execution_failure_count"] == 0
    assert telemetry["model_invocation_count"] >= 1
    assert telemetry["model_fallback_count"] >= 1
    assert telemetry["completed_job_count"] >= 1

    metrics_response = client.get("/api/v1/telemetry/metrics")
    assert metrics_response.status_code == 200
    assert "researchforge_runs_total" in metrics_response.text
    assert "researchforge_model_invocations_total" in metrics_response.text
    assert "researchforge_notebook_execution_failures_total" in metrics_response.text
    assert "researchforge_jobs_completed_total" in metrics_response.text

    sandbox_response = client.get("/api/v1/sandbox/status")
    assert sandbox_response.status_code == 200
    assert sandbox_response.json()["isolation"] in {"local-tempdir", "docker"}
    assert "limits" in sandbox_response.json()
    assert "network" in sandbox_response.json()

    sandbox_check = client.get("/api/v1/sandbox/check")
    assert sandbox_check.status_code == 200
    assert "ready" in sandbox_check.json()
    assert "limits" in sandbox_check.json()

    storage_response = client.get("/api/v1/system/storage")
    assert storage_response.status_code == 200
    storage = storage_response.json()
    assert "backend" in storage
    assert "artifact_store_backend" in storage
    assert "artifact_store_persistent" in storage
    assert "artifact_store_prefix" in storage
    assert "artifact_store_ready" in storage
    assert "artifact_store_message" in storage
    assert "postgres_migrations" in storage

    readiness_response = client.get("/api/v1/system/readiness")
    assert readiness_response.status_code == 200
    readiness = readiness_response.json()
    assert readiness["total"] >= 20
    assert readiness["passed"] >= 19
    assert any(item["id"] == "golden_tasks" and item["passed"] for item in readiness["checks"])
    readiness_ids = {item["id"] for item in readiness["checks"]}
    assert {
        "model_catalog",
        "model_route",
        "sandbox_ready",
        "approval_resume",
        "extension_governance",
        "evaluation_report",
        "model_fallback_diagnostics",
        "release_history",
        "artifact_blob_store",
        "postgres_migrations",
    }.issubset(readiness_ids)

    production_readiness_response = client.get("/api/v1/system/production-readiness")
    assert production_readiness_response.status_code == 200
    production_readiness = production_readiness_response.json()
    assert production_readiness["profile"] == "production"
    assert production_readiness["verified_dependencies"] is False
    assert production_readiness["status"] == "not_ready"
    production_readiness_ids = {item["id"] for item in production_readiness["checks"]}
    assert {
        "production_auth",
        "agent_runtime",
        "postgres_store",
        "isolated_sandbox",
        "external_artifacts",
        "real_model",
        "event_bus",
        "github_oauth_redirect",
        "metrics_auth",
        "artifact_encryption",
    }.issubset(production_readiness_ids)
    next_actions = {item["check_id"]: item for item in production_readiness["next_actions"]}
    assert "production_auth" in next_actions
    assert "RESEARCHFORGE_AUTH_MODE=production" in next_actions["production_auth"]["configuration"]
    assert "real_model" in next_actions


def test_research_evidence_file_import_validates_source(tmp_path) -> None:
    source_file = tmp_path / "agent-evidence.txt"
    source_file.write_text(
        "文件证据标题\n本地证据导入应保留来源元数据。\n人工审核可以追踪导入任务。",
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/research/evidence",
        json={
            "domain": "agent-eval",
            "source_type": "note",
            "file_path": str(source_file),
            "authors": ["file-reviewer"],
        },
    )
    assert response.status_code == 200
    paper = response.json()
    assert paper["title"] == "文件证据标题"
    assert paper["abstract"].startswith("文件证据标题")
    assert paper["source_metadata"]["file_name"] == "agent-evidence.txt"
    assert paper["source_metadata"]["file_size_bytes"] == source_file.stat().st_size
    assert paper["source_metadata"]["job_id"]

    binary_file = tmp_path / "unsafe.bin"
    binary_file.write_bytes(b"research\x00evidence")
    bad_response = client.post(
        "/api/v1/research/evidence",
        json={
            "domain": "agent-eval",
            "source_type": "note",
            "file_path": str(binary_file),
        },
    )
    assert bad_response.status_code == 400
    assert bad_response.json()["detail"] == "RESEARCH_SOURCE_BINARY"

    failed_jobs_response = client.get("/api/v1/jobs?kind=research_evidence_import&status=failed")
    assert failed_jobs_response.status_code == 200
    assert any(
        item["resource_id"] == str(binary_file)
        and item["error_summary"] == "RESEARCH_SOURCE_BINARY"
        for item in failed_jobs_response.json()["items"]
    )
