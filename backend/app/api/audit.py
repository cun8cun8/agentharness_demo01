import json

from fastapi import APIRouter, Query, Request

from app.api.context import audit_log_workspace_id, filter_by_workspace
from app.domain.schemas import AuditLogResponse
from app.infra.store import store

router = APIRouter(tags=["audit"])


@router.get("/audit-logs")
async def list_audit_logs(
    request: Request,
    query: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    decision: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    items = store.list_audit_logs(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
    )
    items = filter_by_workspace(request, items, audit_log_workspace_id)
    if query:
        needle = query.casefold().strip()
        items = [
            item
            for item in items
            if needle
            in " ".join(
                [
                    item.id,
                    item.actor_id or "",
                    item.action,
                    item.resource_type,
                    item.resource_id,
                    item.decision or "",
                    json.dumps(item.detail_json, ensure_ascii=False, sort_keys=True),
                ]
            ).casefold()
        ]
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
    }
