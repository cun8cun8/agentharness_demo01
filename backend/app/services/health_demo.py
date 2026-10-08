"""Deterministic health/IoT demo used to show policy, memory, and review flow."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.infra.idgen import id_generator

_sessions: dict[str, dict[str, Any]] = {}
_user_memory: dict[str, list[str]] = {}


def _number(signals: dict[str, Any], key: str) -> float | None:
    value = signals.get(key)
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def analyze_session(user_id: str, signals: dict[str, Any], multimodal_text: str = "") -> dict[str, Any]:
    score = 0
    alerts: list[dict[str, Any]] = []

    heart_rate = _number(signals, "heart_rate")
    if heart_rate is not None and (heart_rate < 45 or heart_rate > 120):
        score += 2
        alerts.append({"code": "HEART_RATE_OUT_OF_RANGE", "severity": "warning", "value": heart_rate})
    spo2 = _number(signals, "spo2")
    if spo2 is not None and spo2 < 92:
        score += 3
        alerts.append({"code": "SPO2_LOW", "severity": "high", "value": spo2})
    sleep_hours = _number(signals, "sleep_hours")
    if sleep_hours is not None and sleep_hours < 5:
        score += 1
        alerts.append({"code": "SLEEP_LOW", "severity": "info", "value": sleep_hours})
    steps = _number(signals, "steps")
    if steps is not None and steps < 1_000:
        score += 1
        alerts.append({"code": "ACTIVITY_LOW", "severity": "info", "value": steps})
    text = multimodal_text.strip()
    if any(word in text.lower() for word in ("chest pain", "呼吸困难", "胸痛", "晕厥")):
        score += 3
        alerts.append({"code": "TEXT_REVIEW_REQUIRED", "severity": "high", "value": "text"})

    level = "high" if score >= 4 else "moderate" if score >= 2 else "low"
    memory = _user_memory.setdefault(user_id, [])
    summary = f"最近一次状态：{level} 风险，{len(alerts)} 个提示，综合分数 {score}。"
    memory.append(summary)
    memory[:] = memory[-10:]
    session_id = id_generator.next("health")
    record = {
        "id": session_id,
        "user_id": user_id,
        "signals": dict(signals),
        "multimodal_text": text,
        "risk_score": score,
        "risk_level": level,
        "alerts": alerts,
        "requires_human_review": level == "high" or any(item["severity"] == "high" for item in alerts),
        "memory_summary": summary,
        "memory": list(memory),
        "review": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "演示用途，不构成医疗诊断或治疗建议。",
    }
    _sessions[session_id] = record
    return record


def list_sessions(user_id: str | None = None) -> list[dict[str, Any]]:
    items = list(_sessions.values())
    if user_id:
        items = [item for item in items if item["user_id"] == user_id]
    return sorted(items, key=lambda item: item["created_at"], reverse=True)


def get_session(session_id: str) -> dict[str, Any] | None:
    return _sessions.get(session_id)


def review_session(session_id: str, decision: str, notes: str) -> dict[str, Any] | None:
    record = _sessions.get(session_id)
    if record is None:
        return None
    record["review"] = {
        "decision": decision,
        "notes": notes,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    record["requires_human_review"] = False
    return record
