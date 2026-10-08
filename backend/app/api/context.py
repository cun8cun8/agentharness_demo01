from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.domain.schemas import (
    AgentRunResponse,
    ApprovalRequestResponse,
    AuditLogResponse,
    DatasetSnapshotResponse,
    EvaluationRunResponse,
    JobResponse,
    MemoryItemResponse,
    PreferencePairResponse,
    RepositoryConnectionResponse,
    ResearchBriefResponse,
    TaskResponse,
    TraceDatasetItemResponse,
    UserResponse,
    WorkspaceResponse,
)
from app.infra.store import store


def current_user(request: Request) -> UserResponse:
    user = getattr(request.state, "user", None)
    if user is None:
        user = store.get_user(request.headers.get("x-user-id", "user_admin"))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在。")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="用户已停用。")
    return user


def is_admin(request: Request) -> bool:
    return current_user(request).role == "admin"


def workspace_id_for_request(
    request: Request,
    requested_workspace_id: str | None = None,
) -> str | None:
    """Return the requested scope, forcing non-admin users to their own workspace."""
    user = current_user(request)
    if user.role == "admin":
        selected = request.headers.get("x-workspace-id") or request.cookies.get("researchforge_workspace")
        return requested_workspace_id or selected
    workspace_id = requested_workspace_id or user.workspace_id
    require_workspace_access(request, workspace_id)
    return workspace_id


def require_workspace_access(request: Request, workspace_id: str) -> str:
    user = current_user(request)
    if user.role != "admin" and workspace_id != user.workspace_id:
        raise HTTPException(status_code=403, detail="无权访问该工作区。")
    return workspace_id


def require_task_access(request: Request, task: TaskResponse | None) -> TaskResponse:
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_workspace_access(request, task.workspace_id)
    return task


