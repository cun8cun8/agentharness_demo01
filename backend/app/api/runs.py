import hashlib
import asyncio
import csv
from datetime import datetime, timezone
from app.config import get_settings
import json
from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.agent.runtime import AgentRuntime
from app.api.context import require_run_access, require_task_access, workspace_id_for_request
from app.domain.schemas import AgentRunResponse, BatchRunRequest, RetryRunRequest, RunStatus, StartRunRequest
from app.infra.store import WorkspaceQuotaExceeded, store
from app.services.job_queue import job_queue

router = APIRouter(tags=["runs"])
runtime = AgentRuntime(store=store)


def _enrich_run_task_context(item: dict[str, object]) -> dict[str, object]:
    task = store.get_task(str(item.get("task_id") or ""))
    if task is None:
        return item
    item.setdefault("task_title", task.title)
    for key, context_value in store.run_project_context(task).items():
        if not item.get(key):
            item[key] = context_value
    return item


def _parse_time_filter(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, f"INVALID_{field.upper()}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _run_time(item: dict[str, object]) -> datetime:
    raw = str(item.get("finished_at") or item.get("started_at") or item.get("created_at") or "")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _filtered_runs(
    *,
    workspace_id: str | None,
    task_id: str | None,
    status: str | None,
    query: str | None,
    project_name: str | None,
    branch: str | None,
    model_name: str | None,
    strategy_id: str | None,
    from_time: datetime | None,
    to_time: datetime | None,
    sort: str,
) -> list[dict[str, object]]:
    if sort not in {"completed_desc", "created_desc", "duration_desc", "cost_desc"}:
        raise HTTPException(422, "INVALID_RUN_SORT")
    needle = (query or "").strip().lower()
    rows: list[dict[str, object]] = []
    candidate_runs = store.query_run_records(
        workspace_id=workspace_id,
        task_id=task_id,
        status=status,
        query=query,
        model_name=model_name,
        strategy_id=strategy_id,
        from_time=from_time,
        to_time=to_time,
    )
    for run in candidate_runs:
        task = store.get_task(run.task_id)
        if task is None or (workspace_id and task.workspace_id != workspace_id):
            continue
        row = _enrich_run_task_context(run.model_dump(mode="json"))
        if project_name and str(row.get("project_name") or "") != project_name:
            continue
        if branch and str(row.get("branch") or "") != branch:
            continue
        event_time = _run_time(row)
        if needle:
            searchable = " ".join(
                str(row.get(key) or "")
                for key in (
                    "id",
                    "task_id",
                    "task_title",
                    "repository_id",
                    "project_name",
                    "repo_path",
                    "repository_url",
                    "branch",
                    "default_branch",
                    "model_name",
                    "agent_strategy_id",
                    "error_summary",
                )
            ).lower()
            if needle not in searchable:
                continue
        rows.append(row)
    if sort == "duration_desc":
        return sorted(rows, key=lambda item: (float(item.get("duration_ms") or 0), _run_time(item)), reverse=True)
    if sort == "cost_desc":
        return sorted(rows, key=lambda item: (float(item.get("total_cost") or 0), _run_time(item)), reverse=True)
    if sort == "created_desc":
        return sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return sorted(rows, key=_run_time, reverse=True)


def _run_facets(rows: list[dict[str, object]]) -> dict[str, list[str]]:
    fields = {
        "projects": "project_name",
        "branches": "branch",
        "models": "model_name",
        "strategies": "agent_strategy_id",
    }
    return {
        key: sorted({str(row.get(field) or "") for row in rows if row.get(field)})
        for key, field in fields.items()
    }


def _run_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    projects: dict[str, dict[str, object]] = {}
    terminal = {"completed", "failed", "cancelled", "blocked"}
    for row in rows:
        project = str(row.get("project_name") or "未关联项目")
        aggregate = projects.setdefault(
            project,
            {
                "project_name": project,
                "run_count": 0,
                "completed_count": 0,
                "failed_count": 0,
                "duration_ms": 0,
                "total_cost": 0.0,
                "last_finished_at": None,
            },
        )
        aggregate["run_count"] = int(aggregate["run_count"]) + 1
        if row.get("status") == "completed":
            aggregate["completed_count"] = int(aggregate["completed_count"]) + 1
        if row.get("status") in {"failed", "cancelled", "blocked"}:
            aggregate["failed_count"] = int(aggregate["failed_count"]) + 1
        aggregate["duration_ms"] = int(aggregate["duration_ms"]) + int(row.get("duration_ms") or 0)
        aggregate["total_cost"] = float(aggregate["total_cost"]) + float(row.get("total_cost") or 0)
        finished_at = str(row.get("finished_at") or row.get("created_at") or "")
        if finished_at and (not aggregate["last_finished_at"] or finished_at > str(aggregate["last_finished_at"])):
            aggregate["last_finished_at"] = finished_at
    project_items = []
    for aggregate in projects.values():
        resolved = int(aggregate["completed_count"]) + int(aggregate["failed_count"])
        aggregate["success_rate"] = round(int(aggregate["completed_count"]) / resolved, 4) if resolved else None
        aggregate["average_duration_ms"] = round(int(aggregate["duration_ms"]) / int(aggregate["run_count"])) if aggregate["run_count"] else 0
        aggregate["average_cost"] = round(float(aggregate["total_cost"]) / int(aggregate["run_count"]), 6) if aggregate["run_count"] else 0.0
        project_items.append(aggregate)
    completed = sum(1 for row in rows if row.get("status") == "completed")
    failed = sum(1 for row in rows if row.get("status") in {"failed", "cancelled", "blocked"})
    resolved = completed + failed
    return {
        "total_runs": len(rows),
        "completed_runs": completed,
        "failed_runs": failed,
        "success_rate": round(completed / resolved, 4) if resolved else None,
        "total_cost": round(sum(float(row.get("total_cost") or 0) for row in rows), 6),
        "average_duration_ms": round(sum(int(row.get("duration_ms") or 0) for row in rows) / len(rows)) if rows else 0,
        "projects": sorted(project_items, key=lambda item: (str(item["last_finished_at"] or ""), str(item["project_name"])), reverse=True),
    }


@router.post("/tasks/{task_id}/runs", response_model=AgentRunResponse)
async def start_run(
    task_id: str,
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    request_context: Request,
) -> AgentRunResponse:
    require_task_access(request_context, store.get_task(task_id))
    if store.get_strategy(request.agent_strategy_id) is None:
        raise HTTPException(status_code=400, detail="Strategy not found")
    if store.get_policy(request.policy_version_id) is None:
        raise HTTPException(status_code=400, detail="Policy not found")
    idempotency_key = request_context.headers.get("Idempotency-Key", "").strip()
    if idempotency_key:
        if len(idempotency_key) > 128 or any(ord(char) < 33 or ord(char) > 126 for char in idempotency_key):
            raise HTTPException(status_code=400, detail="INVALID_IDEMPOTENCY_KEY")
    try:
        run, job, created = store.create_run_with_job(
            task_id=task_id,
            agent_strategy_id=request.agent_strategy_id,
            policy_version_id=request.policy_version_id,
            model_name=request.model_name,
            idempotency_key=idempotency_key or None,
        )
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.code) from exc
    if created:
        await job_queue.enqueue(background_tasks, _execute_run_job, run.id, job.id)
    return run


