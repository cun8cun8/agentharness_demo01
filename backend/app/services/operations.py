"""Workspace-scoped quality, cost and conservative log retention operations."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from app.domain.schemas import ArtifactType, RunStatus

TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.BLOCKED}

def retention_preview(store, workspace_id, days=30, limit=500, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    candidates = []
    for artifact in store.artifacts.values():
        run = store.runs.get(artifact.run_id)
        task = store.tasks.get(run.task_id) if run else None
        if not task or task.workspace_id != workspace_id or run.status not in TERMINAL:
            continue
        if artifact.type != ArtifactType.LOG or artifact.created_at >= cutoff or artifact.metadata.get("content_expired_at"):
            continue
        if artifact.metadata.get("retention_hold") or run.metrics.get("retention_hold"):
            continue
        candidates.append({"artifact_id": artifact.id, "run_id": run.id, "name": artifact.name, "created_at": artifact.created_at.isoformat(), "size_bytes": int(artifact.metadata.get("size_bytes") or len((artifact.content or "").encode()))})
    candidates.sort(key=lambda a: (a["created_at"], a["artifact_id"]))
    selected = candidates[:limit]
    digest = hashlib.sha256(json.dumps({"workspace_id": workspace_id, "days": days, "ids": [a["artifact_id"] for a in selected]}, sort_keys=True).encode()).hexdigest()
    return {"workspace_id": workspace_id, "days": days, "candidates": selected, "total_candidates": len(candidates), "selected_count": len(selected), "estimated_bytes": sum(a["size_bytes"] for a in selected), "confirmation_digest": digest, "protected": ["nonterminal runs", "diffs", "reports", "held artifacts", "audit metadata"], "dry_run": True}

def expire_logs(store, plan, actor_id):
    expired = []
    for item in plan["candidates"]:
        artifact = store.artifacts[item["artifact_id"]]
        # Delete only this configured blob. Retain the record and audit trail.
        content_hash = artifact.metadata.get("sha256") or hashlib.sha256((store.read_artifact(artifact.id).content or "").encode()).hexdigest()
        store.artifact_blob_store.delete(artifact.id, artifact.metadata)
        artifact.content = ""
        artifact.metadata = {**artifact.metadata, "content_expired_at": datetime.now(timezone.utc).isoformat(), "content_sha256": content_hash, "retention_days": plan["days"]}
        store._persist()
        expired.append(artifact.id)
    store.add_audit_log(action="artifacts.retention", resource_type="workspace", resource_id=plan["workspace_id"], actor_id=actor_id, decision="expired", detail_json={"artifact_ids": expired, "confirmation_digest": plan["confirmation_digest"]})
    return {"workspace_id": plan["workspace_id"], "expired_count": len(expired), "artifact_ids": expired, "metadata_retained": True}

def workspace_quality(store, workspace_id, days=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    grouped = {}
    for run in store.runs.values():
        task = store.tasks.get(run.task_id)
        if not task or task.workspace_id != workspace_id or run.created_at < cutoff:
            continue
        project = store.run_project_context(task).get("project_name") or "unassociated"
        row = {"run_id": run.id, "created_at": run.created_at.isoformat(), "project": project, "status": run.status.value, "cost": run.total_cost, "duration_ms": run.duration_ms or 0, "failure_reason": run.error_summary, "model": run.model_name, "strategy": run.agent_strategy_id, "baseline_tests_passed": run.metrics.get("baseline_tests_passed"), "validation_tests_passed": run.metrics.get("validation_tests_passed"), "test_files_modified": bool(run.metrics.get("touched_tests"))}
        rows.append(row)
        agg = grouped.setdefault(project, {"project": project, "runs": 0, "completed": 0, "failed": 0, "cost": 0.0, "duration_ms": 0, "failure_types": {}})
        agg["runs"] += 1
        agg["completed"] += int(run.status == RunStatus.COMPLETED)
        agg["failed"] += int(run.status in {RunStatus.FAILED, RunStatus.BLOCKED})
        agg["cost"] += run.total_cost
        agg["duration_ms"] += run.duration_ms or 0
        if run.error_summary: agg["failure_types"][run.error_summary] = agg["failure_types"].get(run.error_summary, 0) + 1
    for agg in grouped.values():
        agg["cost"] = round(agg["cost"], 6)
        agg["success_rate"] = round(agg["completed"] / agg["runs"], 4)
        agg["average_duration_ms"] = round(agg.pop("duration_ms") / agg["runs"])
    return {"workspace_id": workspace_id, "days": days, "quota_and_daily_usage": store.workspace_usage(workspace_id), "projects": sorted(grouped.values(), key=lambda a: -a["cost"]), "recent_runs": sorted(rows, key=lambda a: a["created_at"], reverse=True)[:100], "cost_scope": "run cost; direct model usage is separately included in daily usage"}


def compare_quality_reports(baseline, current):
    # Different task sets cannot support a comparable regression claim.
    before = {item.task_id: item for item in baseline.items}
    after = {item.task_id: item for item in current.items}
    matched = before.keys() & after.keys()
    regressions = [{"task_id": key, "before": before[key].score, "after": after[key].score, "success_lost": before[key].success and not after[key].success} for key in sorted(matched) if after[key].score + 0.01 < before[key].score or (before[key].success and not after[key].success)]
    compatible = before.keys() == after.keys() and baseline.benchmark_name == current.benchmark_name and baseline.policy_version_id == current.policy_version_id
    return {"baseline_id": baseline.id, "current_id": current.id, "comparable": compatible, "matched_tasks": len(matched), "missing_tasks": sorted(before.keys() - after.keys()), "added_tasks": sorted(after.keys() - before.keys()), "regressions": regressions, "regression_count": len(regressions), "passed": compatible and not regressions, "success_rate_delta": round(current.summary.get("success_rate", 0) - baseline.summary.get("success_rate", 0), 4), "cost_delta": round(sum(item.cost for item in current.items) - sum(item.cost for item in baseline.items), 6)}
