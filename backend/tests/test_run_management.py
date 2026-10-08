from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import runs
from app.domain.schemas import CreateTaskRequest, CreateWorkspaceRequest, RunStatus
from app.infra.store import store
from app.main import app


client = TestClient(app)


def _completed_run(task_id: str, model_name: str, cost: float, duration_ms: int):
    run = store.create_run(
        task_id=task_id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name=model_name,
    )
    run.model_name = model_name
    run.duration_ms = duration_ms
    store.runs[run.id] = run
    return store.update_run(
        run.id,
        status=RunStatus.COMPLETED,
        total_tokens=120,
        total_cost=cost,
        tool_call_count=4,
        metrics={"tests_total": 2, "tests_passed": 2},
    )


def test_run_management_filters_summary_comparison_and_retry(monkeypatch) -> None:
    workspace_id = f"run_management_{uuid4().hex}"
    store.create_workspace(
        CreateWorkspaceRequest(
            id=workspace_id,
            name="运行管理测试工作区",
            max_active_runs=1_000,
        )
    )
    task = store.create_task(
        CreateTaskRequest(
            title="运行管理验收任务",
            goal="验证项目筛选、汇总和复跑。",
            repo_path="/tmp/run-management-project",
            workspace_id=workspace_id,
        )
    )
    first = _completed_run(task.id, "qwen-plus", 0.01, 5000)
    second = _completed_run(task.id, "qwen-max", 0.02, 8000)

    listed = client.get(
        "/api/v1/runs",
        params={"project_name": "run-management-project", "model_name": "qwen-plus"},
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert [item["id"] for item in payload["items"]] == [first.id]
    assert payload["facets"]["projects"] == ["run-management-project"]
    assert payload["summary"]["total_runs"] == 1
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert payload["has_more"] is False

    summary = client.get("/api/v1/run-summary", params={"project_name": "run-management-project"})
    assert summary.status_code == 200
    assert summary.json()["summary"]["total_runs"] == 2
    assert summary.json()["summary"]["projects"][0]["project_name"] == "run-management-project"

    filtered_summary = client.get(
        "/api/v1/run-summary",
        params={"task_id": task.id, "status": "completed", "query": "qwen-plus"},
    )
    assert filtered_summary.status_code == 200
    assert filtered_summary.json()["summary"]["total_runs"] == 1

    comparison = client.get(
        "/api/v1/run-comparison",
        params={"left_run_id": first.id, "right_run_id": second.id},
    )
    assert comparison.status_code == 200
    assert comparison.json()["same_task"] is True
    assert comparison.json()["delta"]["total_cost"] == 0.01

    async def skip_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(runs.job_queue, "enqueue", skip_enqueue)
    retry = client.post(
        f"/api/v1/runs/{first.id}/retry",
        json={"model_name": "qwen-max"},
    )
    assert retry.status_code == 200
    assert retry.json()["id"] != first.id
    assert retry.json()["model_name"]
    assert retry.json()["status"] == "queued"


def test_start_run_idempotency_key_reuses_the_existing_run(monkeypatch) -> None:
    workspace_id = f"idempotency_{uuid4().hex}"
    store.create_workspace(
        CreateWorkspaceRequest(
            id=workspace_id,
            name="幂等启动测试工作区",
            max_active_runs=10,
        )
    )
    task = store.create_task(
        CreateTaskRequest(
            title="幂等启动测试",
            goal="重复点击只创建一次运行。",
            repo_path="/tmp/idempotent-project",
            workspace_id=workspace_id,
        )
    )

    enqueued = []

    async def skip_enqueue(*args, **_kwargs) -> None:
        enqueued.append(args)

    monkeypatch.setattr(runs.job_queue, "enqueue", skip_enqueue)
    headers = {"Idempotency-Key": "run-start-idempotency-1"}
    first = client.post(
        f"/api/v1/tasks/{task.id}/runs",
        headers=headers,
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "qwen-plus",
        },
    )
    second = client.post(
        f"/api/v1/tasks/{task.id}/runs",
        headers=headers,
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "qwen-plus",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(store.list_jobs(kind="agent_run", resource_id=first.json()["id"])) == 1
    assert len(enqueued) == 1

    invalid = client.post(
        f"/api/v1/tasks/{task.id}/runs",
        headers={"Idempotency-Key": "bad key"},
        json={
            "agent_strategy_id": "repair_baseline_v1",
            "policy_version_id": "policy_default_v1",
            "model_name": "qwen-plus",
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "INVALID_IDEMPOTENCY_KEY"


def test_run_management_filters_by_branch_and_exposes_branch_facet() -> None:
    workspace_id = f"run_branch_{uuid4().hex}"
    store.create_workspace(
        CreateWorkspaceRequest(
            id=workspace_id,
            name="运行分支筛选测试工作区",
            max_active_runs=1_000,
        )
    )
    tasks = []
    for branch in ("main", "researchforge/fix-tax"):
        tasks.append(
            store.create_task(
                CreateTaskRequest(
                    title=f"分支筛选-{branch}",
                    goal="验证运行记录按分支筛选。",
                    repo_path="/tmp/shared-repository",
                    workspace_id=workspace_id,
                    execution_config={"branch": branch},
                )
            )
        )
    main_run = _completed_run(tasks[0].id, "qwen-plus", 0.01, 1000)
    fix_run = _completed_run(tasks[1].id, "qwen-plus", 0.02, 2000)

    listed = client.get(
        "/api/v1/runs",
        params={"workspace_id": workspace_id, "branch": "researchforge/fix-tax"},
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert [item["id"] for item in payload["items"]] == [fix_run.id]
    assert payload["facets"]["branches"] == ["researchforge/fix-tax"]
    assert payload["items"][0]["branch"] == "researchforge/fix-tax"
    assert main_run.id not in {item["id"] for item in payload["items"]}

    summary = client.get(
        "/api/v1/run-summary",
        params={"workspace_id": workspace_id, "branch": "main"},
    )
    assert summary.status_code == 200
    assert summary.json()["summary"]["total_runs"] == 1

    exported = client.get(
        "/api/v1/run-records/export.csv",
        params={"workspace_id": workspace_id, "branch": "researchforge/fix-tax"},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "researchforge-run-records.csv" in exported.headers["content-disposition"]
    csv_text = exported.content.decode("utf-8-sig")
    assert "repository_url" in csv_text.splitlines()[0]
    assert "researchforge/fix-tax" in csv_text
    assert "main" not in csv_text


def test_batch_cancel_and_retry_return_item_level_results(monkeypatch) -> None:
    workspace_id = f"run_batch_{uuid4().hex}"
    store.create_workspace(
        CreateWorkspaceRequest(
            id=workspace_id,
            name="运行批量操作测试工作区",
            max_active_runs=1_000,
        )
    )
    task = store.create_task(
        CreateTaskRequest(
            title="批量运行操作测试",
            goal="验证批量取消和重试。",
            repo_path="/tmp/batch-project",
            workspace_id=workspace_id,
        )
    )
    queued = store.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="qwen-plus",
    )
    cancelled = client.post(
        "/api/v1/runs/batch-cancel",
        json={"run_ids": [queued.id, queued.id]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["requested"] == 1
    assert cancelled.json()["changed"] == 1
    assert cancelled.json()["items"][0]["status"] == "cancelled"

    active = store.create_run(
        task_id=task.id,
        agent_strategy_id="repair_baseline_v1",
        policy_version_id="policy_default_v1",
        model_name="qwen-plus",
    )
    completed = _completed_run(task.id, "qwen-plus", 0.01, 1000)
    enqueued = []

    async def skip_enqueue(*args, **_kwargs) -> None:
        enqueued.append(args)

    monkeypatch.setattr(runs.job_queue, "enqueue", skip_enqueue)
    retried = client.post(
        "/api/v1/runs/batch-retry",
        json={"run_ids": [completed.id, active.id]},
    )
    assert retried.status_code == 200
    payload = retried.json()
    assert payload["requested"] == 2
    assert payload["queued"] == 1
    assert payload["items"][0]["status"] == "queued"
    assert payload["items"][0]["retry_run_id"] != completed.id
    assert payload["items"][1]["status"] == "skipped"
    assert payload["items"][1]["error"] == "ACTIVE_RUN_CANNOT_RETRY"
    assert len(enqueued) == 1