async def _execute_run_job(run_id: str, job_id: str) -> None:
    store.update_job(job_id, "running")
    try:
        result = await runtime.execute_run(run_id)
    except Exception as exc:
        store.update_job(
            job_id,
            "failed",
            str(exc),
            result_json={"agent_run_id": run_id, "status": "failed", "error": str(exc)},
        )
        raise
    result_json = {
        "agent_run_id": result.id,
        "status": result.status.value,
        "phase": result.phase.value,
        "error_summary": result.error_summary,
        "tool_call_count": result.tool_call_count,
        "total_tokens": result.total_tokens,
        "total_cost": result.total_cost,
        "duration_ms": result.duration_ms,
    }
    if result.status.value == "completed":
        store.update_job(job_id, "completed", result_json=result_json)
    elif result.status.value == "paused":
        store.update_job(job_id, "paused", result.error_summary, result_json=result_json)
    elif result.status.value == "cancelled":
        store.update_job(job_id, "cancelled", result.error_summary, result_json=result_json)
    else:
        store.update_job(job_id, "failed", result.error_summary, result_json=result_json)


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_run(request: Request, run_id: str) -> AgentRunResponse:
    run = require_run_access(request, store.read_run(run_id))
    return AgentRunResponse.model_validate(_enrich_run_task_context(run.model_dump()))


