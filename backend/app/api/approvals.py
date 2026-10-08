from fastapi import APIRouter, HTTPException, Query, Request

from app.api.context import filter_by_workspace, require_approval_access, run_workspace_id
from app.domain.schemas import ApprovalDecisionRequest, ApprovalRequestResponse
from app.infra.store import store

router = APIRouter(tags=["approvals"])


@router.get("/approvals")
async def list_approvals(
    request: Request,
    run_id: str | None = None,
    status: str | None = None,
    tool_name: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    if run_id:
        from app.api.context import require_run_access

        require_run_access(request, store.get_run(run_id))
    items = store.list_approval_requests(
        run_id=run_id,
        status=status,
        tool_name=tool_name,
    )
    items = filter_by_workspace(request, items, lambda item: run_workspace_id(item.run_id))
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalRequestResponse)
async def decide_approval(
    request_context: Request,
    approval_id: str,
    request: ApprovalDecisionRequest,
) -> ApprovalRequestResponse:
    if request.decision not in {"approved", "denied"}:
        raise HTTPException(status_code=400, detail="Decision must be approved or denied")
    item = require_approval_access(
        request_context,
        store.approval_requests.get(approval_id),
    )
    item = store.decide_approval_request(
        approval_id=approval_id,
        decision=request.decision,
        decided_by=request.decided_by,
        reason=request.reason,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return item
