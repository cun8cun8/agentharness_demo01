from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.context import require_workspace_access
from app.infra.store import store
from app.services import knowledge

router = APIRouter(tags=["knowledge"])


class IndexRequest(BaseModel):
    workspace_id: str = "workspace_default"
    graph: bool = False


@router.post("/knowledge/index")
def index(request: Request, body: IndexRequest):
    require_workspace_access(request, body.workspace_id)
    try:
        return knowledge.rebuild_index(store, body.workspace_id, body.graph)
    except Exception as exc:
        raise HTTPException(503, str(exc) if isinstance(exc, ValueError) else "KNOWLEDGE_INDEX_FAILED") from exc


@router.get("/knowledge/search")
def search(request: Request, q: str = Query(min_length=1, max_length=2000), workspace_id: str = "workspace_default", limit: int = Query(default=10, ge=1, le=50)):
    require_workspace_access(request, workspace_id)
    try:
        return {"items": knowledge.search(store, workspace_id, q, limit)}
    except Exception as exc:
        raise HTTPException(503, str(exc) if isinstance(exc, ValueError) else "KNOWLEDGE_SEARCH_FAILED") from exc


@router.get("/knowledge/graph")
def graph(request: Request, workspace_id: str = "workspace_default"):
    require_workspace_access(request, workspace_id)
    try:
        return knowledge.graph(store, workspace_id)
    except Exception as exc:
        raise HTTPException(503, "KNOWLEDGE_GRAPH_UNAVAILABLE") from exc