@router.post("/runs/{run_id}/cancel", response_model=AgentRunResponse)
async def cancel_run(run_id: str, request_context: Request) -> AgentRunResponse:
    require_run_access(request_context, store.get_run(run_id))
    return await runtime.cancel_run(run_id)


@router.post("/runs/{run_id}/pause", response_model=AgentRunResponse)
async def pause_run(run_id: str, request_context: Request) -> AgentRunResponse:
    require_run_access(request_context, store.get_run(run_id))
    return await runtime.pause_run(run_id)


@router.post("/runs/{run_id}/resume", response_model=AgentRunResponse)
async def resume_run_after_approval(
    run_id: str,
    background_tasks: BackgroundTasks,
    request_context: Request,
) -> AgentRunResponse:
    previous = require_run_access(request_context, store.get_run(run_id))
    if previous.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
        raise HTTPException(status_code=400, detail="Run is still active")
    if previous.status == RunStatus.PAUSED and previous.metrics.get("runtime") == "langgraph":
        store.pause_requests.discard(previous.id)
        store.update_run(previous.id, status=RunStatus.QUEUED)
        job = store.create_job(kind="agent_run_resume", resource_id=previous.id, task_id=previous.task_id,
                               metadata={"previous_run_id": previous.id, "resume_mode": "checkpoint"})
        await job_queue.enqueue(background_tasks, _execute_run_job, previous.id, job.id)
        return previous
    existing_resume_jobs = sorted(
        (
            job
            for job in store.jobs.values()
            if job.kind == "agent_run_resume"
            and job.metadata.get("previous_run_id") == previous.id
        ),
        key=lambda item: item.created_at,
        reverse=True,
    )
    for existing_job in existing_resume_jobs:
        existing_run = store.get_run(existing_job.resource_id)
        if existing_run is None:
            continue
        if existing_job.status in {"queued", "running", "paused", "completed", "failed", "cancelled"}:
            return existing_run
    approved = []
    if previous.status != RunStatus.PAUSED:
        approved = store.list_approved_tool_approvals_for_task(
            task_id=previous.task_id,
            policy_version_id=previous.policy_version_id,
        )
        if not approved:
            raise HTTPException(status_code=400, detail="A matching approved request is required")
    try:
        resumed = store.create_run(
            task_id=previous.task_id,
            agent_strategy_id=previous.agent_strategy_id,
            policy_version_id=previous.policy_version_id,
            model_name=previous.model_name,
        )
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.code) from exc
    job = store.create_job(
        kind="agent_run_resume",
        resource_id=resumed.id,
        task_id=previous.task_id,
        metadata={
            "previous_run_id": previous.id,
            "approval_ids": [item.id for item in approved],
            "resume_mode": "paused" if previous.status == RunStatus.PAUSED else "approval",
        },
    )
    for previous_job in store.list_jobs(resource_id=previous.id):
        if previous_job.status == "paused":
            store.update_job(
                previous_job.id,
                "completed",
                result_json={
                    **previous_job.result_json,
                    "resumed_run_id": resumed.id,
                    "resumed_from_run_id": previous.id,
                },
                metadata={"resumed_run_id": resumed.id},
            )
    store.add_audit_log(
        action="run.resume",
        resource_type="run",
        resource_id=resumed.id,
        decision="queued",
        actor_id="operator",
        detail_json={
            "previous_run_id": previous.id,
            "task_id": previous.task_id,
            "approval_ids": [item.id for item in approved],
            "job_id": job.id,
            "resume_mode": "paused" if previous.status == RunStatus.PAUSED else "approval",
        },
    )
    await job_queue.enqueue(background_tasks, _execute_run_job, resumed.id, job.id)
    return resumed


