from fastapi import APIRouter, HTTPException, Query

from app.domain.schemas import (
    ExtensionHealthResponse,
    ExtensionManifest,
    ExtensionInvokeRequest,
    ExtensionInvokeResponse,
    ExtensionStatusRequest,
    HookDispatchRequest,
    HookDispatchResponse,
)
from app.infra.store import store
from app.services.extensions import invoke_mcp

router = APIRouter(tags=["extensions"])


@router.get("/extensions")
async def list_extensions(
    type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = store.list_extensions(extension_type=type, status=status)
    items = all_items[offset : offset + limit]
    health = {item.extension_id: item for item in store.list_extension_health()}
    return {"items": items, "total": len(all_items), "health": health}


@router.post("/extensions", response_model=ExtensionManifest)
async def upsert_extension(extension: ExtensionManifest) -> ExtensionManifest:
    return store.upsert_extension(extension)


@router.patch("/extensions/{extension_id}/status", response_model=ExtensionManifest)
async def update_extension_status(
    extension_id: str,
    request: ExtensionStatusRequest,
) -> ExtensionManifest:
    job = store.create_job(
        kind="extension_status",
        resource_id=extension_id,
        metadata={"status": request.status, "reason": request.reason},
    )
    store.update_job(job.id, "running")
    extension = store.update_extension_status(extension_id, request.status)
    if extension is None:
        store.update_job(job.id, "failed", "Extension not found")
        raise HTTPException(status_code=404, detail="Extension not found")
    store.update_job(
        job.id,
        "completed",
        result_json={
            "extension_id": extension.id,
            "type": extension.type,
            "status": extension.status,
            "reason": request.reason,
        },
    )
    return extension


@router.post("/extensions/{extension_id}/invoke", response_model=ExtensionInvokeResponse)
async def invoke_extension(extension_id: str, request: ExtensionInvokeRequest) -> ExtensionInvokeResponse:
    job = store.create_job(
        kind="extension_invoke",
        resource_id=extension_id,
        metadata={"action": request.action, "input_keys": sorted(request.input)},
    )
    store.update_job(job.id, "running")
    extension = store.extension_manifests.get(extension_id)
    if extension and extension.type == "mcp_tool" and extension.id != "mcp_local_git":
        try:
            if extension.status != "enabled":
                raise ValueError("EXTENSION_DISABLED")
            output = await invoke_mcp(extension.config, request.action, request.input)
            status = "failed" if output.get("isError") else "completed"
        except Exception as exc:
            output, status = {"reason": str(exc) if isinstance(exc, ValueError) else "MCP_INVOCATION_FAILED"}, "failed"
        response = ExtensionInvokeResponse(extension_id=extension_id, type=extension.type, action=request.action, status=status, output=output, job_id=job.id)
        store.add_audit_log(action="extension.invoke", resource_type="extension", resource_id=extension_id, decision=status, detail_json={"action": request.action, "job_id": job.id})
    else:
        response = store.invoke_extension(extension_id, request.action, request.input, job_id=job.id)
    if response is None:
        store.update_job(job.id, "failed", "Extension not found")
        raise HTTPException(status_code=404, detail="Extension not found")
    error_summary = None
    if response.status not in {"completed", "blocked"}:
        error_summary = str(response.output.get("reason") or response.status)
    store.update_job(
        job.id,
        response.status,
        error_summary,
        result_json={
            "extension_id": response.extension_id,
            "type": response.type,
            "action": response.action,
            "status": response.status,
            "job_id": response.job_id,
            "output": response.output,
        },
    )
    return response


@router.get("/extensions/{extension_id}/health", response_model=ExtensionHealthResponse)
async def check_extension_health(extension_id: str) -> ExtensionHealthResponse:
    job = store.create_job(kind="extension_health", resource_id=extension_id)
    store.update_job(job.id, "running")
    health = store.check_extension_health(extension_id)
    if health is None:
        store.update_job(job.id, "failed", "Extension not found")
        raise HTTPException(status_code=404, detail="Extension not found")
    store.update_job(
        job.id,
        "completed" if health.healthy else "failed",
        None if health.healthy else health.reason,
        result_json=health.model_dump(mode="json"),
    )
    return health


@router.post("/extensions/hooks/dispatch", response_model=HookDispatchResponse)
async def dispatch_hook(request: HookDispatchRequest) -> HookDispatchResponse:
    job = store.create_job(
        kind="hook_dispatch",
        resource_id=request.event_type,
        metadata={"payload_keys": sorted(request.payload)},
    )
    store.update_job(job.id, "running")
    delivered, skipped, targets, record = store.dispatch_hook(
        request.event_type,
        request.payload,
        job_id=job.id,
    )
    store.update_job(
        job.id,
        "completed",
        result_json={
            "record_id": record.id,
            "event_type": request.event_type,
            "delivered": delivered,
            "skipped": skipped,
            "targets": targets,
        },
    )
    return HookDispatchResponse(
        event_type=request.event_type,
        delivered=delivered,
        skipped=skipped,
        targets=targets,
        job_id=job.id,
        record_id=record.id,
    )


@router.get("/extensions/hooks/dispatches")
async def list_hook_dispatches(
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    items = store.list_hook_dispatch_records(event_type=event_type)
    return {"items": items[offset : offset + limit], "total": len(items)}
