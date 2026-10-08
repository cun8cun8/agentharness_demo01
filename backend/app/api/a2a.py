"""Bounded internal A2A-style delegation for Coding Agent runs.

The bridge deliberately delegates into the existing Task/Run contract instead
of letting a child process bypass policy, budget, audit, or repository checks.
It can be fronted by a standards-compliant A2A gateway later without changing
the persisted execution model.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from app.api.context import current_user, require_run_access, require_task_access, workspace_id_for_request
from app.api.runs import _execute_run_job
from app.config import get_settings
from app.domain.schemas import A2ADelegationRequest, Budget, CreateTaskRequest, RunStatus, TaskType
from app.infra.store import WorkspaceQuotaExceeded, store
from app.services.job_queue import job_queue
from app.services.runtime_adapters import validate_runtime_adapter

router = APIRouter(tags=["a2a"])
_EXECUTION_BACKENDS = frozenset(
    {"native", "langgraph", "openhands", "openhands_cli", "mini_swe_agent"}
)


def _bounded_budget(requested: Budget | None, parent: Budget) -> Budget:
    """Never let a delegated run expand the parent's resource authority."""
    candidate = requested or parent
    return Budget(
        max_steps=min(candidate.max_steps, parent.max_steps),
        max_runtime_seconds=min(candidate.max_runtime_seconds, parent.max_runtime_seconds),
        max_tokens=min(candidate.max_tokens, parent.max_tokens),
        max_model_cost=min(candidate.max_model_cost, parent.max_model_cost),
        max_tool_calls=min(candidate.max_tool_calls, parent.max_tool_calls),
    )


def _delegation_metadata(task: Any) -> dict[str, Any] | None:
    config = getattr(task, "execution_config", {})
    if not isinstance(config, dict):
        return None
    metadata = config.get("a2a")
    return dict(metadata) if isinstance(metadata, dict) else None


def _delegation_depth(task: Any) -> int:
    """Treat malformed persisted metadata conservatively as a first-level child."""
    metadata = _delegation_metadata(task)
    if metadata is None:
        return 0
    try:
        return max(1, int(metadata.get("depth", 1)))
    except (TypeError, ValueError):
        return 1


def _response(child_task: Any, child_run: Any, job: Any) -> dict[str, Any]:
    metadata = _delegation_metadata(child_task) or {}
    return {
        "id": child_run.id,
        "parent_run_id": metadata.get("parent_run_id"),
        "parent_task_id": metadata.get("parent_task_id"),
        "child_task": child_task,
        "child_run": child_run,
        "job": job,
        "delegation": metadata,
    }


@router.get("/a2a/agent-card")
async def agent_card() -> dict[str, object]:
    """Expose the bridge capabilities without leaking deployment credentials."""
    return {
        "id": "researchforge-coding-harness",
        "name": "ResearchForge Coding Agent Harness",
        "version": "1.0",
        "url": "/api/v1/a2a",
        "capabilities": {
            "delegation": True,
            "streaming": False,
            "push_notifications": False,
            "max_delegation_depth": get_settings().agent_max_delegation_depth,
        },
        "skills": [
            {
                "id": "coding-repair-delegation",
                "name": "受约束代码修复委派",
                "description": "将子任务交给同一工作区内受策略、预算、沙箱和审计约束的 Agent Run。",
            }
        ],
        "authentication": "ResearchForge session or API key",
    }


