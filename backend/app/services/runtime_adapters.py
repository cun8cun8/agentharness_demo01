"""Capability registry for optional Agent Runtime integrations."""

from __future__ import annotations

import importlib.util
from typing import Any

from app.agent.external_backends import external_backend_status, select_agent_backend
from app.config import get_settings
from app.infra.store import store


def _available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _external_adapter(adapter_id: str, name: str) -> dict[str, Any]:
    details = external_backend_status(adapter_id)
    return {
        "id": adapter_id,
        "name": name,
        "status": details["status"],
        "version": details["command_executable"] if details["ready"] else None,
        "capabilities": ["coding-repair", "isolated-workspace", "model-bridge", "diff-validation", "critic"],
        "details": details,
    }


def list_runtime_adapters() -> list[dict[str, Any]]:
    settings = get_settings()
    extensions = store.list_extensions(status="active")
    mcp_enabled = any(item.name.startswith("mcp_") for item in extensions)
    skills_enabled = any(item.name.startswith("skill_") for item in extensions)
    return [
        {
            "id": "auto",
            "name": "成熟开源 Runtime 自动选择",
            "status": "active",
            "version": "policy-v1",
            "capabilities": ["external-first", "openhands", "mini-swe-agent", "langgraph-fallback"],
            "details": {
                "selected": select_agent_backend("auto", settings),
                "policy": "OpenHands -> mini-SWE-agent -> LangGraph",
            },
        },
        {
            "id": "native",
            "name": "ResearchForge Native Runtime",
            "status": "active",
            "version": "v1",
            "capabilities": ["coding-repair", "checkpoint", "budget", "critic"],
        },
        {
            "id": "langgraph",
            "name": "LangGraph Adapter",
            "status": "available" if _available("langgraph") else "unavailable",
            "version": "installed" if _available("langgraph") else None,
            "capabilities": ["state-graph", "checkpoint", "human-review"],
        },
        _external_adapter("openhands", "OpenHands Runtime Adapter"),
        _external_adapter("mini_swe_agent", "mini-SWE-agent Runtime Adapter"),
        {
            "id": "mcp",
            "name": "MCP Tool Adapter",
            "status": "configured" if mcp_enabled else "available",
            "version": "stdio/http",
            "capabilities": ["stdio", "streamable-http", "tool-discovery"],
        },
        {
            "id": "skills",
            "name": "Skills Registry",
            "status": "configured" if skills_enabled else "available",
            "version": "manifest-v1",
            "capabilities": ["versioned-skills", "hooks", "prompt-assets"],
        },
        {
            "id": "a2a",
            "name": "A2A / Sub-Agent Bridge",
            "status": "active",
            "version": "delegation-v1",
            "capabilities": ["sub-agent-routing", "delegation-contract", "budget-inheritance", "audit-trail"],
        },
        {
            "id": "runtime-policy",
            "name": "Runtime Policy",
            "status": "active",
            "version": settings.environment,
            "capabilities": ["rbac", "tool-approval", "tenant-isolation"],
        },
    ]


def validate_runtime_adapter(adapter: str) -> dict[str, Any]:
    normalized = adapter.strip().lower().replace("-", "_")
    item = next((entry for entry in list_runtime_adapters() if entry["id"] == normalized), None)
    if item is None:
        return {"adapter": normalized, "valid": False, "reason": "ADAPTER_NOT_FOUND"}
    valid = item["status"] not in {"unavailable", "planned"}
    payload = {
        "adapter": normalized,
        "valid": valid,
        "status": item["status"],
        "capabilities": item["capabilities"],
        "reason": None if valid else item.get("details", {}).get("reason") or f"ADAPTER_{str(item['status']).upper()}",
    }
    if "details" in item:
        payload["details"] = item["details"]
    return payload