@router.post("/runs/{run_id}/retry", response_model=AgentRunResponse)
async def retry_run(
    run_id: str,
    body: RetryRunRequest,
    background_tasks: BackgroundTasks,
    request_context: Request,
) -> AgentRunResponse:
    previous = require_run_access(request_context, store.get_run(run_id))
    if previous.status in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED}:
        raise HTTPException(409, "ACTIVE_RUN_CANNOT_RETRY")
    strategy_id = body.agent_strategy_id or previous.agent_strategy_id
    policy_id = body.policy_version_id or previous.policy_version_id
    model_name = body.model_name or previous.model_name
    if store.get_strategy(strategy_id) is None:
        raise HTTPException(400, "STRATEGY_NOT_FOUND")
    if store.get_policy(policy_id) is None:
        raise HTTPException(400, "POLICY_NOT_FOUND")
    try:
        retried = store.create_run(
            task_id=previous.task_id,
            agent_strategy_id=strategy_id,
            policy_version_id=policy_id,
            model_name=model_name,
        )
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.code) from exc
    job = store.create_job(
        kind="agent_run_retry",
        resource_id=retried.id,
        task_id=retried.task_id,
        metadata={
            "previous_run_id": previous.id,
            "workspace_id": getattr(request_context.state, "workspace_id", None),
            "strategy_id": strategy_id,
            "policy_id": policy_id,
            "model_name": model_name,
        },
    )
    store.add_audit_log(
        action="run.retry",
        resource_type="run",
        resource_id=retried.id,
        decision="queued",
        actor_id=getattr(request_context.state, "user_id", "operator"),
        detail_json={"previous_run_id": previous.id, "job_id": job.id},
    )
    await job_queue.enqueue(background_tasks, _execute_run_job, retried.id, job.id)
    return retried


def _unique_run_ids(run_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(run_id.strip() for run_id in run_ids if run_id.strip()))


def _validate_batch_run_access(request: Request, run_ids: list[str]) -> list[AgentRunResponse]:
    unique_ids = _unique_run_ids(run_ids)
    if not unique_ids:
        raise HTTPException(status_code=422, detail="RUN_IDS_REQUIRED")
    if len(unique_ids) > 50:
        raise HTTPException(status_code=422, detail="TOO_MANY_RUN_IDS")
    return [require_run_access(request, store.get_run(run_id)) for run_id in unique_ids]


@router.post("/runs/batch-cancel")
async def batch_cancel_runs(
    body: BatchRunRequest,
    request_context: Request,
) -> dict[str, object]:
    runs = _validate_batch_run_access(request_context, body.run_ids)
    items: list[dict[str, object]] = []
    for run in runs:
        original_status = run.status
        updated = await runtime.cancel_run(run.id)
        items.append(
            {
                "run_id": run.id,
                "status": updated.status.value,
                "changed": updated.status != original_status,
            }
        )
    store.add_audit_log(
        action="run.batch_cancel",
        resource_type="run_batch",
        resource_id=items[0]["run_id"] if items else "batch",
        decision="completed",
        actor_id=getattr(request_context.state, "user_id", "operator"),
        detail_json={"run_ids": [item["run_id"] for item in items], "count": len(items)},
    )
    return {
        "operation": "cancel",
        "requested": len(items),
        "changed": sum(1 for item in items if item["changed"]),
        "items": items,
    }


@router.post("/runs/batch-retry")
async def batch_retry_runs(
    body: BatchRunRequest,
    background_tasks: BackgroundTasks,
    request_context: Request,
) -> dict[str, object]:
    runs = _validate_batch_run_access(request_context, body.run_ids)
    retry_body = RetryRunRequest(
        agent_strategy_id=body.agent_strategy_id,
        policy_version_id=body.policy_version_id,
        model_name=body.model_name,
    )
    items: list[dict[str, object]] = []
    for run in runs:
        try:
            retried = await retry_run(run.id, retry_body, background_tasks, request_context)
            items.append({"run_id": run.id, "status": "queued", "retry_run_id": retried.id})
        except HTTPException as exc:
            items.append({"run_id": run.id, "status": "skipped", "error": str(exc.detail)})
    queued = sum(1 for item in items if item["status"] == "queued")
    store.add_audit_log(
        action="run.batch_retry",
        resource_type="run_batch",
        resource_id=items[0]["run_id"] if items else "batch",
        decision="completed" if queued == len(items) else "partial",
        actor_id=getattr(request_context.state, "user_id", "operator"),
        detail_json={"run_ids": [item["run_id"] for item in items], "queued": queued},
    )
    return {"operation": "retry", "requested": len(items), "queued": queued, "items": items}


