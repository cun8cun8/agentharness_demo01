from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.services.event_bus import event_publisher

router = APIRouter(tags=["events"])


@router.get("/events/stream")
async def stream_events(request: Request) -> StreamingResponse:
    snapshot = event_publisher.snapshot()
    if snapshot["backend"] not in {"redis", "kafka", "redpanda"}:
        raise HTTPException(status_code=503, detail="跨进程事件流需要启用 Redis 事件总线。")
    workspace_id = getattr(request.state, "workspace_id", None)
    is_admin = bool(getattr(request.state, "is_admin", False))

    async def event_stream():
        try:
            async for raw in event_publisher.stream():
                if await request.is_disconnected():
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                payload = message.get("payload") if isinstance(message, dict) else None
                if not is_admin and (not isinstance(payload, dict) or payload.get("workspace_id") != workspace_id):
                    continue
                yield "event: trace.event\n"
                yield f"data: {json.dumps(message, ensure_ascii=False)}\n\n"
        finally:
            return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
