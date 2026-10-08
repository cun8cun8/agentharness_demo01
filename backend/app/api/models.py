import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.domain.schemas import (
    ModelInvokeRequest,
    ModelInvokeResponse,
    ModelHealthResponse,
    ModelBillingReconciliationResponse,
    ModelBillingImportRequest,
    ModelBillingImportPullRequest,
    ModelBillingImportResponse,
    ModelBillingStatementRequest,
    ModelProviderConfig,
    ModelRouteRequest,
    ModelRouteResponse,
    RunStatus,
    TaskType,
)
from app.config import get_settings
from app.infra.store import store
from app.api.context import current_user, is_admin, workspace_id_for_request
from app.services.model_gateway import invoke_configured_model, model_health
from app.services.secrets import resolve_secret
from app.services.billing_import import BillingImportValidationError, import_model_billing, pull_configured_model_billing

router = APIRouter(tags=["models"])


def _bridge_token(settings) -> str | None:
    try:
        return resolve_secret(settings.agent_bridge_token_env) or None
    except Exception:
        return None


def _bridge_run_id(request: Request) -> str | None:
    # OpenAI clients preserve organization headers while custom headers vary by SDK.
    return (
        request.headers.get("x-researchforge-run-id")
        or request.headers.get("openai-organization")
        or request.headers.get("x-run-id")
    )


def _message_text(messages: object, role: str) -> str:
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for item in messages:
        if not isinstance(item, dict) or str(item.get("role") or "") != role:
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") in {None, "text"}
            )
    return "\n\n".join(item for item in parts if item).strip()


def _bridge_openai_response(response, *, run_id: str, stream: bool) -> dict[str, object]:
    response_id = f"chatcmpl-rf-{run_id}-{int(time.time() * 1000)}"
    usage = {
        "prompt_tokens": int(response.usage.get("prompt_tokens") or 0),
        "completion_tokens": int(response.usage.get("completion_tokens") or 0),
        "total_tokens": int(response.usage.get("total_tokens") or 0),
    }
    if stream:
        # The client receives a valid OpenAI SSE envelope; the bridge itself has
        # already persisted the complete usage record before streaming begins.
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": response.model_name,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": response.output_text}, "finish_reason": response.finish_reason}],
            "usage": usage,
        }
    return {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": response.model_name,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": response.output_text}, "finish_reason": response.finish_reason}],
        "usage": usage,
    }