@router.get("/runs")
async def list_runs(
    request: Request,
    task_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    query: str | None = None,
    project_name: str | None = None,
    branch: str | None = None,
    model_name: str | None = None,
    strategy_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    sort: str = "completed_desc",
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request, workspace_id)
    if task_id:
        require_task_access(request, store.get_task(task_id))
    all_rows = _filtered_runs(
        workspace_id=workspace_id,
        task_id=task_id,
        status=status,
        query=query,
        project_name=project_name,
        branch=branch,
        model_name=model_name,
        strategy_id=strategy_id,
        from_time=_parse_time_filter(from_time, "from_time"),
        to_time=_parse_time_filter(to_time, "to_time"),
        sort=sort,
    )
    return {
        "items": all_rows[offset : offset + limit],
        "total": len(all_rows),
        "facets": _run_facets(all_rows),
        "summary": _run_summary(all_rows),
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(all_rows),
        "next_offset": offset + limit if offset + limit < len(all_rows) else None,
    }


@router.get("/run-summary")
async def run_summary(
    request: Request,
    task_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    query: str | None = None,
    project_name: str | None = None,
    branch: str | None = None,
    model_name: str | None = None,
    strategy_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request, workspace_id)
    if task_id:
        require_task_access(request, store.get_task(task_id))
    rows = _filtered_runs(
        workspace_id=workspace_id,
        task_id=task_id,
        status=status,
        query=query,
        project_name=project_name,
        branch=branch,
        model_name=model_name,
        strategy_id=strategy_id,
        from_time=_parse_time_filter(from_time, "from_time"),
        to_time=_parse_time_filter(to_time, "to_time"),
        sort="completed_desc",
    )
    return {"summary": _run_summary(rows), "facets": _run_facets(rows)}


@router.get("/run-records/export.csv")
async def export_run_records(
    request: Request,
    task_id: str | None = None,
    workspace_id: str | None = None,
    status: str | None = None,
    query: str | None = None,
    project_name: str | None = None,
    branch: str | None = None,
    model_name: str | None = None,
    strategy_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    sort: str = "completed_desc",
    limit: int = Query(default=10_000, ge=1, le=10_000),
) -> Response:
    """Export the current run-record view without exposing trace payloads."""
    workspace_id = workspace_id_for_request(request, workspace_id)
    if task_id:
        require_task_access(request, store.get_task(task_id))
    rows = _filtered_runs(
        workspace_id=workspace_id,
        task_id=task_id,
        status=status,
        query=query,
        project_name=project_name,
        branch=branch,
        model_name=model_name,
        strategy_id=strategy_id,
        from_time=_parse_time_filter(from_time, "from_time"),
        to_time=_parse_time_filter(to_time, "to_time"),
        sort=sort,
    )[:limit]
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "run_id",
            "task_id",
            "task_title",
            "project_name",
            "repository_id",
            "repository_url",
            "branch",
            "status",
            "model_name",
            "strategy_id",
            "created_at",
            "started_at",
            "finished_at",
            "duration_ms",
            "total_tokens",
            "total_cost",
            "error_summary",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("id"),
                row.get("task_id"),
                row.get("task_title"),
                row.get("project_name"),
                row.get("repository_id"),
                row.get("repository_url"),
                row.get("branch"),
                row.get("status"),
                row.get("model_name"),
                row.get("agent_strategy_id"),
                row.get("created_at"),
                row.get("started_at"),
                row.get("finished_at"),
                row.get("duration_ms"),
                row.get("total_tokens"),
                row.get("total_cost"),
                row.get("error_summary"),
            ]
        )
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=researchforge-run-records.csv"},
    )


