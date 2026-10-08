from fastapi import APIRouter, Query
from app.agent.runtime import AgentRuntime
from app.benchmarks.catalog import list_golden_tasks
from app.domain.schemas import CreateEvaluationRunRequest, GoldenAcceptanceRequest
from app.evals.benchmark_runner import BenchmarkRunner
from app.infra.store import store
from app.services.job_queue import job_queue

router = APIRouter(tags=["benchmarks"])
benchmark_runner = BenchmarkRunner(store=store, runtime=AgentRuntime(store=store))


def _acceptance_request_payload(request: GoldenAcceptanceRequest) -> dict[str, object]:
    # Keep the default request shape stable across upgrades.  A fresh batch is
    # explicit through acceptance_id; null optional fields must not invalidate
    # idempotent reuse of a completed historical acceptance.
    return request.model_dump(mode="json", exclude_none=True)


def _find_reusable_acceptance_job(
    request: GoldenAcceptanceRequest,
):
    request_payload = _acceptance_request_payload(request)
    candidates = store.read_jobs(
        kind="coding_acceptance",
        resource_id=request.benchmark_name,
    )
    reusable = [
        job
        for job in candidates
        if job.status in {"queued", "running", "completed"}
        and job.metadata.get("request") == request_payload
    ]
    if not reusable:
        return None
    return max(reusable, key=lambda item: item.created_at)


def _reused_acceptance_response(job) -> dict[str, object]:
    """Return a poll-compatible response when a client retries the same batch."""
    if job.status != "completed":
        return {
            "status": "queued",
            "job_id": job.id,
            "job": job,
            "acceptance": {
                "job_id": job.id,
                "report_path": f"/api/v1/jobs/{job.id}",
                "deduplicated": True,
            },
        }

    result_json = dict(job.result_json or {})
    evaluation_id = result_json.get("evaluation_run_id")
    evaluation = store.get_evaluation_run(str(evaluation_id)) if evaluation_id else None
    acceptance = {
        **result_json,
        "job_id": job.id,
        "evaluation_id": evaluation_id,
        "deduplicated": True,
    }
    return {
        "status": "completed",
        "job_id": job.id,
        "job": job,
        "evaluation": evaluation,
        "acceptance": acceptance,
    }


