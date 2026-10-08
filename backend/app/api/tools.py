from fastapi import APIRouter, HTTPException, Query, Request

from app.api.context import require_task_access
from app.domain.schemas import ToolPolicyPreviewRequest, ToolPolicyPreviewResponse
from app.infra.store import store
from app.policy.engine import default_policy_engine
from app.tools.registry import default_registry

router = APIRouter(tags=["tools"])


@router.get("/tools")
async def list_tools(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = [tool.manifest() for tool in default_registry.list_tools()]
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.post(
    "/tools/{tool_name}/policy-preview",
    response_model=ToolPolicyPreviewResponse,
)
async def preview_tool_policy(
    tool_name: str,
    request: ToolPolicyPreviewRequest,
    request_context: Request,
) -> ToolPolicyPreviewResponse:
    if not any(tool.name == tool_name for tool in default_registry.list_tools()):
        raise HTTPException(status_code=404, detail="Tool not found")
    policy = store.get_policy(request.policy_version_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    repo_path = request.repo_path
    if request.task_id:
        task = require_task_access(request_context, store.get_task(request.task_id))
        repo_path = repo_path or task.repo_path
    decision = default_policy_engine.evaluate_tool(
        tool_name=tool_name,
        input_data=request.input,
        policy=policy,
        repo_path=repo_path,
    )
    store.add_audit_log(
        action="tool.policy_preview",
        resource_type="tool",
        resource_id=tool_name,
        decision="allow" if decision.allowed else "deny",
        actor_id="operator",
        detail_json={
            "task_id": request.task_id,
            "policy_version_id": request.policy_version_id,
            "repo_path": repo_path,
            "input_keys": sorted(request.input),
            "reason": decision.reason,
            "requires_approval": decision.requires_approval,
        },
    )
    return ToolPolicyPreviewResponse(
        tool_name=tool_name,
        allowed=decision.allowed,
        requires_approval=decision.requires_approval,
        reason=decision.reason,
        policy_version_id=request.policy_version_id,
        repo_path=repo_path,
    )