@router.get("/run-comparison")
async def compare_runs(
    request: Request,
    left_run_id: str,
    right_run_id: str,
) -> dict[str, object]:
    left = require_run_access(request, store.read_run(left_run_id))
    right = require_run_access(request, store.read_run(right_run_id))
    left_row = _enrich_run_task_context(left.model_dump(mode="json"))
    right_row = _enrich_run_task_context(right.model_dump(mode="json"))
    return {
        "left": left_row,
        "right": right_row,
        "same_task": left.task_id == right.task_id,
        "delta": {
            "duration_ms": right.duration_ms - left.duration_ms,
            "total_cost": round(right.total_cost - left.total_cost, 6),
            "total_tokens": right.total_tokens - left.total_tokens,
            "tool_call_count": right.tool_call_count - left.tool_call_count,
            "tests_passed": int(right.metrics.get("tests_passed") or 0) - int(left.metrics.get("tests_passed") or 0),
        },
    }


@router.get("/runs/{run_id}/steps")
async def list_steps(
    request: Request,
    run_id: str,
    limit: int = Query(default=1000, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    require_run_access(request, store.read_run(run_id))
    all_items = store.read_steps(run_id)
    return {
        "run_id": run_id,
        "items": all_items[offset : offset + limit],
        "total": len(all_items),
    }


@router.get("/runs/{run_id}/tool-calls")
async def list_tool_calls(
    request: Request,
    run_id: str,
    limit: int = Query(default=1000, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    require_run_access(request, store.read_run(run_id))
    all_items = store.read_tool_calls(run_id)
    return {
        "run_id": run_id,
        "items": all_items[offset : offset + limit],
        "total": len(all_items),
    }


@router.get("/runs/{run_id}/artifacts")
async def list_artifacts(
    request: Request,
    run_id: str,
    limit: int = Query(default=1000, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    require_run_access(request, store.read_run(run_id))
    all_items = store.read_artifacts(run_id)
    return {
        "run_id": run_id,
        "items": all_items[offset : offset + limit],
        "total": len(all_items),
    }


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(request: Request, artifact_id: str) -> dict[str, str]:
    artifact = store.read_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    require_run_access(request, store.read_run(artifact.run_id))
    return {"id": artifact.id, "name": artifact.name, "content": artifact.content}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(request: Request, artifact_id: str) -> Response:
    artifact = store.read_artifact(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    require_run_access(request, store.read_run(artifact.run_id))
    if artifact.metadata.get("content_expired_at"):
        raise HTTPException(410, "ARTIFACT_CONTENT_EXPIRED")
    import re
    filename = re.sub(r"[^a-zA-Z0-9._-]", "_", artifact.name) or "artifact.txt"
    return Response(
        content=artifact.content or "",
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store", "X-Artifact-SHA256": hashlib.sha256((artifact.content or "").encode("utf-8")).hexdigest()},
    )


@router.get("/runs/{run_id}/export")
async def export_run_bundle(request: Request, run_id: str) -> Response:
    run = require_run_access(request, store.read_run(run_id))

    steps = store.read_steps(run_id)
    tool_calls = store.read_tool_calls(run_id)
    artifacts = store.read_artifacts(run_id)
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("run.json", run.model_dump_json(indent=2))
        archive.writestr(
            "steps.json",
            json.dumps([step.model_dump(mode="json") for step in steps], ensure_ascii=True, indent=2),
        )
        archive.writestr(
            "tool_calls.json",
            json.dumps([call.model_dump(mode="json") for call in tool_calls], ensure_ascii=True, indent=2),
        )
        archive.writestr(
            "artifacts.json",
            json.dumps([artifact.model_dump(mode="json") for artifact in artifacts], ensure_ascii=True, indent=2),
        )
        for artifact in artifacts:
            safe_name = artifact.name.replace("/", "_").replace("\\", "_")
            archive.writestr(f"artifacts/{artifact.id}-{safe_name}", artifact.content)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={run_id}-bundle.zip"},
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(request: Request, run_id: str) -> StreamingResponse:
    require_run_access(request, store.get_run(run_id))

    async def event_stream():
        queue = store.subscribe_events(run_id)
        seen: set[str] = set()
        last_event_id = request.headers.get("last-event-id")
        try:
            history = store.list_events(run_id)
            resume_index = next((index + 1 for index, event in enumerate(history) if event.id == last_event_id), 0)
            seen.update(event.id for event in history[:resume_index])
            for event in history[resume_index:]:
                seen.add(event.id)
                yield f"id: {event.id}\n"
                yield f"event: {event.event_type}\n"
                yield f"data: {event.model_dump_json()}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                run = store.get_run(run_id)
                if run is None:
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if get_settings().store_backend == "postgres" or (get_settings().job_queue_backend == "redis" and not get_settings().job_worker_enabled):
                        store.refresh()
                        for unseen in store.list_events(run_id):
                            if unseen.id not in seen:
                                seen.add(unseen.id)
                                yield f"id: {unseen.id}\nevent: {unseen.event_type}\ndata: {unseen.model_dump_json()}\n\n"
                        run = store.get_run(run_id)
                        if run is None:
                            break
                    if run.status.value in {"completed", "failed", "blocked", "cancelled", "paused"}:
                        break
                    yield ": keep-alive\n\n"
                    continue
                if event.id in seen:
                    continue
                seen.add(event.id)
                yield f"id: {event.id}\n"
                yield f"event: {event.event_type}\n"
                yield f"data: {event.model_dump_json()}\n\n"
                if event.event_type in {"run.completed", "run.failed", "run.cancelled", "run.paused"}:
                    break
        finally:
            store.unsubscribe_events(run_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


job_queue.register_handler(_execute_run_job)


@router.get("/runs/{run_id}/progress")
async def run_progress(request: Request, run_id: str):
    run = require_run_access(request, store.read_run(run_id))
    task = store.get_task(run.task_id)
    from datetime import datetime, timezone
    elapsed = max(0.0, (datetime.now(timezone.utc) - run.started_at).total_seconds()) if run.started_at and run.status == RunStatus.RUNNING else (run.duration_ms or 0) / 1000
    steps = store.read_steps(run_id)
    calls = store.read_tool_calls(run_id)
    budget = task.budget
    limits = {"seconds": (budget.max_runtime_seconds, elapsed), "tokens": (budget.max_tokens, run.total_tokens), "model_cost": (budget.max_model_cost, run.total_cost), "tool_calls": (budget.max_tool_calls, len(calls)), "steps": (budget.max_steps, len(steps))}
    remaining = {name: max(0, round(limit - used, 6)) if limit > 0 else None for name, (limit, used) in limits.items()}
    recommendations = {"DEPENDENCY_PREPARATION_FAILED": "检查依赖锁文件、离线缓存和沙箱镜像，然后使用新配置复跑。", "QUEUE_WAIT_TIMEOUT": "检查 Worker 和工作区并发配额，然后重试任务。", "BUDGET_EXCEEDED": "检查执行记录，再调整预算或缩小修复目标后复跑。", "MODEL_INVOCATION_TIMEOUT": "检查模型服务连通性和超时设置后重试。", "AUTONOMOUS_REPAIR_FAILED": "查看最后一次工具日志和测试失败原因后复跑。"}
    return {"run_id": run.id, "status": run.status, "phase": run.phase, "current_tool": run.metrics.get("current_tool"), "current_goal": run.metrics.get("current_goal"), "elapsed_seconds": round(elapsed, 1), "budget": budget.model_dump(), "remaining": remaining, "latest_step": steps[-1].model_dump(mode="json") if steps else None, "failure_reason": run.error_summary, "recovery_hint": recommendations.get(run.error_summary or ""), "retry_available": run.status in {RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.BLOCKED}}


@router.get("/artifacts/{artifact_id}/download-info")
async def artifact_download_info(request: Request, artifact_id: str):
    import hashlib
    artifact = store.read_artifact(artifact_id)
    if artifact is None: raise HTTPException(404, "Artifact not found")
    require_run_access(request, store.read_run(artifact.run_id))
    if artifact.metadata.get("content_expired_at"): raise HTTPException(410, "ARTIFACT_CONTENT_EXPIRED")
    content = (artifact.content or "").encode("utf-8")
    return {"artifact_id": artifact.id, "filename": artifact.name, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(), "download_path": f"/api/v1/artifacts/{artifact.id}/download"}
