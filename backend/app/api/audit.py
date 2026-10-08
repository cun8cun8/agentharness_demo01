import csv
import json
from io import StringIO

from fastapi import APIRouter, Query, Request, Response

from app.api.context import audit_log_workspace_id, filter_by_workspace
from app.domain.schemas import AuditLogResponse
from app.infra.store import store

router = APIRouter(tags=["audit"])


def _filtered_audit_logs(
    request: Request,
    query: str | None = None,
    *,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    decision: str | None = None,
) -> list[AuditLogResponse]:
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
    return items


@router.get("/audit-logs/export.csv")
async def export_audit_logs(
    request: Request,
    query: str | None = None,
    actor_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    decision: str | None = None,
    limit: int = Query(default=10_000, ge=1, le=10_000),
) -> Response:
    """Export the filtered audit view without exposing secrets outside details."""
    rows = _filtered_audit_logs(
        request,
        query,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
    )[:limit]
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        ["audit_id", "actor_id", "action", "resource_type", "resource_id", "decision", "created_at", "detail_json"]
    )
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.actor_id,
                row.action,
                row.resource_type,
                row.resource_id,
                row.decision,
                row.created_at.isoformat(),
                json.dumps(row.detail_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ]
        )
    return Response(
        content=output.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=researchforge-audit-logs.csv"},
    )


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
    items = _filtered_audit_logs(
        request,
        query,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
    )
    return {"items": items[offset : offset + limit], "total": len(items)}
