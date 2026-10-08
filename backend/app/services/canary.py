import hashlib
from uuid import uuid4

from pydantic import BaseModel, Field

from app.domain.schemas import RunStatus
from app.services.training import now, validate_model_release


class CanaryRequest(BaseModel):
    version_id: str
    candidate_model_id: str
    baseline_model_id: str
    evaluation_id: str
    traffic_percent: int = Field(default=10, ge=1, le=99)
    min_samples: int = Field(default=20, ge=2, le=10000)
    max_failure_rate: float = Field(default=0.2, ge=0, le=1)
    max_cost_ratio: float = Field(default=1.5, ge=1, le=10)


def create_canary(store, body, actor_id):
    version = store.model_versions.get(body.version_id)
    if not version:
        raise ValueError("MODEL_VERSION_NOT_FOUND")
    candidate, _ = validate_model_release(store, version, body.candidate_model_id, body.evaluation_id)
    baseline = store.model_configs.get(body.baseline_model_id)
    if not baseline or baseline.id == candidate.id or baseline.role != candidate.role or baseline.provider == "mock" or baseline.status != "active":
        raise ValueError("ACTIVE_BASELINE_MODEL_REQUIRED")
    if any(item["workspace_id"] == version["workspace_id"] and item["role"] == candidate.role and item["status"] in {"canary", "promoted"}
           for item in store.model_canaries.values()):
        raise ValueError("WORKSPACE_CANARY_ALREADY_ACTIVE")
    record = {"id": "canary_" + uuid4().hex, **body.model_dump(), "workspace_id": version["workspace_id"],
              "role": candidate.role, "status": "canary", "created_at": now(), "created_by": actor_id, "metrics": {}}
    store.model_canaries[record["id"]] = record
    store.add_audit_log(action="model.canary.create", resource_type="workspace", resource_id=record["workspace_id"],
                        actor_id=actor_id, decision="allow", detail_json={"canary_id": record["id"], "traffic_percent": body.traffic_percent})
    store._persist()
    return record


def route_canary(store, task, run_id, selected):
    role = "research" if str(task.type) == "research" else "coding"
    candidates = [item for item in store.model_canaries.values() if item["workspace_id"] == task.workspace_id
                  and item["role"] == role and item["status"] in {"canary", "promoted"}]
    if not candidates:
        return selected, {}
    record = max(candidates, key=lambda item: item["created_at"])
    bucket = int(hashlib.sha256(f"{record['id']}:{run_id}".encode()).hexdigest()[:8], 16) % 100
    arm = "candidate" if record["status"] == "promoted" or bucket < record["traffic_percent"] else "baseline"
    model = store.model_configs.get(record[f"{arm}_model_id"])
    if not model or model.status not in {"active", "candidate"}:
        stop_canary(store, record, "system", "MODEL_ENDPOINT_DISABLED")
        return selected, {}
    return model, {"canary_id": record["id"], "canary_arm": arm, "model_config_id": model.id}


def measure_canary(store, record):
    arms = {"baseline": [], "candidate": []}
    for run in store.runs.values():
        if run.metrics.get("canary_id") == record["id"] and run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED}:
            task = store.get_task(run.task_id)
            arm = run.metrics.get("canary_arm")
            if task and task.workspace_id == record["workspace_id"] and arm in arms:
                arms[arm].append(run)
    metrics = {}
    for name, runs in arms.items():
        metrics[name] = {"samples": len(runs), "failure_rate": sum(run.status != RunStatus.COMPLETED for run in runs) / len(runs) if runs else 0,
                         "average_cost": sum(run.total_cost for run in runs) / len(runs) if runs else 0,
                         "policy_violations": sum(run.status == RunStatus.BLOCKED or bool(run.metrics.get("policy_violation_count")) for run in runs)}
    baseline, candidate = metrics["baseline"], metrics["candidate"]
    enough = candidate["samples"] >= record["min_samples"]
    reason = None
    if candidate["policy_violations"]:
        reason = "CANARY_POLICY_VIOLATION"
    elif enough and candidate["failure_rate"] > record["max_failure_rate"]:
        reason = "CANARY_FAILURE_RATE_EXCEEDED"
    elif enough and baseline["samples"] >= record["min_samples"] and candidate["average_cost"] > baseline["average_cost"] * record["max_cost_ratio"]:
        reason = "CANARY_COST_INCREASE_EXCEEDED"
    record["metrics"] = metrics
    record["measured_at"] = now()
    return reason


def stop_canary(store, record, actor_id, reason="MANUAL_ROLLBACK"):
    if record["status"] not in {"canary", "promoted"}:
        raise ValueError("CANARY_NOT_ACTIVE")
    record.update(status="rolled_back", finished_at=now(), rollback_reason=reason)
    store.add_audit_log(action="model.canary.rollback", resource_type="workspace", resource_id=record["workspace_id"],
                        actor_id=actor_id, decision=reason, detail_json={"canary_id": record["id"], "metrics": record["metrics"]})
    store._persist()
    return record


def monitor_run(store, run):
    record = store.model_canaries.get(run.metrics.get("canary_id"))
    if record and record["status"] in {"canary", "promoted"}:
        reason = measure_canary(store, record)
        if reason:
            stop_canary(store, record, "canary-controller", reason)


def promote_canary(store, record, actor_id):
    if record["status"] != "canary":
        raise ValueError("CANARY_NOT_ACTIVE")
    reason = measure_canary(store, record)
    if reason:
        stop_canary(store, record, "canary-controller", reason)
        raise ValueError(reason)
    if any(record["metrics"][arm]["samples"] < record["min_samples"] for arm in ("candidate", "baseline")):
        raise ValueError("CANARY_INSUFFICIENT_SAMPLES")
    record.update(status="promoted", traffic_percent=100, promoted_at=now())
    store.add_audit_log(action="model.canary.promote", resource_type="workspace", resource_id=record["workspace_id"],
                        actor_id=actor_id, decision="allow", detail_json={"canary_id": record["id"]})
    store._persist()
    return record
