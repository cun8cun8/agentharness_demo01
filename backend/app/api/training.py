import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.context import current_user, require_workspace_access, workspace_id_for_request
from app.infra.store import store
from app.services import training
from app.services.training_controller import training_action
from app.services import canary
from app.services import online_rl

router = APIRouter(tags=["training"])


class OnlineRolloutInput(BaseModel):
    prompt: str | None = Field(default=None, max_length=50_000)
    reference_item_id: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("/training/online-rl/{session_id}/export")
async def export_online_rl(request: Request, session_id: str, payload: online_rl.OnlineRLExportRequest | None = None):
    session = _item(request, store.online_rl_sessions, session_id)
    try:
        return online_rl.export_dataset(store, session, payload or online_rl.OnlineRLExportRequest(), current_user(request).id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/training/online-rl")
async def online_rl_sessions(request: Request):
    workspace = workspace_id_for_request(request)
    items = [item for item in store.online_rl_sessions.values() if not workspace or item["workspace_id"] == workspace]
    return {"items": sorted((_public(item) for item in items), key=lambda item: item["created_at"], reverse=True), "total": len(items)}


@router.post("/training/online-rl", status_code=201)
async def create_online_rl(request: Request, payload: online_rl.OnlineRLRequest):
    require_workspace_access(request, payload.workspace_id)
    try:
        return _public(online_rl.create_session(store, payload, current_user(request).id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/training/online-rl/{session_id}/rollouts")
async def create_online_rl_rollout(request: Request, session_id: str, payload: OnlineRolloutInput | None = None):
    session = _item(request, store.online_rl_sessions, session_id)
    payload = payload or OnlineRolloutInput()
    try:
        result = await asyncio.to_thread(
            online_rl.rollout,
            store,
            session,
            current_user(request).id,
            payload.prompt,
            payload.reference_item_id,
        )
        return result
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/training/online-rl/{session_id}/stop")
async def stop_online_rl(request: Request, session_id: str):
    session = _item(request, store.online_rl_sessions, session_id)
    return online_rl.stop_session(store, session, current_user(request).id)


@router.get("/training/online-rl/{session_id}")
async def get_online_rl(request: Request, session_id: str):
    return _public(_item(request, store.online_rl_sessions, session_id))


@router.get("/model-registry/canaries")
async def canaries(request: Request):
    workspace = workspace_id_for_request(request)
    items = [item for item in store.model_canaries.values() if not workspace or item["workspace_id"] == workspace]
    return {"items": sorted(items, key=lambda item: item["created_at"], reverse=True), "total": len(items)}


@router.post("/model-registry/canaries", status_code=201)
async def create_canary(request: Request, body: canary.CanaryRequest):
    _item(request, store.model_versions, body.version_id)
    try:
        return canary.create_canary(store, body, current_user(request).id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/model-registry/canaries/{canary_id}/{action}")
async def canary_action(request: Request, canary_id: str, action: str):
    record = _item(request, store.model_canaries, canary_id)
    handler = {"promote": canary.promote_canary, "rollback": canary.stop_canary}.get(action)
    if not handler:
        raise HTTPException(404, "ACTION_NOT_FOUND")
    try:
        return handler(store, record, current_user(request).id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/training/controller")
async def controller_status():
    from app.main import training_controller
    return training_controller.status()


def _item(request, collection, item_id):
    item = collection.get(item_id)
    if not item:
        raise HTTPException(404, "NOT_FOUND")
    require_workspace_access(request, item["workspace_id"])
    return item


def _public(record):
    return {key: value for key, value in record.items() if key not in {"base_path", "artifact_path"}}


@router.get("/training/jobs")
async def list_jobs(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    workspace = workspace_id_for_request(request)
    items = [item for item in store.training_jobs.values() if not workspace or item["workspace_id"] == workspace]
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return {"items": [_public(item) for item in items[offset:offset+limit]], "total": len(items)}


@router.post("/training/jobs", status_code=201)
async def prepare(request: Request, payload: training.TrainingRequest):
    require_workspace_access(request, payload.workspace_id)
    try:
        return _public(training.prepare_training(store, payload, current_user(request).id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/training/jobs/{job_id}/kubernetes-manifest")
async def kubernetes_manifest(request: Request, job_id: str):
    record = _item(request, store.training_jobs, job_id)
    try:
        if str(record.get("backend") or training.training_backend()) not in {"kubernetes", "k8s"}:
            raise ValueError("TRAINING_KUBERNETES_BACKEND_REQUIRED")
        return training.build_kubernetes_manifest(record)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/training/jobs/{job_id}")
async def get_job(request: Request, job_id: str):
    return _public(_item(request, store.training_jobs, job_id))


@router.post("/training/jobs/{job_id}/{action}")
async def job_action(request: Request, job_id: str, action: str):
    if action not in {"start", "enqueue", "refresh", "cancel"}:
        raise HTTPException(404, "ACTION_NOT_FOUND")
    record = _item(request, store.training_jobs, job_id)
    try:
        result = await training_action(store, record["id"], action)
        store.add_audit_log(action="training." + action, resource_type="training_job", resource_id=job_id, actor_id=current_user(request).id, decision="allow")
        return _public(result)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "TRAINING_BACKEND_UNAVAILABLE") from exc


@router.get("/training/jobs/{job_id}/logs")
async def logs(request: Request, job_id: str):
    record = _item(request, store.training_jobs, job_id)
    try:
        return {"log": await asyncio.to_thread(training.get_training_logs, record)}
    except Exception as exc:
        raise HTTPException(503, "TRAINING_LOGS_UNAVAILABLE") from exc


@router.get("/model-registry/versions")
async def versions(request: Request, limit: int = Query(25, ge=1, le=200), offset: int = Query(0, ge=0)):
    workspace = workspace_id_for_request(request)
    items = sorted((item for item in store.model_versions.values() if not workspace or item["workspace_id"] == workspace), key=lambda item: item["created_at"], reverse=True)
    return {"items": [_public(item) for item in items[offset:offset+limit]], "total": len(items)}


@router.get("/model-registry/versions/{version_id}")
async def version(request: Request, version_id: str):
    return _public(_item(request, store.model_versions, version_id))


@router.get("/model-registry/deployments")
async def deployments(request: Request):
    workspace = workspace_id_for_request(request)
    return {"items": [item for item in store.model_deployments.values() if not workspace or item["workspace_id"] == workspace]}


class Promotion(BaseModel):
    model_config_id: str
    evaluation_id: str


@router.post("/model-registry/versions/{version_id}/promote")
async def promote(request: Request, version_id: str, payload: Promotion):
    try:
        return training.promote_model(store, _item(request, store.model_versions, version_id), payload.model_config_id, payload.evaluation_id, current_user(request).id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/model-registry/deployments/{deployment_id}/rollback")
async def rollback(request: Request, deployment_id: str):
    try:
        return training.rollback_model(store, _item(request, store.model_deployments, deployment_id), current_user(request).id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
