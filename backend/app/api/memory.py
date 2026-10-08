from fastapi import APIRouter, HTTPException, Query, Request

from app.api.context import (
    require_memory_access,
    require_run_access,
    require_task_access,
    workspace_id_for_request,
)
from app.domain.schemas import (
    CreateMemoryItemRequest,
    MemoryItemResponse,
    UpdateMemoryItemRequest,
)
from app.infra.store import store

router = APIRouter(tags=["memory"])


def _validate_memory_item_refs(
    task_id: str | None,
    agent_run_id: str | None,
    request_context: Request,
) -> None:
    task = store.get_task(task_id) if task_id else None
    if task_id and task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task is not None:
        require_task_access(request_context, task)
    run = store.get_run(agent_run_id) if agent_run_id else None
    if agent_run_id and run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    if run is not None:
        require_run_access(request_context, run)
    if task and run and task.id != run.task_id:
        raise HTTPException(status_code=400, detail="Memory references must belong to the same task")


@router.post("/memory/items", response_model=MemoryItemResponse)
async def create_memory_item(
    request: CreateMemoryItemRequest,
    request_context: Request,
) -> MemoryItemResponse:
    workspace_id = workspace_id_for_request(request_context, request.workspace_id)
    request = request.model_copy(update={"workspace_id": workspace_id})
    _validate_memory_item_refs(request.task_id, request.agent_run_id, request_context)
    try:
        return store.create_memory_item(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/memory/items/{item_id}", response_model=MemoryItemResponse)
async def update_memory_item(
    request_context: Request,
    item_id: str,
    request: UpdateMemoryItemRequest,
) -> MemoryItemResponse:
    item = require_memory_access(request_context, store.get_memory_item(item_id))
    workspace_id = workspace_id_for_request(
        request_context,
        request.workspace_id or item.workspace_id,
    )
    request = request.model_copy(update={"workspace_id": workspace_id})
    _validate_memory_item_refs(request.task_id, request.agent_run_id, request_context)
    try:
        item = store.update_memory_item(item_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item


@router.get("/memory/items/{item_id}", response_model=MemoryItemResponse)
async def get_memory_item(request: Request, item_id: str) -> MemoryItemResponse:
    return require_memory_access(request, store.get_memory_item(item_id))


@router.get("/memory/items")
async def list_memory_items(
    request: Request,
    task_id: str | None = None,
    agent_run_id: str | None = None,
    memory_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    if task_id:
        require_task_access(request, store.get_task(task_id))
    if agent_run_id:
        require_run_access(request, store.get_run(agent_run_id))
    items = store.list_memory_items(
        task_id=task_id,
        agent_run_id=agent_run_id,
        memory_type=memory_type,
        status=status,
        workspace_id=workspace_id,
    )
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.get("/memory/recommendations")
async def recommend_memory_items(
    request: Request,
    task_id: str | None = None,
    query: str | None = None,
    memory_type: str | None = None,
    status: str | None = "active",
    limit: int = Query(default=6, ge=1, le=100),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    if task_id:
        require_task_access(request, store.get_task(task_id))
    items = store.recommend_memory_items(
        task_id=task_id,
        query=query,
        memory_types=[memory_type] if memory_type else None,
        status=status,
        limit=limit,
        workspace_id=workspace_id,
    )
    return {"items": items, "total": len(items)}