def require_run_access(request: Request, run: AgentRunResponse | None) -> AgentRunResponse:
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    task = store.get_task(run.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    require_workspace_access(request, task.workspace_id)
    return run


def require_approval_access(
    request: Request,
    approval: ApprovalRequestResponse | None,
) -> ApprovalRequestResponse:
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    require_run_access(request, store.get_run(approval.run_id))
    return approval


def require_repository_access(
    request: Request,
    repository: RepositoryConnectionResponse | None,
) -> RepositoryConnectionResponse:
    if repository is None:
        raise HTTPException(status_code=404, detail="Repository connection not found")
    require_workspace_access(request, repository.workspace_id)
    return repository


def require_memory_access(
    request: Request,
    item: MemoryItemResponse | None,
) -> MemoryItemResponse:
    if item is None:
        raise HTTPException(status_code=404, detail="Memory item not found")
    require_workspace_access(request, memory_workspace_id(item))
    return item


def require_trace_item_access(
    request: Request,
    item: TraceDatasetItemResponse | None,
) -> TraceDatasetItemResponse:
    if item is None:
        raise HTTPException(status_code=404, detail="Trace dataset item not found")
    require_run_access(request, store.get_run(item.agent_run_id))
    return item


def require_preference_pair_access(
    request: Request,
    item: PreferencePairResponse | None,
) -> PreferencePairResponse:
    if item is None:
        raise HTTPException(status_code=404, detail="Preference pair not found")
    workspace_id = preference_pair_workspace_id(item)
    if workspace_id is None:
        raise HTTPException(status_code=404, detail="Preference pair task not found")
    require_workspace_access(request, workspace_id)
    return item


def require_snapshot_access(
    request: Request,
    item: DatasetSnapshotResponse | None,
) -> DatasetSnapshotResponse:
    if item is None:
        raise HTTPException(status_code=404, detail="Dataset snapshot not found")
    require_workspace_access(request, snapshot_workspace_id(item))
    return item


def require_research_brief_access(
    request: Request,
    brief: ResearchBriefResponse | None,
) -> ResearchBriefResponse:
    if brief is None:
        raise HTTPException(status_code=404, detail="Research brief not found")
    require_workspace_access(request, brief_workspace_id(brief))
    return brief


def task_workspace_id(task_id: str | None) -> str | None:
    if not task_id:
        return None
    task = store.get_task(task_id)
    return task.workspace_id if task is not None else None


def run_workspace_id(run_id: str | None) -> str | None:
    if not run_id:
        return None
    run = store.get_run(run_id)
    return task_workspace_id(run.task_id) if run is not None else None


def memory_workspace_id(item: MemoryItemResponse) -> str:
    explicit = getattr(item, "workspace_id", None)
    if explicit:
        return explicit
    return run_workspace_id(item.agent_run_id) or task_workspace_id(item.task_id) or "workspace_default"


def trace_item_workspace_id(item: TraceDatasetItemResponse) -> str | None:
    return run_workspace_id(item.agent_run_id)


def preference_pair_workspace_id(item: PreferencePairResponse) -> str | None:
    task_id = item.task_id
    if task_id is None:
        chosen = store.get_run(item.chosen_run_id)
        task_id = chosen.task_id if chosen is not None else None
    return task_workspace_id(task_id)


def snapshot_workspace_id(item: DatasetSnapshotResponse) -> str:
    explicit = getattr(item, "workspace_id", None)
    if explicit:
        return explicit
    for trace_id in item.trace_item_ids:
        trace_item = store.get_trace_dataset_item(trace_id)
        if trace_item is not None:
            workspace_id = trace_item_workspace_id(trace_item)
            if workspace_id:
                return workspace_id
    for pair_id in item.preference_pair_ids:
        pair = store.preference_pairs.get(pair_id)
        if pair is not None:
            workspace_id = preference_pair_workspace_id(pair)
            if workspace_id:
                return workspace_id
    return "workspace_default"


def brief_workspace_id(brief: ResearchBriefResponse) -> str:
    explicit = getattr(brief, "workspace_id", None)
    return explicit or task_workspace_id(brief.task_id) or "workspace_default"


def evaluation_workspace_id(evaluation: EvaluationRunResponse) -> str | None:
    workspace_ids = {
        task_workspace_id(item.task_id)
        for item in evaluation.items
        if task_workspace_id(item.task_id)
    }
    return next(iter(workspace_ids)) if len(workspace_ids) == 1 else None


def require_evaluation_access(
    request: Request,
    evaluation: EvaluationRunResponse | None,
) -> EvaluationRunResponse:
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    workspace_id = evaluation_workspace_id(evaluation)
    if workspace_id is None and not is_admin(request):
        raise HTTPException(status_code=403, detail="无权访问该评测运行。")
    if workspace_id is not None:
        require_workspace_access(request, workspace_id)
    return evaluation


def job_workspace_id(job: JobResponse | None) -> str | None:
    if job is None:
        return None
    metadata_workspace_id = job.metadata.get("workspace_id")
    if isinstance(metadata_workspace_id, str) and metadata_workspace_id:
        return metadata_workspace_id
    if job.task_id:
        return task_workspace_id(job.task_id)
    if job.kind.startswith("repository"):
        repository = store.get_repository_connection(job.resource_id)
        return repository.workspace_id if repository is not None else None
    if job.kind == "notebook_run":
        brief = store.get_research_brief(job.resource_id)
        return brief_workspace_id(brief) if brief is not None else None
    if job.kind == "evaluation":
        evaluation = store.get_evaluation_run(job.resource_id)
        return evaluation_workspace_id(evaluation) if evaluation is not None else None
    if job.kind in {"agent_run", "agent_run_resume"}:
        return run_workspace_id(job.resource_id)
    if job.kind == "release_gate":
        evaluation = store.get_evaluation_run(job.resource_id)
        if evaluation is None:
            return None
        return evaluation_workspace_id(evaluation)
    return None


def require_job_access(request: Request, job: JobResponse | None) -> JobResponse:
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    workspace_id = job_workspace_id(job)
    if workspace_id is None and not is_admin(request):
        raise HTTPException(status_code=403, detail="无权访问该 Job。")
    if workspace_id is not None:
        require_workspace_access(request, workspace_id)
    return job


def filter_by_workspace(
    request: Request,
    items: list[Any],
    resolver,
) -> list[Any]:
    workspace_id = workspace_id_for_request(request)
    if workspace_id is None:
        return items
    return [item for item in items if resolver(item) == workspace_id]


def audit_log_workspace_id(item: AuditLogResponse) -> str | None:
    detail_workspace_id = item.detail_json.get("workspace_id")
    if isinstance(detail_workspace_id, str) and detail_workspace_id:
        return detail_workspace_id
    resolvers = {
        "task": lambda: task_workspace_id(item.resource_id),
        "run": lambda: run_workspace_id(item.resource_id),
        "job": lambda: job_workspace_id(store.jobs.get(item.resource_id)),
        "approval_request": lambda: run_workspace_id(
            store.approval_requests.get(item.resource_id).run_id
            if store.approval_requests.get(item.resource_id) is not None
            else None
        ),
        "trace_dataset_item": lambda: trace_item_workspace_id(
            store.get_trace_dataset_item(item.resource_id)
        )
        if store.get_trace_dataset_item(item.resource_id) is not None
        else None,
        "preference_pair": lambda: preference_pair_workspace_id(
            store.preference_pairs.get(item.resource_id)
        )
        if store.preference_pairs.get(item.resource_id) is not None
        else None,
        "dataset_snapshot": lambda: snapshot_workspace_id(
            store.get_dataset_snapshot(item.resource_id)
        )
        if store.get_dataset_snapshot(item.resource_id) is not None
        else None,
        "memory_item": lambda: memory_workspace_id(
            store.get_memory_item(item.resource_id)
        )
        if store.get_memory_item(item.resource_id) is not None
        else None,
        "research_brief": lambda: brief_workspace_id(
            store.get_research_brief(item.resource_id)
        )
        if store.get_research_brief(item.resource_id) is not None
        else None,
        "research_paper": lambda: (
            store.research_papers.get(item.resource_id).workspace_id
            if store.research_papers.get(item.resource_id) is not None
            else None
        ),
        "repository_connection": lambda: (
            store.get_repository_connection(item.resource_id).workspace_id
            if store.get_repository_connection(item.resource_id) is not None
            else None
        ),
        "workspace": lambda: item.resource_id,
        "user": lambda: (
            store.users.get(item.resource_id).workspace_id
            if store.users.get(item.resource_id) is not None
            else None
        ),
    }
    resolver = resolvers.get(item.resource_type)
    return resolver() if resolver is not None else None


def require_workspace_object(
    request: Request,
    item: WorkspaceResponse | None,
) -> WorkspaceResponse:
    if item is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    require_workspace_access(request, item.id)
    return item
