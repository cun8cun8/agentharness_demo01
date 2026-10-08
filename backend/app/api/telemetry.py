from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.api.context import (
    audit_log_workspace_id,
    evaluation_workspace_id,
    filter_by_workspace,
    job_workspace_id,
    run_workspace_id,
    workspace_id_for_request,
)
from app.config import get_settings
from app.data_flywheel.exporter import TraceDatasetExporter
from app.infra.store import store
from app.services.request_metrics import request_metrics

router = APIRouter(tags=["telemetry"])


@router.get("/telemetry/summary")
async def telemetry_summary(request: Request) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request)
    runs = filter_by_workspace(request, list(store.runs.values()), lambda item: run_workspace_id(item.id))
    run_ids = {run.id for run in runs}
    tool_calls = [item for item in store.tool_calls.values() if item.run_id in run_ids]
    jobs = filter_by_workspace(request, list(store.jobs.values()), job_workspace_id)
    audit_logs = filter_by_workspace(request, list(store.audit_logs.values()), audit_log_workspace_id)
    dataset_quality = TraceDatasetExporter(store).quality_report(workspace_id=workspace_id)
    model_invocations = [
        item for item in audit_logs if item.action == "model.invoke"
    ]
    briefs = store.list_research_briefs(workspace_id=workspace_id)
    evidence = store.list_research_evidence(workspace_id=workspace_id)
    notebook_runs = filter_by_workspace(
        request,
        list(store.notebook_runs.values()),
        lambda item: (
            store.get_research_brief(item.brief_id).workspace_id
            if store.get_research_brief(item.brief_id) is not None
            else None
        ),
    )
    evaluation_runs = filter_by_workspace(
        request,
        list(store.evaluation_runs.values()),
        evaluation_workspace_id,
    )
    memory_items = store.list_memory_items(workspace_id=workspace_id)
    dataset_snapshots = store.list_dataset_snapshots(workspace_id=workspace_id)
    repository_connections = store.list_repository_connections(workspace_id=workspace_id)
    retryable_failed_jobs = [
        job
        for job in jobs
        if job.status in {"failed", "cancelled"} and job.kind in {"agent_run", "agent_run_resume"}
    ]
    return {
        "run_count": len(runs),
        "active_run_count": sum(
            1 for run in runs if run.status.value in {"queued", "running", "paused"}
        ),
        "completed_runs": sum(1 for run in runs if run.status.value == "completed"),
        "failed_runs": sum(1 for run in runs if run.status.value == "failed"),
        "tool_call_count": len(tool_calls),
        "audit_log_count": len(audit_logs),
        "job_count": len(jobs),
        "queued_job_count": sum(1 for job in jobs if job.status == "queued"),
        "completed_job_count": sum(1 for job in jobs if job.status == "completed"),
        "failed_job_count": sum(1 for job in jobs if job.status == "failed"),
        "running_job_count": sum(1 for job in jobs if job.status == "running"),
        "retryable_failed_job_count": len(retryable_failed_jobs),
        "cancel_requested_job_count": sum(1 for job in jobs if job.cancel_requested),
        "job_queue_backend": get_settings().job_queue_backend,
        "research_brief_count": len(briefs),
        "research_evidence_count": len(evidence),
        "notebook_run_count": len(notebook_runs),
        "notebook_execution_failure_count": sum(
            1
            for item in notebook_runs
            if item.metrics.get("execution_status") not in {None, "completed"}
        ),
        "evaluation_run_count": len(evaluation_runs),
        "memory_item_count": len(memory_items),
        "success_memory_count": sum(
            1 for item in memory_items if item.memory_type == "success"
        ),
        "failure_memory_count": sum(
            1 for item in memory_items if item.memory_type == "failure"
        ),
        "dataset_snapshot_count": len(dataset_snapshots),
        "dataset_quality": dataset_quality,
        "approved_trace_item_count": dataset_quality["approved_trace_item_count"],
        "approved_preference_pair_count": dataset_quality["approved_preference_pair_count"],
        "training_bundle_sft_candidate_count": dataset_quality["usable_for_sft_count"],
        "training_bundle_preference_candidate_count": dataset_quality["usable_for_preference_count"],
        "model_config_count": len(store.model_configs),
        "model_invocation_count": len(model_invocations),
        "model_fallback_count": sum(
            1 for item in model_invocations if item.decision == "fallback"
        ),
        "repository_connection_count": len(repository_connections),
        "http_request_metrics": request_metrics.snapshot(),
    }


