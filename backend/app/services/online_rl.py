"""Controlled online GRPO rollout sessions.

Rollouts are model calls only. Reward evaluation is deterministic and data-only;
the generated completion is never executed by this service.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.schemas import ModelInvokeRequest, TaskType
from app.services.model_gateway import invoke_configured_model


class OnlineRLRequest(BaseModel):
    workspace_id: str = "workspace_default"
    model_name: str | None = None
    reference_item_ids: list[str] = Field(min_length=1, max_length=100)
    max_rollouts: int = Field(default=10, ge=1, le=1000)
    max_tokens_per_rollout: int = Field(default=1024, ge=32, le=4096)
    max_cost: float = Field(default=2.0, gt=0, le=1000)
    temperature: float = Field(default=0.7, ge=0, le=2)


class OnlineRLExportRequest(BaseModel):
    min_reward: float = Field(default=0.5, ge=0, le=1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", value))


def patch_reward(completion: str, reference_patch: str) -> float:
    """Score a completion without running it or parsing arbitrary user code."""
    output = completion if isinstance(completion, str) else str(completion)
    reference = reference_patch if isinstance(reference_patch, str) else str(reference_patch)
    if not all(marker in output for marker in ("--- ", "+++ ", "@@")):
        return 0.0
    output_tokens, reference_tokens = _tokens(output), _tokens(reference)
    similarity = len(output_tokens & reference_tokens) / max(1, len(output_tokens | reference_tokens))
    changed_lines = sum(line.startswith(("+", "-")) and not line.startswith(("+++", "---")) for line in output.splitlines())
    if changed_lines == 0 or changed_lines > 400:
        return 0.0
    return round(min(1.0, 0.2 + similarity * 0.8), 6)


def _reference(store, item_id: str, workspace_id: str) -> dict[str, str]:
    item = store.trace_dataset_items.get(item_id)
    if not item or item.status != "approved" or not item.usable_for_rl:
        raise ValueError("APPROVED_RL_DATA_REQUIRED")
    run = store.get_run(item.agent_run_id)
    task = store.get_task(run.task_id) if run else None
    if not run or not task or task.workspace_id != workspace_id:
        raise ValueError("TRAINING_DATA_WORKSPACE_MISMATCH")
    artifacts = store.list_artifacts(run.id)
    patch = next((artifact.content for artifact in reversed(artifacts) if str(artifact.type) in {"diff", "patch"} and artifact.content), None)
    if not patch:
        raise ValueError("TRAINING_PATCH_REQUIRED")
    return {"item_id": item.id, "prompt": task.goal, "reference_patch": patch}


def create_session(store, request: OnlineRLRequest, actor_id: str) -> dict[str, Any]:
    if request.workspace_id not in store.workspaces:
        raise ValueError("WORKSPACE_NOT_FOUND")
    if len(request.reference_item_ids) != len(set(request.reference_item_ids)):
        raise ValueError("TRAINING_DUPLICATE_ITEMS")
    references = [_reference(store, item_id, request.workspace_id) for item_id in request.reference_item_ids]
    try:
        model, reason = store.select_model_config(TaskType.CODING, requested_model=request.model_name, strict=True)
    except LookupError as exc:
        raise ValueError("NO_REAL_MODEL_CONFIGURED") from exc
    if model.provider == "mock":
        # Mock is useful in local tests, but is never silently used in production.
        if not get_settings().allow_mock_models:
            raise ValueError("NO_REAL_MODEL_CONFIGURED")
    session = {
        "id": "online_rl_" + uuid4().hex,
        "workspace_id": request.workspace_id,
        "model_name": model.model_name,
        "model_config_id": model.id,
        "reference_item_ids": [item["item_id"] for item in references],
        "max_rollouts": request.max_rollouts,
        "max_tokens_per_rollout": request.max_tokens_per_rollout,
        "max_cost": request.max_cost,
        "temperature": request.temperature,
        "status": "active",
        "rollout_count": 0,
        "total_reward": 0.0,
        "total_cost": 0.0,
        "rollouts": [],
        "created_at": _now(),
        "created_by": actor_id,
        "routing_reason": reason,
    }
    store.online_rl_sessions[session["id"]] = session
    store.add_audit_log(action="online_rl.create", resource_type="workspace", resource_id=request.workspace_id,
                        actor_id=actor_id, decision="allow", detail_json={"session_id": session["id"], "model_name": model.model_name})
    store._persist()
    return session


def rollout(store, session: dict[str, Any], actor_id: str, prompt: str | None = None, reference_item_id: str | None = None) -> dict[str, Any]:
    if session["status"] != "active":
        if session.get("rollout_count", 0) >= session.get("max_rollouts", 0):
            raise ValueError("ONLINE_RL_ROLLOUT_LIMIT_REACHED")
        raise ValueError("ONLINE_RL_SESSION_NOT_ACTIVE")
    if session["rollout_count"] >= session["max_rollouts"]:
        session["status"] = "completed"
        store._persist()
        raise ValueError("ONLINE_RL_ROLLOUT_LIMIT_REACHED")
    if reference_item_id is None:
        reference_item_id = session["reference_item_ids"][session["rollout_count"] % len(session["reference_item_ids"])]
    if reference_item_id not in session["reference_item_ids"]:
        raise ValueError("ONLINE_RL_REFERENCE_NOT_ALLOWED")
    reference = _reference(store, reference_item_id, session["workspace_id"])
    model = store.model_configs.get(session["model_config_id"])
    if not model:
        raise ValueError("MODEL_CONFIG_NOT_FOUND")
    rollout_prompt = (prompt or reference["prompt"])[:50_000]
    result = invoke_configured_model(model, ModelInvokeRequest(
        task_type=TaskType.CODING,
        requested_model=model.model_name,
        system_prompt="输出 unified diff；只修改必要代码，不执行命令，不修改测试文件。",
        prompt=rollout_prompt,
        max_tokens=session["max_tokens_per_rollout"],
        temperature=session["temperature"],
    ))
    projected_cost = round(session["total_cost"] + result.estimated_cost, 6)
    if projected_cost > session["max_cost"]:
        session["status"] = "completed"
        store._persist()
        raise ValueError("ONLINE_RL_COST_BUDGET_EXCEEDED")
    reward = patch_reward(result.output_text, reference["reference_patch"])
    rollout_record = {
        "id": "rollout_" + uuid4().hex,
        "reference_item_id": reference_item_id,
        "prompt": rollout_prompt,
        "prompt_sha256": hashlib.sha256(rollout_prompt.encode()).hexdigest(),
        "reference_patch": reference["reference_patch"][:100_000],
        "completion": result.output_text[:100_000],
        "reward": reward,
        "usage": result.usage,
        "cost": result.estimated_cost,
        "fallback_used": result.fallback_used,
        "created_at": _now(),
    }
    session["rollouts"].append(rollout_record)
    session["rollout_count"] += 1
    session["total_reward"] = round(session["total_reward"] + reward, 6)
    session["total_cost"] = projected_cost
    if session["rollout_count"] >= session["max_rollouts"]:
        session["status"] = "completed"
    store.add_audit_log(action="online_rl.rollout", resource_type="workspace", resource_id=session["workspace_id"],
                        actor_id=actor_id, decision="completed", detail_json={"session_id": session["id"], "reward": reward, "cost": result.estimated_cost})
    store._persist()
    return rollout_record


def stop_session(store, session: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if session["status"] == "active":
        session["status"] = "cancelled"
        session["finished_at"] = _now()
        store.add_audit_log(action="online_rl.cancel", resource_type="workspace", resource_id=session["workspace_id"], actor_id=actor_id, decision="allow", detail_json={"session_id": session["id"]})
        store._persist()
    return session


def export_dataset(store, session: dict[str, Any], request: OnlineRLExportRequest, actor_id: str) -> dict[str, Any]:
    if not session.get("rollouts"):
        raise ValueError("ONLINE_RL_ROLLOUTS_REQUIRED")
    rows = [
        {"prompt": item.get("prompt", ""), "completion": item["completion"], "reward": item["reward"], "rollout_id": item["id"]}
        for item in session["rollouts"]
        if float(item.get("reward", 0)) >= request.min_reward
    ]
    if not rows:
        raise ValueError("ONLINE_RL_REWARD_THRESHOLD_EMPTY")
    payload = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    exported = {
        "row_count": len(rows),
        "min_reward": request.min_reward,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "rollout_ids": [row["rollout_id"] for row in rows],
        "jsonl": payload.decode("utf-8"),
    }
    session["exported_dataset"] = {key: value for key, value in exported.items() if key != "jsonl"}
    session["training_ready"] = True
    session["status"] = "frozen"
    session["finished_at"] = _now()
    store.add_audit_log(action="online_rl.export", resource_type="workspace", resource_id=session["workspace_id"], actor_id=actor_id, decision="allow", detail_json={"session_id": session["id"], **session["exported_dataset"]})
    store._persist()
    return exported