@router.get("/benchmarks/golden-tasks")
async def get_golden_tasks(
    benchmark_name: str = "coding_golden_v1",
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_tasks = [task.as_dict() for task in list_golden_tasks(benchmark_name)]
    tasks = all_tasks[offset : offset + limit]
    return {"items": tasks, "total": len(all_tasks)}


@router.post("/benchmarks/golden-tasks/seed")
async def seed_golden_tasks(benchmark_name: str = "coding_golden_v1") -> dict[str, object]:
    seeded = []
    for golden in list_golden_tasks(benchmark_name):
        existing = next(
            (
                task
                for task in store.tasks.values()
                if task.title == golden.title and task.repo_path == golden.repo_path
            ),
            None,
        )
        if existing is None:
            seeded.append(store.create_task(golden.to_task_request()))
        else:
            seeded.append(store.update_task(golden.sync_task(existing)))
    return {"items": seeded, "total": len(seeded)}


@router.post("/benchmarks/golden-tasks/acceptance")
async def run_golden_acceptance(
    request: GoldenAcceptanceRequest,
) -> dict[str, object]:
    """Run a reproducible batch acceptance over the discovered Golden Tasks."""
    reusable_job = _find_reusable_acceptance_job(request)
    if reusable_job is not None:
        return _reused_acceptance_response(reusable_job)

    job = store.create_job(
        kind="coding_acceptance",
        resource_id=request.benchmark_name,
        metadata={
            "agent_strategy_id": request.agent_strategy_id,
            "policy_version_id": request.policy_version_id,
            "model_name": request.model_name,
            "limit": request.limit,
            "request": _acceptance_request_payload(request),
        },
    )
    if job_queue.backend == "redis":
        try:
            await job_queue.enqueue(
                None,
                _execute_golden_acceptance_job,
                job.id,
                request.model_dump(mode="json"),
            )
        except Exception as exc:
            failed_job = store.update_job(job.id, "failed", str(exc))
            return {
                "status": "failed",
                "error": str(exc),
                "job_id": job.id,
                "job": failed_job or job,
            }
        return {
            "status": "queued",
            "job_id": job.id,
            "acceptance": {
                "job_id": job.id,
                "report_path": f"/api/v1/jobs/{job.id}",
            },
        }
    return await _execute_golden_acceptance_job(
        job.id,
        request.model_dump(mode="json"),
    )


async def _execute_golden_acceptance_job(
    job_id: str,
    request_data: dict[str, object],
) -> dict[str, object]:
    request = GoldenAcceptanceRequest.model_validate(request_data)
    job = store.jobs.get(job_id)
    if job is None:
        raise ValueError(f"Job not found: {job_id}")
    if job.status in {"cancelled", "completed", "failed", "paused"}:
        return {"status": job.status, "job_id": job.id, "job": job}
    if job.cancel_requested:
        cancelled = store.update_job(job.id, "cancelled", "CANCELLED_BY_OPERATOR")
        return {"status": "cancelled", "job_id": job.id, "job": cancelled or job}
    store.update_job(job.id, "running")
    if store.get_strategy(request.agent_strategy_id) is None:
        store.update_job(job.id, "failed", "STRATEGY_NOT_FOUND")
        return {"status": "failed", "error": "STRATEGY_NOT_FOUND", "job_id": job.id}
    if store.get_policy(request.policy_version_id) is None:
        store.update_job(job.id, "failed", "POLICY_NOT_FOUND")
        return {"status": "failed", "error": "POLICY_NOT_FOUND", "job_id": job.id}
    catalog = list_golden_tasks(request.benchmark_name)
    selected_catalog = catalog[: request.limit]
    catalog_validation = {
        "task_count": len(catalog),
        "selected_count": len(selected_catalog),
        "all_have_execution_config": all(bool(item.execution_config) for item in catalog),
        "missing_execution_config": [item.id for item in catalog if not item.execution_config],
        "expected_file_count": sum(1 for item in catalog if item.expected_path),
        "success_criteria_count": sum(1 for item in catalog if item.success_criteria),
    }
    seeded = []
    for golden in selected_catalog:
        existing = next(
            (
                task
                for task in store.tasks.values()
                if task.title == golden.title and task.repo_path == golden.repo_path
            ),
            None,
        )
        seeded.append(
            store.create_task(golden.to_task_request())
            if existing is None
            else store.update_task(golden.sync_task(existing))
        )
    if not seeded:
        store.update_job(job.id, "failed", "GOLDEN_TASKS_NOT_FOUND")
        return {
            "status": "failed",
            "error": "GOLDEN_TASKS_NOT_FOUND",
            "job_id": job.id,
            "catalog_validation": catalog_validation,
        }
    try:
        evaluation = await benchmark_runner.run(
            CreateEvaluationRunRequest(
                benchmark_name=request.benchmark_name,
                task_ids=[task.id for task in seeded],
                agent_strategy_id=request.agent_strategy_id,
                policy_version_id=request.policy_version_id,
                model_name=request.model_name,
            )
        )
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    passed = [item for item in evaluation.items if item.success]
    failed = [item for item in evaluation.items if not item.success]
    result_json = {
        "evaluation_run_id": evaluation.id,
        "benchmark_name": request.benchmark_name,
        "task_count": len(evaluation.items),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "all_passed": bool(evaluation.items) and not failed,
        "avg_score": evaluation.summary.get("avg_score", 0),
        "report_path": f"/api/v1/evaluations/runs/{evaluation.id}/report",
        "catalog_validation": catalog_validation,
    }
    store.add_audit_log(
        action="benchmark.acceptance",
        resource_type="benchmark",
        resource_id=request.benchmark_name,
        decision="completed",
        actor_id="system",
        detail_json={"job_id": job.id, "summary": result_json},
    )
    store.update_job(job.id, "completed", result_json=result_json)
    return {
        "status": "completed",
        "evaluation": evaluation,
        "acceptance": {
            "job_id": job.id,
            "task_count": len(evaluation.items),
            "passed_count": len(passed),
            "failed_count": len(failed),
            "all_passed": bool(evaluation.items) and not failed,
            "failure_type_distribution": evaluation.summary.get("failure_type_distribution", {}),
            "report_path": f"/api/v1/evaluations/runs/{evaluation.id}/report",
            "evaluation_id": evaluation.id,
            "catalog_validation": catalog_validation,
            "items": [
                {
                    "task_id": item.task_id,
                    "success": item.success,
                    "score": item.score,
                    "failure_reason": item.failure_reason,
                    "criteria_met": item.metrics.get("criteria_met", 0),
                    "criteria_total": item.metrics.get("criteria_total", 0),
                    "failed_criteria": item.metrics.get("failed_criteria", []),
                    "expected_checks": item.metrics.get("expected_checks", []),
                }
                for item in evaluation.items
            ],
        },
    }


job_queue.register_handler(_execute_golden_acceptance_job)


@router.get("/benchmarks/golden-tasks/validation")
async def validate_golden_tasks(benchmark_name: str = "coding_golden_v1") -> dict[str, object]:
    catalog = list_golden_tasks(benchmark_name)
    return {
        "benchmark_name": benchmark_name,
        "task_count": len(catalog),
        "all_valid": bool(catalog)
        and all(item.execution_config and item.success_criteria for item in catalog),
        "items": [
            {
                "id": item.id,
                "title": item.title,
                "has_execution_config": bool(item.execution_config),
                "has_expected_file": bool(item.expected_path),
                "success_criteria_count": len(item.success_criteria),
                "source_path": item.execution_config.get("source_path"),
                "patch_configured": bool(item.execution_config.get("patch")),
            }
            for item in catalog
        ],
    }
