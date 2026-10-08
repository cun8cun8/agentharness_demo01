from fastapi import APIRouter, HTTPException, Query, Request

from app.api.context import require_task_access, workspace_id_for_request
from app.domain.schemas import CreateTaskRequest, TaskResponse, TaskSummary, UpdateTaskRequest
from app.infra.store import WorkspaceQuotaExceeded, store
from app.tools.path_utils import resolve_repo_child, resolve_repo_path
from app.infra.queries import page_records

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=TaskResponse)
async def create_task(request: CreateTaskRequest, request_context: Request) -> TaskResponse:
    request = request.model_copy(
        update={"workspace_id": workspace_id_for_request(request_context, request.workspace_id)}
    )
    try:
        return store.create_task(request)
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: str,
    request: UpdateTaskRequest,
    request_context: Request,
) -> TaskResponse:
    task = require_task_access(request_context, store.get_task(task_id))
    changes = request.model_dump(exclude_unset=True)
    workspace_id = changes.get("workspace_id")
    if workspace_id is not None and workspace_id not in store.workspaces:
        raise HTTPException(status_code=400, detail="Workspace not found")
    if workspace_id is not None:
        workspace_id_for_request(request_context, workspace_id)
    for key, value in changes.items():
        if value is not None:
            setattr(task, key, value)
    updated = store.update_task(task)
    store.add_audit_log(
        action="task.update",
        resource_type="task",
        resource_id=updated.id,
        decision="allow",
        actor_id="operator",
        detail_json={"changed_fields": sorted(changes)},
    )
    return updated


@router.get("/tasks", response_model=dict[str, object])
async def list_tasks(
    request: Request,
    type: str | None = None,
    status: str | None = None,
    workspace_id: str | None = None,
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request, workspace_id)
    page = page_records(store, "tasks", workspace_id=workspace_id, filters={"type": type, "status": status}, query=query, limit=limit, offset=offset)
    return {"items": [TaskSummary.from_task(TaskResponse.model_validate(item)) for item in page["items"]], "total": page["total"]}


@router.get("/tasks/stats")
async def task_stats(request: Request, workspace_id: str | None = None) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request, workspace_id)
    tasks = store.list_tasks(workspace_id=workspace_id)
    task_ids = {task.id for task in tasks}
    runs = [run for run in store.runs.values() if run.task_id in task_ids]
    completed = [task for task in tasks if task.status == "completed"]
    failed = [task for task in tasks if task.status == "failed"]
    running = [task for task in tasks if task.status == "running"]
    paused = [task for task in tasks if task.status == "paused"]
    avg_duration_ms = round(sum(run.duration_ms for run in runs) / len(runs), 2) if runs else 0
    avg_cost = round(sum(run.total_cost for run in runs) / len(runs), 4) if runs else 0
    return {
        "task_count": len(tasks),
        "run_count": len(runs),
        "completed_count": len(completed),
        "failed_count": len(failed),
        "running_count": len(running),
        "paused_count": len(paused),
        "success_rate": round(len(completed) / len(tasks), 4) if tasks else 0,
        "avg_duration_ms": avg_duration_ms,
        "avg_cost": avg_cost,
    }


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(request: Request, task_id: str) -> TaskResponse:
    return require_task_access(request, store.get_task(task_id))


@router.get("/tasks/{task_id}/context")
async def get_task_context(request: Request, task_id: str) -> dict[str, object]:
    task = require_task_access(request, store.get_task(task_id))
    runs = [run for run in store.runs.values() if run.task_id == task_id]
    runs = sorted(runs, key=lambda item: item.created_at, reverse=True)
    run_ids = {run.id for run in runs}
    jobs = [
        job
        for job in store.jobs.values()
        if job.task_id == task_id or job.resource_id in run_ids
    ]
    jobs = sorted(jobs, key=lambda item: item.created_at, reverse=True)
    trace_items = [
        item
        for item in store.list_trace_dataset_items()
        if item.agent_run_id in run_ids
    ]
    preference_pairs = store.list_preference_pairs(task_id=task_id)
    return {
        "task": task,
        "runs": runs,
        "latest_run": runs[0] if runs else None,
        "jobs": jobs,
        "memory_items": store.list_memory_items(task_id=task_id),
        "memory_recommendations": store.recommend_memory_items(
            task_id=task_id,
            status="active",
            limit=6,
        ),
        "trace_items": trace_items,
        "preference_pairs": preference_pairs,
    }


@router.get("/tasks/{task_id}/files")
async def list_task_files(
    request: Request,
    task_id: str,
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, object]:
    task = require_task_access(request, store.get_task(task_id))
    repo = resolve_repo_path(task.repo_path)
    if repo is None or not repo.exists() or not repo.is_dir():
        return {"task_id": task_id, "items": [], "total": 0, "truncated": False}

    ignored_parts = {".git", ".pytest_cache", "__pycache__"}
    files = []
    total_count = 0
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo)
        if any(part in ignored_parts for part in relative.parts):
            continue
        if path.is_symlink():
            # Symlinks can point outside the task repository; never expose them in the file browser.
            try:
                path.resolve().relative_to(repo.resolve())
            except ValueError:
                continue
            continue
        try:
            path.resolve().relative_to(repo.resolve())
        except ValueError:
            continue
        relative_path = relative.as_posix()
        total_count += 1
        if len(files) < limit:
            files.append(
                {
                    "path": relative_path,
                    "kind": "directory" if path.is_dir() else "file",
                    "size_bytes": path.stat().st_size if path.is_file() else 0,
                }
            )
    return {
        "task_id": task_id,
        "items": files,
        "total": total_count,
        "truncated": total_count > len(files),
    }


@router.get("/tasks/{task_id}/files/{file_path:path}")
async def read_task_file(request: Request, task_id: str, file_path: str) -> dict[str, object]:
    task = require_task_access(request, store.get_task(task_id))
    try:
        path = resolve_repo_child(task.repo_path, file_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Unable to read file") from exc
    truncated = len(content) > 200_000
    return {
        "task_id": task_id,
        "path": path.relative_to(resolve_repo_path(task.repo_path)).as_posix(),
        "content": content[:200_000],
        "truncated": truncated,
    }
