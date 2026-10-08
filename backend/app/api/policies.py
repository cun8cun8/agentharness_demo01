from fastapi import APIRouter, HTTPException, Query

from app.domain.schemas import PolicyVersion
from app.infra.store import store

router = APIRouter(tags=["policies"])


@router.get("/policies")
async def list_policies(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = store.list_policies()
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.post("/policies", response_model=PolicyVersion)
async def create_policy(policy: PolicyVersion) -> PolicyVersion:
    return store.upsert_policy(policy)


@router.get("/policies/{policy_id}", response_model=PolicyVersion)
async def get_policy(policy_id: str) -> PolicyVersion:
    policy = store.get_policy(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy
