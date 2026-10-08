import asyncio
from collections.abc import Callable
from uuid import uuid4

import pytest

from app.api.evaluations import _execute_evaluation_job
from app.domain.schemas import EvaluationRunResponse, RunStatus
from app.infra.idgen import id_generator
from app.infra.store import store
from app.main import app
from app.services.job_queue import job_queue
from fastapi.testclient import TestClient


client = TestClient(app)


@pytest.fixture
def redis_enqueue_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[object, ...]]]:
    calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(job_queue, "backend", "redis")

    async def enqueue(
        _background_tasks,
        handler: Callable,
        *args: object,
    ) -> None:
        calls.append((f"{handler.__module__}:{handler.__qualname__}", args))

    monkeypatch.setattr(job_queue, "enqueue", enqueue)
    return calls


def test_golden_acceptance_returns_queued_job_in_redis_mode(
    redis_enqueue_spy: list[tuple[str, tuple[object, ...]]],
) -> None:
    response = client.post(
        "/api/v1/benchmarks/golden-tasks/acceptance",
        json={"limit": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["acceptance"]["job_id"] == payload["job_id"]
    assert redis_enqueue_spy[0][0] == "app.api.benchmarks:_execute_golden_acceptance_job"
    assert redis_enqueue_spy[0][1][0] == payload["job_id"]

    duplicate = client.post(
        "/api/v1/benchmarks/golden-tasks/acceptance",
        json={"limit": 1},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "queued"
    assert duplicate.json()["job_id"] == payload["job_id"]
    assert len(redis_enqueue_spy) == 1

    fresh = client.post(
        "/api/v1/benchmarks/golden-tasks/acceptance",
        json={"limit": 1, "acceptance_id": "fresh-test-batch"},
    )
    assert fresh.status_code == 200
    assert fresh.json()["job_id"] != payload["job_id"]
    assert len(redis_enqueue_spy) == 2


def test_strategy_comparison_returns_queued_job_in_redis_mode(
    redis_enqueue_spy: list[tuple[str, tuple[object, ...]]],
) -> None:
    response = client.post("/api/v1/evaluations/compare", json={"task_ids": []})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["job_id"]
    assert redis_enqueue_spy[0][0] == "app.api.evaluations:_execute_strategy_comparison_job"


def test_evaluation_returns_queued_record_with_stable_id_in_redis_mode(
    redis_enqueue_spy: list[tuple[str, tuple[object, ...]]],
) -> None:
    response = client.post(
        "/api/v1/evaluations/runs",
        json={
            "benchmark_name": "coding_golden_v1",
            "task_ids": ["task_missing_for_async_test"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["job_id"]
    assert payload["evaluation"]["id"]
    evaluation = client.get(f"/api/v1/evaluations/runs/{payload['evaluation']['id']}")
    assert evaluation.status_code == 200
    assert evaluation.json()["status"] == "queued"
    assert evaluation.json()["job_id"] == payload["job_id"]
    assert redis_enqueue_spy[0][0] == "app.api.evaluations:_execute_evaluation_job"
    assert redis_enqueue_spy[0][1][1] == payload["evaluation"]["id"]


def test_research_acceptance_returns_queued_job_in_redis_mode(
    redis_enqueue_spy: list[tuple[str, tuple[object, ...]]],
) -> None:
    response = client.post(
        "/api/v1/research/benchmarks/acceptance",
        json={"limit": 1, "max_papers": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["acceptance"]["job_id"]
    assert redis_enqueue_spy[0][0] == (
        "app.api.research:_execute_research_benchmark_acceptance_job"
    )


def test_notebook_run_returns_queued_job_in_redis_mode(
    redis_enqueue_spy: list[tuple[str, tuple[object, ...]]],
) -> None:
    brief_response = client.post(
        "/api/v1/research/briefs",
        json={"question": "如何验证一个可复现实验？", "domain": "ai"},
    )
    assert brief_response.status_code == 200

    response = client.post(
        f"/api/v1/research/briefs/{brief_response.json()['id']}/notebook-runs",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["brief_id"] == brief_response.json()["id"]
    assert redis_enqueue_spy[0][0] == "app.api.research:_execute_notebook_run_job"


def test_cancelled_evaluation_job_can_be_retried_in_redis_mode(
    redis_enqueue_spy: list[tuple[str, tuple[object, ...]]],
) -> None:
    response = client.post(
        "/api/v1/evaluations/runs",
        json={
            "benchmark_name": "coding_golden_v1",
            "task_ids": ["task_missing_for_retry_test"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    job_id = payload["job_id"]
    evaluation_id = payload["evaluation"]["id"]

    cancel_response = client.post(f"/api/v1/jobs/{job_id}/cancel", json={"reason": "test"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    cancelled_evaluation = client.get(f"/api/v1/evaluations/runs/{evaluation_id}")
    assert cancelled_evaluation.status_code == 200
    assert cancelled_evaluation.json()["status"] == "cancelled"

    retry_response = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retry_response.status_code == 200
    retry_payload = retry_response.json()
    assert retry_payload["job"]["kind"] == "evaluation"
    assert retry_payload["job"]["retry_of_job_id"] == job_id
    assert retry_payload["evaluation"]["status"] == "queued"
    assert retry_payload["evaluation"]["id"] != evaluation_id
    assert redis_enqueue_spy[-1][0] == "app.api.evaluations:_execute_evaluation_job"
    assert redis_enqueue_spy[-1][1][0] == retry_payload["job"]["id"]
    assert redis_enqueue_spy[-1][1][1] == retry_payload["evaluation"]["id"]


def test_running_coding_acceptance_job_can_be_cancelled() -> None:
    job = store.create_job(
        kind="coding_acceptance",
        resource_id="coding_golden_v1",
        metadata={"request": {"benchmark_name": "coding_golden_v1"}},
    )
    store.update_job(job.id, "running")

    response = client.post(
        f"/api/v1/jobs/{job.id}/cancel",
        json={"reason": "operator stopped stale acceptance"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["error_summary"] == "operator stopped stale acceptance"


def test_evaluation_cancel_race_persists_cancelled_result() -> None:
    evaluation_id = id_generator.next("eval")
    request_data = {"benchmark_name": "coding_golden_v1", "task_ids": []}
    job = store.create_job(
        kind="evaluation",
        resource_id=evaluation_id,
        metadata={"request": request_data, "evaluation_id": evaluation_id},
    )
    placeholder = EvaluationRunResponse(
        id=evaluation_id,
        benchmark_name="coding_golden_v1",
        policy_version_id="policy_default_v1",
        agent_strategy_id="repair_baseline_v1",
        model_name="mock-coding-agent",
        status=RunStatus.QUEUED,
        job_id=job.id,
        summary={"status": "queued", "job_id": job.id},
    )
    store.add_evaluation_run(placeholder)
    store.request_job_cancel(job.id, "test")

    result = asyncio.run(_execute_evaluation_job(job.id, evaluation_id, request_data))

    assert result.status == RunStatus.CANCELLED
    stored = store.get_evaluation_run(evaluation_id)
    assert stored is not None
    assert stored.status == RunStatus.CANCELLED
    assert stored.summary["status"] == "cancelled"
    assert stored.summary["error"] == "CANCELLED_BY_OPERATOR"
    assert store.jobs[job.id].status == "cancelled"
    assert store.jobs[job.id].cancel_requested is False


def test_non_admin_job_list_includes_resource_jobs_by_metadata_workspace() -> None:
    suffix = uuid4().hex[:8]
    workspace_response = client.post(
        "/api/v1/workspaces",
        json={"name": f"资源 Job 工作区 {suffix}", "owner_id": "user_admin"},
    )
    assert workspace_response.status_code == 200
    workspace_id = workspace_response.json()["id"]
    user_response = client.post(
        "/api/v1/users",
        json={
            "workspace_id": workspace_id,
            "email": f"resource-job-{suffix}@researchforge.local",
            "name": "资源 Job 操作员",
            "role": "operator",
            "status": "active",
        },
    )
    assert user_response.status_code == 200
    headers = {"x-user-id": user_response.json()["id"]}
    own_job = store.create_job(
        kind="research_acceptance",
        resource_id="research_brief_v1",
        metadata={"workspace_id": workspace_id},
    )
    foreign_job = store.create_job(
        kind="research_acceptance",
        resource_id="research_brief_v1",
        metadata={"workspace_id": "workspace_default"},
    )

    listed = client.get("/api/v1/jobs", headers=headers, params={"limit": 1000})

    assert listed.status_code == 200
    listed_ids = {item["id"] for item in listed.json()["items"]}
    assert own_job.id in listed_ids
    assert foreign_job.id not in listed_ids