@router.post("/models/bridge/chat/completions")
async def model_bridge_chat_completions(request: Request, payload: dict[str, Any]) -> object:
    """OpenAI-compatible model bridge for external coding-agent runtimes.

    The bridge is deliberately run-scoped: the caller must present the worker
    bridge token and a Run ID, so external runtimes cannot use the endpoint as a
    general-purpose model proxy or write usage to an unrelated workspace.
    """
    settings = get_settings()
    expected = _bridge_token(settings)
    authorization = request.headers.get("authorization", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "AGENT_MODEL_BRIDGE_UNAUTHORIZED")

    run_id = _bridge_run_id(request)
    run = store.get_run(run_id) if run_id else None
    if run is None:
        raise HTTPException(404, "AGENT_RUN_NOT_FOUND")
    task = store.get_task(run.task_id)
    if task is None:
        raise HTTPException(404, "AGENT_TASK_NOT_FOUND")
    if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.PAUSED}:
        raise HTTPException(409, "AGENT_RUN_NOT_ACTIVE")

    messages = payload.get("messages")
    prompt = _message_text(messages, "user")
    system_prompt = _message_text(messages, "system") or None
    if not prompt:
        raise HTTPException(422, "MODEL_BRIDGE_PROMPT_REQUIRED")
    try:
        requested_tokens = int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 1024)
    except (TypeError, ValueError):
        raise HTTPException(422, "MODEL_BRIDGE_MAX_TOKENS_INVALID") from None
    budget_max_tokens = int(getattr(getattr(task, "budget", None), "max_tokens", 32_000) or 32_000)
    max_tokens = max(1, min(requested_tokens, 32_000, budget_max_tokens))
    try:
        temperature = float(payload.get("temperature", 0.2))
    except (TypeError, ValueError):
        raise HTTPException(422, "MODEL_BRIDGE_TEMPERATURE_INVALID") from None
    model, _reason = store.select_model_config(task.type, requested_model=run.model_name, strategy_id=run.agent_strategy_id, strict=True)
    input_tokens = max(1, (len(prompt) + len(system_prompt or "")) // 4)
    input_rate = float(model.config.get("input_cost_per_1k_tokens", model.cost_per_1k_tokens))
    output_rate = float(model.config.get("output_cost_per_1k_tokens", model.cost_per_1k_tokens))
    projected_cost = round((input_tokens * input_rate + max_tokens * output_rate) / 1000, 6)
    try:
        store.ensure_workspace_model_budget(task.workspace_id, projected_cost)
        response = invoke_configured_model(
            model,
            ModelInvokeRequest(
                task_type=task.type if isinstance(task.type, TaskType) else TaskType(str(task.type)),
                requested_model=run.model_name,
                system_prompt=system_prompt,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=max(0.0, min(2.0, temperature)),
            ),
        )
        store.record_run_usage(run.id, int(response.usage.get("total_tokens") or 0), response.estimated_cost)
        usage_entry = store.record_model_usage(
            workspace_id=task.workspace_id,
            model=model,
            usage=response.usage,
            estimated_cost=response.estimated_cost,
            billable_cost=0.0 if response.fallback_used else response.estimated_cost,
            fallback_used=response.fallback_used,
            source="external_agent_bridge",
            reference_type="run",
            reference_id=run.id,
            actor_id=f"run:{run.id}",
        )
        store.add_audit_log(
            action="agent.model_bridge.invoke",
            resource_type="run",
            resource_id=run.id,
            decision="fallback" if response.fallback_used else "completed",
            actor_id=f"run:{run.id}",
            detail_json={
                "model_name": response.model_name,
                "provider": response.provider,
                "usage_entry_id": usage_entry.id,
                "usage": response.usage,
                "estimated_cost": response.estimated_cost,
                "fallback_used": response.fallback_used,
                "attempts": response.attempts,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        store.add_audit_log(
            action="agent.model_bridge.invoke",
            resource_type="run",
            resource_id=run.id,
            decision="failed",
            actor_id=f"run:{run.id}",
            detail_json={"error": str(exc)},
        )
        raise HTTPException(502, "AGENT_MODEL_BRIDGE_FAILED") from exc

    result = _bridge_openai_response(response, run_id=run.id, stream=bool(payload.get("stream")))
    if payload.get("stream"):
        chunks = [
            {"id": result["id"], "object": "chat.completion.chunk", "created": result["created"], "model": result["model"], "choices": [{"index": 0, "delta": {"role": "assistant", "content": response.output_text}, "finish_reason": None}]},
            {"id": result["id"], "object": "chat.completion.chunk", "created": result["created"], "model": result["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": response.finish_reason}]},
        ]
        body = "".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return StreamingResponse(iter([body]), media_type="text/event-stream")
    return result


@router.get("/models")
async def list_models(
    role: str | None = None,
    status: str | None = "active",
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = store.list_model_configs(role=role, status=status)
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.post("/models", response_model=ModelProviderConfig)
async def upsert_model(model: ModelProviderConfig, request: Request) -> ModelProviderConfig:
    if not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    return store.upsert_model_config(model)


@router.get("/models/health")
async def list_model_health(
    request: Request,
    role: str | None = None,
    status: str | None = "active",
    verify_connectivity: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    if verify_connectivity and not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    all_items = [
        model_health(model, verify_connectivity=verify_connectivity)
        for model in store.list_model_configs(role=role, status=status)
    ]
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.post("/models/route", response_model=ModelRouteResponse)
async def route_model(request: ModelRouteRequest) -> ModelRouteResponse:
    chosen, reason = _select_model_config(request)
    return ModelRouteResponse(
        provider=chosen.provider,
        model_name=chosen.model_name,
        reason=reason,
        context_window=chosen.context_window,
        estimated_cost=round(request.estimated_tokens / 1000 * chosen.cost_per_1k_tokens, 6),
    )


@router.get("/models/usage")
async def list_model_usage(
    request: Request,
    workspace_id: str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    scope = workspace_id_for_request(request, workspace_id) or current_user(request).workspace_id
    items = store.list_model_usage(scope, model_name=model_name, provider=provider)
    page = items[offset : offset + limit]
    total_tokens = sum(item.total_tokens for item in items)
    estimated_cost = round(sum(item.estimated_cost for item in items), 6)
    billable_cost = round(sum(item.billable_cost for item in items), 6)
    return {
        "items": page,
        "total": len(items),
        "summary": {
            "workspace_id": scope,
            "total_tokens": total_tokens,
            "estimated_cost": estimated_cost,
            "billable_cost": billable_cost,
            "fallback_count": sum(1 for item in items if item.fallback_used),
        },
    }


@router.get("/models/billing/reconciliations")
async def list_model_billing_reconciliations(
    request: Request,
    workspace_id: str | None = None,
    provider: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    scope = workspace_id_for_request(request, workspace_id) or current_user(request).workspace_id
    items = store.list_model_billing_reconciliations(scope, provider=provider)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/models/billing/reconciliations", response_model=ModelBillingReconciliationResponse, status_code=201)
async def reconcile_model_billing(request: Request, payload: ModelBillingStatementRequest) -> ModelBillingReconciliationResponse:
    if not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    payload.workspace_id = workspace_id_for_request(request, payload.workspace_id) or current_user(request).workspace_id
    try:
        return store.reconcile_model_billing(payload, current_user(request).id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/models/billing/imports")
async def list_model_billing_imports(
    request: Request,
    workspace_id: str | None = None,
    provider: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    scope = workspace_id_for_request(request, workspace_id) or current_user(request).workspace_id
    items = store.list_model_billing_imports(scope, provider=provider)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/models/billing/imports", response_model=ModelBillingImportResponse, status_code=201)
async def import_billing_statement(request: Request, payload: ModelBillingImportRequest) -> ModelBillingImportResponse:
    if not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    payload.workspace_id = workspace_id_for_request(request, payload.workspace_id) or current_user(request).workspace_id
    try:
        return import_model_billing(store, payload, current_user(request).id)
    except BillingImportValidationError as exc:
        raise HTTPException(422, {"code": str(exc), "issues": [issue.model_dump() for issue in exc.issues]}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/models/billing/imports/pull", response_model=ModelBillingImportResponse, status_code=201)
async def pull_billing_statement(request: Request, payload: ModelBillingImportPullRequest) -> ModelBillingImportResponse:
    if not is_admin(request):
        raise HTTPException(403, "ADMIN_REQUIRED")
    payload.workspace_id = workspace_id_for_request(request, payload.workspace_id) or current_user(request).workspace_id
    try:
        return pull_configured_model_billing(store, payload, current_user(request).id)
    except BillingImportValidationError as exc:
        raise HTTPException(422, {"code": str(exc), "issues": [issue.model_dump() for issue in exc.issues]}) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/models/invoke", response_model=ModelInvokeResponse)
async def invoke_model(body: ModelInvokeRequest, request: Request, workspace_id: str | None = None) -> ModelInvokeResponse:
    actor = current_user(request)
    if actor.role == "viewer":
        raise HTTPException(403, "MODEL_INVOKE_FORBIDDEN")
    scope = workspace_id_for_request(request, workspace_id) or actor.workspace_id
    chosen, _reason = _select_model_config(
        ModelRouteRequest(
            task_type=body.task_type,
            requested_model=body.requested_model,
            estimated_tokens=max(1, len(body.prompt) // 4 + body.max_tokens),
        )
    )
    store.ensure_workspace_model_budget(scope, _maximum_invocation_cost(chosen, body))
    job = store.create_job(
        kind="model_invocation",
        resource_id=chosen.model_name,
        metadata={
            "workspace_id": scope,
            "task_type": body.task_type.value,
            "requested_model": body.requested_model,
            "provider": chosen.provider,
        },
    )
    store.update_job(job.id, "running")
    try:
        response = invoke_configured_model(chosen, body)
    except Exception as exc:
        store.update_job(job.id, "failed", str(exc))
        raise
    decision = "fallback" if response.fallback_used else "completed"
    usage_entry = store.record_model_usage(
        workspace_id=scope,
        model=chosen,
        usage=response.usage,
        estimated_cost=response.estimated_cost,
        billable_cost=0.0 if response.fallback_used else response.estimated_cost,
        fallback_used=response.fallback_used,
        source="direct_api",
        reference_type="job",
        reference_id=job.id,
        actor_id=actor.id,
    )
    store.add_audit_log(
        action="model.invoke",
        resource_type="model",
        resource_id=response.model_name,
        decision=decision,
        actor_id=actor.id,
        detail_json={
            "job_id": job.id,
            "workspace_id": scope,
            "usage_entry_id": usage_entry.id,
            "task_type": body.task_type.value,
            "provider": response.provider,
            "usage": response.usage,
            "estimated_cost": response.estimated_cost,
            "fallback_used": response.fallback_used,
            "attempts": response.attempts,
            "fallback_reason": response.fallback_reason,
            "billable_cost": usage_entry.billable_cost,
        },
    )
    store.update_job(
        job.id,
        "completed",
        result_json={
            "provider": response.provider,
            "model_name": response.model_name,
            "finish_reason": response.finish_reason,
            "usage": response.usage,
            "estimated_cost": response.estimated_cost,
            "fallback_used": response.fallback_used,
            "attempts": response.attempts,
            "fallback_reason": response.fallback_reason,
            "usage_entry_id": usage_entry.id,
        },
    )
    return response


def _maximum_invocation_cost(model: ModelProviderConfig, request: ModelInvokeRequest) -> float:
    prompt_tokens = max(1, (len(request.prompt) + len(request.system_prompt or "")) // 4)
    input_rate = float(model.config.get("input_cost_per_1k_tokens", model.cost_per_1k_tokens))
    output_rate = float(model.config.get("output_cost_per_1k_tokens", model.cost_per_1k_tokens))
    return round((prompt_tokens * input_rate + max(0, request.max_tokens) * output_rate) / 1000, 6)


def _select_model_config(request: ModelRouteRequest) -> tuple[ModelProviderConfig, str]:
    try:
        return store.select_model_config(
            request.task_type,
            requested_model=request.requested_model,
            strategy_id=request.strategy_id,
            strict=True,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