@router.get("/telemetry/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(request: Request) -> str:
    summary = await telemetry_summary(request)
    lines = [
        "# HELP researchforge_runs_total Total agent runs.",
        "# TYPE researchforge_runs_total gauge",
        f"researchforge_runs_total {summary['run_count']}",
        "# HELP researchforge_runs_active_total Active queued, running, or paused agent runs.",
        "# TYPE researchforge_runs_active_total gauge",
        f"researchforge_runs_active_total {summary['active_run_count']}",
        "# HELP researchforge_tool_calls_total Total tool calls.",
        "# TYPE researchforge_tool_calls_total gauge",
        f"researchforge_tool_calls_total {summary['tool_call_count']}",
        "# HELP researchforge_audit_logs_total Total audit log records.",
        "# TYPE researchforge_audit_logs_total gauge",
        f"researchforge_audit_logs_total {summary['audit_log_count']}",
        "# HELP researchforge_jobs_total Total background jobs.",
        "# TYPE researchforge_jobs_total gauge",
        f"researchforge_jobs_total {summary['job_count']}",
        "# HELP researchforge_jobs_queued_total Queued background jobs.",
        "# TYPE researchforge_jobs_queued_total gauge",
        f"researchforge_jobs_queued_total {summary['queued_job_count']}",
        "# HELP researchforge_jobs_completed_total Completed background jobs.",
        "# TYPE researchforge_jobs_completed_total gauge",
        f"researchforge_jobs_completed_total {summary['completed_job_count']}",
        "# HELP researchforge_jobs_failed_total Failed background jobs.",
        "# TYPE researchforge_jobs_failed_total gauge",
        f"researchforge_jobs_failed_total {summary['failed_job_count']}",
        "# HELP researchforge_jobs_retryable_total Retryable failed or cancelled agent-run jobs.",
        "# TYPE researchforge_jobs_retryable_total gauge",
        f"researchforge_jobs_retryable_total {summary['retryable_failed_job_count']}",
        "# HELP researchforge_research_briefs_total Total research briefs.",
        "# TYPE researchforge_research_briefs_total gauge",
        f"researchforge_research_briefs_total {summary['research_brief_count']}",
        "# HELP researchforge_research_evidence_total Total imported research evidence.",
        "# TYPE researchforge_research_evidence_total gauge",
        f"researchforge_research_evidence_total {summary['research_evidence_count']}",
        "# HELP researchforge_notebook_runs_total Total notebook runs.",
        "# TYPE researchforge_notebook_runs_total gauge",
        f"researchforge_notebook_runs_total {summary['notebook_run_count']}",
        "# HELP researchforge_notebook_execution_failures_total Failed notebook executions.",
        "# TYPE researchforge_notebook_execution_failures_total gauge",
        f"researchforge_notebook_execution_failures_total {summary['notebook_execution_failure_count']}",
        "# HELP researchforge_evaluation_runs_total Total evaluation runs.",
        "# TYPE researchforge_evaluation_runs_total gauge",
        f"researchforge_evaluation_runs_total {summary['evaluation_run_count']}",
        "# HELP researchforge_model_invocations_total Total model invocations.",
        "# TYPE researchforge_model_invocations_total gauge",
        f"researchforge_model_invocations_total {summary['model_invocation_count']}",
        "# HELP researchforge_model_fallbacks_total Total model fallback invocations.",
        "# TYPE researchforge_model_fallbacks_total gauge",
        f"researchforge_model_fallbacks_total {summary['model_fallback_count']}",
        "# HELP researchforge_dataset_snapshots_total Total dataset snapshots.",
        "# TYPE researchforge_dataset_snapshots_total gauge",
        f"researchforge_dataset_snapshots_total {summary['dataset_snapshot_count']}",
        "# HELP researchforge_dataset_trace_items_approved_total Approved trace dataset items.",
        "# TYPE researchforge_dataset_trace_items_approved_total gauge",
        f"researchforge_dataset_trace_items_approved_total {summary['approved_trace_item_count']}",
        "# HELP researchforge_training_bundle_sft_candidates_total SFT candidate records for training bundles.",
        "# TYPE researchforge_training_bundle_sft_candidates_total gauge",
        f"researchforge_training_bundle_sft_candidates_total {summary['training_bundle_sft_candidate_count']}",
        "# HELP researchforge_repository_connections_total Total repository connections.",
        "# TYPE researchforge_repository_connections_total gauge",
        f"researchforge_repository_connections_total {summary['repository_connection_count']}",
        "# HELP researchforge_http_requests_total HTTP requests by method, route, and status.",
        "# TYPE researchforge_http_requests_total counter",
    ]
    from app.services.job_queue import job_queue
    try:
        queue = await job_queue.snapshot()
        connected = int(bool(queue.get("connected")))
    except Exception:
        queue, connected = {}, 0
    workspace_id = workspace_id_for_request(request)
    jobs = filter_by_workspace(request, store.read_jobs(), job_workspace_id)
    expired = sum(job.error_summary == "QUEUE_WAIT_TIMEOUT" for job in jobs)
    lines.extend([
        "# TYPE researchforge_queue_connected gauge", f"researchforge_queue_connected {connected}",
        "# TYPE researchforge_queue_dead_letters gauge", f"researchforge_queue_dead_letters {queue.get('dead_letters', 0)}",
        "# TYPE researchforge_queue_active_workers gauge", f"researchforge_queue_active_workers {queue.get('active_workers') or 0}",
        "# TYPE researchforge_queue_wait_timeouts gauge", f"researchforge_queue_wait_timeouts {expired}",
    ])
    for item in summary["http_request_metrics"]:
        labels = (
            f'method="{item["method"]}",path="{item["path"]}",'
            f'status="{item["status"]}"'
        )
        lines.append(f"researchforge_http_requests_total{{{labels}}} {item['count']}")
        lines.append(
            f"researchforge_http_request_duration_ms{{{labels}}} {item['total_ms']}"
        )
    return "\n".join(lines) + "\n"