@router.post("/a2a/delegations")
async def create_delegation(
    body: A2ADelegationRequest,
    background_tasks: BackgroundTasks,
    request: Request,
) -> dict[str, Any]:
    parent_run = require_run_access(request, store.get_run(body.parent_run_id))
    if parent_run.status == RunStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="PARENT_RUN_CANCELLED")
    parent_task = require_task_access(request, store.get_task(parent_run.task_id))
    child_depth = _delegation_depth(parent_task) + 1
    max_depth = get_settings().agent_max_delegation_depth
    if child_depth > max_depth:
        raise HTTPException(status_code=422, detail="A2A_DELEGATION_DEPTH_EXCEEDED")
    backend = (body.agent_backend or "langgraph").strip().lower().replace("-", "_")
    if backend not in _EXECUTION_BACKENDS:
        raise HTTPException(status_code=422, detail="A2A_EXECUTOR_UNSUPPORTED")
    adapter = validate_runtime_adapter(backend)
    if not adapter["valid"]:
        raise HTTPException(status_code=422, detail=str(adapter["reason"] or "A2A_RUNTIME_UNAVAILABLE"))
    strategy_id = body.agent_strategy_id or parent_run.agent_strategy_id
    policy_id = body.policy_version_id or parent_run.policy_version_id
    if store.get_strategy(strategy_id) is None:
        raise HTTPException(status_code=422, detail="STRATEGY_NOT_FOUND")
    if store.get_policy(policy_id) is None:
        raise HTTPException(status_code=422, detail="POLICY_NOT_FOUND")

    inherited_config: dict[str, Any] = {}
    if isinstance(parent_task.execution_config, dict):
        for key in ("repository_id", "protected_paths", "test_file_protection"):
            if key in parent_task.execution_config:
                inherited_config[key] = parent_task.execution_config[key]
    actor = current_user(request)
    inherited_config.update(
        {
            "agent_backend": backend,
            "runtime": "langgraph" if backend == "langgraph" else inherited_config.get("runtime"),
            "a2a": {
                "contract_version": "1.0",
                "parent_run_id": parent_run.id,
                "parent_task_id": parent_task.id,
                "depth": child_depth,
                "adapter": backend,
                "delegated_by": actor.id,
                "strategy_id": strategy_id,
                "policy_version_id": policy_id,
            },
        }
    )
    if inherited_config.get("runtime") is None:
        inherited_config.pop("runtime", None)
    try:
        child_task = store.create_task(
            CreateTaskRequest(
                type=TaskType.CODING,
                title=body.title.strip(),
                workspace_id=workspace_id_for_request(request, parent_task.workspace_id)
                or parent_task.workspace_id,
                repo_path=parent_task.repo_path,
                test_command=body.test_command or parent_task.test_command,
                test_timeout_seconds=body.test_timeout_seconds or parent_task.test_timeout_seconds,
                goal=body.goal.strip(),
                execution_config=inherited_config,
                budget=_bounded_budget(body.budget, parent_task.budget),
            )
        )
        child_run = store.create_run(
            task_id=child_task.id,
            agent_strategy_id=strategy_id,
            policy_version_id=policy_id,
            model_name=body.model_name or parent_run.model_name,
        )
        store.update_run(
            child_run.id,
            metrics={
                "a2a_parent_run_id": parent_run.id,
                "a2a_parent_task_id": parent_task.id,
                "a2a_delegation_depth": child_depth,
                "a2a_contract_version": "1.0",
            },
        )
    except WorkspaceQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=exc.code) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job = store.create_job(
        kind="a2a_delegation",
        resource_id=child_run.id,
        task_id=child_task.id,
        metadata={
            "workspace_id": child_task.workspace_id,
            "parent_run_id": parent_run.id,
            "parent_task_id": parent_task.id,
            "contract_version": "1.0",
            "depth": child_depth,
            "adapter": backend,
        },
    )
    store.add_audit_log(
        action="a2a.delegate",
        resource_type="run",
        resource_id=child_run.id,
        decision="queued",
        actor_id=actor.id,
        detail_json={
            "parent_run_id": parent_run.id,
            "parent_task_id": parent_task.id,
            "child_task_id": child_task.id,
            "job_id": job.id,
            "adapter": backend,
            "depth": child_depth,
            "budget": child_task.budget.model_dump(mode="json"),
        },
    )
    await job_queue.enqueue(background_tasks, _execute_run_job, child_run.id, job.id)
    return _response(child_task, child_run, job)


@router.get("/a2a/delegations")
async def list_delegations(
    request: Request,
    parent_run_id: str | None = None,
    workspace_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1_000),
) -> dict[str, object]:
    scoped_workspace = workspace_id_for_request(request, workspace_id)
    if parent_run_id:
        require_run_access(request, store.get_run(parent_run_id))
    items = []
    for task in store.list_tasks(workspace_id=scoped_workspace):
        metadata = _delegation_metadata(task)
        if metadata is None or (parent_run_id and metadata.get("parent_run_id") != parent_run_id):
            continue
        run = store.get_run(task.latest_run_id or "")
        if run is None:
            continue
        jobs = store.list_jobs(resource_id=run.id)
        items.append(_response(task, run, jobs[0] if jobs else None))
    items.sort(key=lambda item: item["child_run"].created_at, reverse=True)
    return {"items": items[:limit], "total": len(items)}


@router.get("/a2a/delegations/{child_run_id}")
async def get_delegation(child_run_id: str, request: Request) -> dict[str, Any]:
    run = require_run_access(request, store.get_run(child_run_id))
    task = require_task_access(request, store.get_task(run.task_id))
    if _delegation_metadata(task) is None:
        raise HTTPException(status_code=404, detail="A2A_DELEGATION_NOT_FOUND")
    jobs = store.list_jobs(resource_id=run.id)
    return _response(task, run, jobs[0] if jobs else None)


@router.post("/a2a/delegations/{child_run_id}/cancel")
async def cancel_delegation(child_run_id: str, request: Request) -> dict[str, Any]:
    from app.agent.runtime import AgentRuntime

    run = require_run_access(request, store.get_run(child_run_id))
    task = require_task_access(request, store.get_task(run.task_id))
    metadata = _delegation_metadata(task)
    if metadata is None:
        raise HTTPException(status_code=404, detail="A2A_DELEGATION_NOT_FOUND")
    cancelled = await AgentRuntime(store).cancel_run(run.id)
    store.add_audit_log(
        action="a2a.cancel",
        resource_type="run",
        resource_id=run.id,
        decision="requested",
        actor_id=current_user(request).id,
        detail_json={"parent_run_id": metadata.get("parent_run_id")},
    )
    jobs = store.list_jobs(resource_id=run.id)
    return _response(task, cancelled, jobs[0] if jobs else None)
