from fastapi import APIRouter, HTTPException, Request

from app.api.context import workspace_id_for_request
from app.domain.schemas import HealthDemoReviewRequest, HealthDemoSessionRequest
from app.infra.store import store
from app.services.health_demo import analyze_session, get_session, list_sessions, review_session

router = APIRouter(tags=["health-demo"])


@router.post("/health-demo/sessions")
async def create_health_demo_session(request: Request, body: HealthDemoSessionRequest) -> dict[str, object]:
    workspace_id_for_request(request, None)
    result = analyze_session(body.user_id, body.signals, body.multimodal_text)
    store.add_audit_log(
        action="health_demo.analyze",
        resource_type="health_demo_session",
        resource_id=str(result["id"]),
        decision="completed",
        actor_id=getattr(request.state, "user_id", "operator"),
        detail_json={"risk_level": result["risk_level"], "requires_human_review": result["requires_human_review"]},
    )
    return result


@router.get("/health-demo/sessions")
async def get_health_demo_sessions(request: Request, user_id: str | None = None) -> dict[str, object]:
    workspace_id_for_request(request, None)
    return {"items": list_sessions(user_id)}


@router.get("/health-demo/sessions/{session_id}")
async def get_health_demo_session(request: Request, session_id: str) -> dict[str, object]:
    workspace_id_for_request(request, None)
    record = get_session(session_id)
    if record is None:
        raise HTTPException(404, "HEALTH_DEMO_SESSION_NOT_FOUND")
    return record


@router.post("/health-demo/sessions/{session_id}/review")
async def review_health_demo_session(request: Request, session_id: str, body: HealthDemoReviewRequest) -> dict[str, object]:
    workspace_id_for_request(request, None)
    record = review_session(session_id, body.decision, body.notes)
    if record is None:
        raise HTTPException(404, "HEALTH_DEMO_SESSION_NOT_FOUND")
    store.add_audit_log(
        action="health_demo.review",
        resource_type="health_demo_session",
        resource_id=session_id,
        decision=body.decision,
        actor_id=getattr(request.state, "user_id", "operator"),
        detail_json={"notes": body.notes[:500]},
    )
    return record
