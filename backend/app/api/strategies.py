from fastapi import APIRouter, HTTPException, Query

from app.domain.schemas import AgentStrategy, StrategyLifecycleRequest, StrategyLifecycleResponse
from app.infra.store import store

router = APIRouter(tags=["strategies"])


@router.get("/strategies")
async def list_strategies(
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    all_items = store.list_strategies()
    return {"items": all_items[offset : offset + limit], "total": len(all_items)}


@router.post("/strategies", response_model=AgentStrategy)
async def create_strategy(strategy: AgentStrategy) -> AgentStrategy:
    return store.upsert_strategy(strategy)


@router.get("/strategies/{strategy_id}", response_model=AgentStrategy)
async def get_strategy(strategy_id: str) -> AgentStrategy:
    strategy = store.get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategy


@router.post("/strategies/{strategy_id}/candidate", response_model=StrategyLifecycleResponse)
async def mark_strategy_candidate(
    strategy_id: str,
    request: StrategyLifecycleRequest | None = None,
) -> StrategyLifecycleResponse:
    return _transition_strategy(
        strategy_id=strategy_id,
        action="strategy.candidate",
        next_status="candidate",
        request=request or StrategyLifecycleRequest(),
    )


@router.post("/strategies/{strategy_id}/promote", response_model=StrategyLifecycleResponse)
async def promote_strategy(
    strategy_id: str,
    request: StrategyLifecycleRequest | None = None,
) -> StrategyLifecycleResponse:
    request = request or StrategyLifecycleRequest()
    if request.evaluation_run_id:
        evaluation = store.get_evaluation_run(request.evaluation_run_id)
        if evaluation is None:
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        if evaluation.agent_strategy_id != strategy_id:
            raise HTTPException(status_code=400, detail="Evaluation strategy does not match")
        gates = [
            gate
            for gate in store.list_release_gates()
            if gate.evaluation_run_id == request.evaluation_run_id
        ]
        latest_gate = gates[0] if gates else None
        if latest_gate is None or not latest_gate.passed:
            raise HTTPException(status_code=400, detail="A passed release gate is required")
    return _transition_strategy(
        strategy_id=strategy_id,
        action="strategy.promote",
        next_status="active",
        request=request,
    )


@router.post("/strategies/{strategy_id}/rollback", response_model=StrategyLifecycleResponse)
async def rollback_strategy(
    strategy_id: str,
    request: StrategyLifecycleRequest | None = None,
) -> StrategyLifecycleResponse:
    return _transition_strategy(
        strategy_id=strategy_id,
        action="strategy.rollback",
        next_status="rolled_back",
        request=request or StrategyLifecycleRequest(),
    )


def _transition_strategy(
    strategy_id: str,
    action: str,
    next_status: str,
    request: StrategyLifecycleRequest,
) -> StrategyLifecycleResponse:
    job = store.create_job(
        kind="strategy_lifecycle",
        resource_id=strategy_id,
        metadata={
            "action": action,
            "next_status": next_status,
            "evaluation_run_id": request.evaluation_run_id,
            "reason": request.reason,
        },
    )
    store.update_job(job.id, "running")
    strategy = store.get_strategy(strategy_id)
    if strategy is None:
        store.update_job(job.id, "failed", "Strategy not found")
        raise HTTPException(status_code=404, detail="Strategy not found")
    previous_status = strategy.status
    strategy.status = next_status
    saved = store.upsert_strategy(strategy)
    store.add_audit_log(
        action=action,
        resource_type="agent_strategy",
        resource_id=strategy_id,
        decision=next_status,
        actor_id="operator",
        detail_json={
            "previous_status": previous_status,
            "status": next_status,
            "evaluation_run_id": request.evaluation_run_id,
            "reason": request.reason,
            "job_id": job.id,
        },
    )
    store.update_job(
        job.id,
        "completed",
        result_json={
            "strategy_id": strategy_id,
            "action": action,
            "previous_status": previous_status,
            "status": next_status,
            "evaluation_run_id": request.evaluation_run_id,
        },
    )
    return StrategyLifecycleResponse(
        strategy=saved,
        action=action,
        previous_status=previous_status,
        status=next_status,
        evaluation_run_id=request.evaluation_run_id,
        job_id=job.id,
    )
