from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.context import (
    filter_by_workspace,
    job_workspace_id,
    require_job_access,
    workspace_id_for_request,
)
from app.config import get_settings
from app.domain.schemas import JobResponse, RunStatus
from app.infra.store import WorkspaceQuotaExceeded, store
from app.services.job_queue import job_queue

router = APIRouter(tags=["jobs"])
RETRYABLE_JOB_KINDS = {
    "agent_run",
    "agent_run_resume",
    "evaluation",
    "repository_sync",
    "repository_repair",
    "coding_acceptance",
    "strategy_comparison",
    "research_acceptance",
    "notebook_run",
    "notebook_execute",
}

CANCELLABLE_JOB_KINDS = RETRYABLE_JOB_KINDS | {
    "research_cycle",
    "research_evidence_import",
    "release_gate",
    "dataset_export",
}


class JobActionRequest(BaseModel):
    reason: str | None = None


def _job_summary(items) -> dict[str, object]:
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for item in items:
        by_status[item.status] = by_status.get(item.status, 0) + 1
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
    return {
        "total": len(items),
        "by_status": dict(sorted(by_status.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "retryable_failed": sum(
            1
            for item in items
            if item.status in {"failed", "cancelled"}
            and item.kind in RETRYABLE_JOB_KINDS
        ),
        "cancel_requested": sum(1 for item in items if item.cancel_requested),
    }


@router.get("/jobs/summary")
async def job_summary(request: Request) -> dict[str, object]:
    visible_jobs = filter_by_workspace(request, store.read_jobs(), job_workspace_id)
    return {
        **_job_summary(visible_jobs),
        "queue": await job_queue.snapshot(),
    }


@router.get("/jobs")
async def list_jobs(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    task_id: str | None = None,
    workspace_id: str | None = None,
    resource_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request, workspace_id)
    items = store.read_jobs(
        kind=kind,
        status=status,
        task_id=task_id,
        resource_id=resource_id,
    )
    if workspace_id:
        items = [item for item in items if job_workspace_id(item) == workspace_id]
    items = filter_by_workspace(request, items, job_workspace_id)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    request_context: Request,
    job_id: str,
    request: JobActionRequest | None = None,
) -> JobResponse:
    job = require_job_access(request_context, store.jobs.get(job_id))
    if job.status in {"completed", "failed", "cancelled", "paused"}:
        return job
    if job.status == "queued" and job.kind not in {"agent_run", "agent_run_resume"}:
        cancelled = store.update_job(
            job_id,
            "cancelled",
            (request.reason if request else None) or "CANCELLED_BY_OPERATOR",
            result_json={
                "job_id": job.id,
                "kind": job.kind,
                "resource_id": job.resource_id,
                "reason": request.reason if request else None,
            },
        )
        if job.kind == "evaluation":
            evaluation = store.get_evaluation_run(job.resource_id)
            if evaluation is not None:
                store.update_evaluation_run(
                    evaluation.model_copy(
                        update={
                            "status": RunStatus.CANCELLED,
                            "summary": {
                                **evaluation.summary,
                                "status": "cancelled",
                                "error": "CANCELLED_BY_OPERATOR",
                            },
                        }
                    )
                )
        return cancelled or job
    if job.kind not in CANCELLABLE_JOB_KINDS:
        raise HTTPException(
            status_code=409,
            detail="This Job kind does not support cooperative cancellation",
        )
    updated = store.request_job_cancel(job_id, request.reason if request else None)
    if job.kind in {"agent_run", "agent_run_resume"}:
        from app.api.runs import runtime

        await runtime.cancel_run(job.resource_id)
    else:
        updated = store.update_job(
            job_id,
            "cancelled",
            (request.reason if request else None) or "CANCELLED_BY_OPERATOR",
            result_json={
                "job_id": job.id,
                "kind": job.kind,
                "resource_id": job.resource_id,
                "reason": request.reason if request else None,
            },
        ) or updated
    return updated or job


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    job = require_job_access(request, store.jobs.get(job_id))
    if job.kind not in RETRYABLE_JOB_KINDS:
        raise HTTPException(status_code=409, detail="This Job kind is not retryable")
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled Jobs can be retried")
    retry_count = int(job.metadata.get("retry_count", 0))
    max_retries = max(0, get_settings().job_max_retries)
    if retry_count >= max_retries:
        raise HTTPException(status_code=409, detail="Job retry limit reached")
    if job.kind == "notebook_execute":
        from app.api.notebooks import execute_notebook_job
        from app.research.notebooks import ExecuteNotebookRequest

        data = job.metadata.get("request")
        if not isinstance(data, dict) or store.get_research_brief(job.resource_id) is None:
            raise HTTPException(409, "NOTEBOOK_RETRY_PAYLOAD_MISSING")
        ExecuteNotebookRequest.model_validate(data)
        retried = store.create_job(
            kind=job.kind, resource_id=job.resource_id, task_id=job.task_id,
            metadata={**job.metadata, "retry_count": retry_count + 1, "previous_job_id": job.id},
            parent_job_id=job.parent_job_id or job.id, retry_of_job_id=job.id,
        )
        store.add_audit_log(action="job.retry", resource_type="job", resource_id=retried.id, decision="queued", detail_json={"retry_of_job_id": job.id})
        if job_queue.backend == "redis":
            await job_queue.enqueue(None, execute_notebook_job, retried.id, job.resource_id, data)
            return {"job": retried}
        result = await execute_notebook_job(retried.id, job.resource_id, data)
        return {"job": retried, "result": result}
    if job.kind == "repository_sync":
        repository = store.get_repository_connection(job.resource_id)
        if repository is None:
            raise HTTPException(status_code=404, detail="Repository connection not found")
        retry_job = store.create_job(
            kind=job.kind,
            resource_id=repository.id,
            metadata={
                **job.metadata,
                "retry_count": retry_count + 1,
                "previous_job_id": job.id,
                "workspace_id": repository.workspace_id,
            },
            parent_job_id=job.parent_job_id or job.id,
            retry_of_job_id=job.id,
        )
        store.add_audit_log(
            action="job.retry",
            resource_type="job",
            resource_id=retry_job.id,
            decision="queued",
            actor_id="operator",
            detail_json={
                "retry_of_job_id": job.id,
                "repository_id": repository.id,
            },
        )
        from app.api.integrations import _execute_repository_sync_job

        if job_queue.backend == "redis":
            await job_queue.enqueue(
                None,
                _execute_repository_sync_job,
                repository.id,
                retry_job.id,
            )
        else:
            await _execute_repository_sync_job(repository.id, retry_job.id)
        return {"job": retry_job, "repository": repository}
    if job.kind == "repository_repair":
        repository = store.get_repository_connection(job.resource_id)
        if repository is None:
            raise HTTPException(status_code=404, detail="Repository connection not found")
        request_data = job.metadata.get("request")
        if not isinstance(request_data, dict):
            raise HTTPException(status_code=409, detail="Job does not contain a retryable request payload")
        from app.api.integrations import _execute_repository_repair_job

        retry_job = store.create_job(
            kind=job.kind,
            resource_id=repository.id,
            task_id=job.task_id,
            metadata={
                **job.metadata,
                "retry_count": retry_count + 1,
                "previous_job_id": job.id,
                "workspace_id": repository.workspace_id,
            },
            parent_job_id=job.parent_job_id or job.id,
            retry_of_job_id=job.id,
        )
        store.add_audit_log(
            action="job.retry",
            resource_type="job",
            resource_id=retry_job.id,
            decision="queued",
            actor_id="operator",
            detail_json={"retry_of_job_id": job.id, "repository_id": repository.id},
        )
        if job_queue.backend == "redis":
            await job_queue.enqueue(None, _execute_repository_repair_job, repository.id, retry_job.id, request_data)
            return {"job": retry_job, "repository": repository}
        result = await _execute_repository_repair_job(repository.id, retry_job.id, request_data)
        return {"job": retry_job, "repository": repository, "result": result}
    if job.kind in {
        "coding_acceptance",
        "strategy_comparison",
        "research_acceptance",
        "notebook_run",
        "evaluation",
    }:
        if job.kind == "evaluation":
            request_data = job.metadata.get("request")
            if not isinstance(request_data, dict):
                raise HTTPException(
                    status_code=409,
                    detail="Job does not contain a retryable request payload",
                )
            from app.domain.schemas import (
                CreateEvaluationRunRequest,
                EvaluationRunResponse,
                RunStatus,
            )
            from app.infra.idgen import id_generator

            evaluation_id = id_generator.next("eval")
            retry_job = store.create_job(
                kind=job.kind,
                resource_id=evaluation_id,
                metadata={
                    **job.metadata,
                    "retry_count": retry_count + 1,
                    "previous_job_id": job.id,
                    "evaluation_id": evaluation_id,
                },
                parent_job_id=job.parent_job_id or job.id,
                retry_of_job_id=job.id,
            )
            request = CreateEvaluationRunRequest.model_validate(request_data)
            placeholder = EvaluationRunResponse(
                id=evaluation_id,
                benchmark_name=request.benchmark_name,
                policy_version_id=request.policy_version_id,
                agent_strategy_id=request.agent_strategy_id,
                model_name=request.model_name or "model_pending",
                status=RunStatus.QUEUED,
                job_id=retry_job.id,
                summary={
                    "status": "queued",
                    "job_id": retry_job.id,
                    "task_count": len(request.task_ids),
                },
            )
            store.add_evaluation_run(placeholder)
            from app.api.evaluations import _execute_evaluation_job

            if job_queue.backend == "redis":
                await job_queue.enqueue(
                    None,
                    _execute_evaluation_job,
                    retry_job.id,
                    evaluation_id,
                    request_data,
                )
                return {"job": retry_job, "evaluation": placeholder}
            result = await _execute_evaluation_job(retry_job.id, evaluation_id, request_data)
            return {"job": retry_job, "evaluation": result}
        request_data = job.metadata.get("request")
        if job.kind == "notebook_run":
            brief_id = str(job.metadata.get("brief_id") or job.resource_id)
            if not brief_id:
                raise HTTPException(status_code=400, detail="Notebook Job is missing brief_id")
            retry_job = store.create_job(
                kind=job.kind,
                resource_id=brief_id,
                task_id=job.task_id,
                metadata={
                    **job.metadata,
                    "retry_count": retry_count + 1,
                    "previous_job_id": job.id,
                },
                parent_job_id=job.parent_job_id or job.id,
                retry_of_job_id=job.id,
            )
            from app.api.research import _execute_notebook_run_job

            if job_queue.backend == "redis":
                await job_queue.enqueue(None, _execute_notebook_run_job, brief_id, retry_job.id)
                return {"job": retry_job}
            result = await _execute_notebook_run_job(brief_id, retry_job.id)
            return {"job": retry_job, "result": result}
        if not isinstance(request_data, dict):
            raise HTTPException(
                status_code=409,
                detail="Job does not contain a retryable request payload",
            )
        retry_job = store.create_job(
            kind=job.kind,
            resource_id=job.resource_id,
            task_id=job.task_id,
            metadata={
                **job.metadata,
                "retry_count": retry_count + 1,
                "previous_job_id": job.id,
            },
            parent_job_id=job.parent_job_id or job.id,
            retry_of_job_id=job.id,
        )
        if job.kind == "coding_acceptance":
            from app.api.benchmarks import _execute_golden_acceptance_job

            handler = _execute_golden_acceptance_job
        elif job.kind == "strategy_comparison":
            from app.api.evaluations import _execute_strategy_comparison_job

            handler = _execute_strategy_comparison_job
        else:
            from app.api.research import _execute_research_benchmark_acceptance_job

            handler = _execute_research_benchmark_acceptance_job
        if job_queue.backend == "redis":
            await job_queue.enqueue(None, handler, retry_job.id, request_data)
            return {"job": retry_job}
        result = await handler(retry_job.id, request_data)
        return {"job": retry_job, "result": result}
    previous_run = store.get_run(job.resource_id)
    if previous_run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    from app.api.runs import _execute_run_job

    try:
        retry_run = store.create_run(
            task_id=previous_run.task_id,
            agent_strategy_id=previous_run.agent_strategy_id,
            policy_version_id=previous_run.policy_version_id,
            model_name=previous_run.model_name,
        )
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.code) from exc
    retry_job = store.create_job(
        kind=job.kind,
        resource_id=retry_run.id,
        task_id=previous_run.task_id,
        parent_job_id=job.parent_job_id or job.id,
        retry_of_job_id=job.id,
        metadata={
            **job.metadata,
            "retry_count": retry_count + 1,
            "previous_run_id": previous_run.id,
        },
    )
    store.add_audit_log(
        action="job.retry",
        resource_type="job",
        resource_id=retry_job.id,
        decision="queued",
        actor_id="operator",
        detail_json={"retry_of_job_id": job.id, "previous_run_id": previous_run.id},
    )
    await job_queue.enqueue(background_tasks, _execute_run_job, retry_run.id, retry_job.id)
    return {"job": retry_job, "run": retry_run}


@router.post("/jobs/{job_id}/resume")
async def resume_job(
    request: Request,
    job_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    job = require_job_access(request, store.jobs.get(job_id))
    if job.kind not in {"agent_run", "agent_run_resume"}:
        raise HTTPException(status_code=409, detail="This Job kind is not resumable")
    if job.status != "paused":
        raise HTTPException(status_code=409, detail="Only paused Jobs can be resumed")
    previous_run = store.get_run(job.resource_id)
    if previous_run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    from app.api.runs import resume_run_after_approval

    resumed = await resume_run_after_approval(previous_run.id, background_tasks, request)
    store.add_audit_log(
        action="job.resume",
        resource_type="job",
        resource_id=job.id,
        decision="queued",
        actor_id="operator",
        detail_json={
            "previous_run_id": previous_run.id,
            "resumed_run_id": resumed.id,
        },
    )
    return {"job": job, "run": resumed}


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(request: Request, job_id: str) -> JobResponse:
    return require_job_access(request, store.read_job(job_id))




