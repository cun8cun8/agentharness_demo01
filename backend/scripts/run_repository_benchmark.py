"""Run a repeatable repair benchmark against a connected repository.

The script deliberately uses the public API so the report measures the same
workflow as a user: repository sync, task creation, Agent Run, and evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SUPPORTED_AGENT_BACKENDS = {"native", "langgraph", "openhands", "openhands_cli", "mini_swe_agent"}


def _request(
    base_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request_headers.update(headers or {})
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail[:500]}") from exc


def _poll(
    base_url: str,
    path: str,
    *,
    timeout: int,
    terminal: set[str],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _request(base_url, "GET", path, headers=headers)
        status = str(last.get("status") or last.get("job", {}).get("status") or "").lower()
        if status in terminal:
            return last
        time.sleep(1)
    raise TimeoutError(f"poll timeout: {path}; last={last}")


def _load_tasks(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return [{
            "title": "Repository smoke repair",
            "goal": "Find and fix the failing test in this repository without changing tests.",
            "test_command": "pytest -q",
        }]
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("tasks JSON must be a non-empty array")
    return [dict(item) for item in raw[:50] if isinstance(item, dict)]


def _auth_headers(api_key_env: str | None, token_env: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key_env:
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise ValueError(f"API key environment variable is empty: {api_key_env}")
        headers["X-API-Key"] = api_key
    if token_env:
        token = os.getenv(token_env, "").strip()
        if not token:
            raise ValueError(f"Bearer token environment variable is empty: {token_env}")
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _normalize_backend(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_AGENT_BACKENDS:
        supported = ", ".join(sorted(SUPPORTED_AGENT_BACKENDS))
        raise ValueError(f"Unsupported agent backend: {value}. Supported values: {supported}")
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="ResearchForge real repository repair benchmark")
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--tasks-json")
    parser.add_argument("--model-name")
    parser.add_argument("--agent-backend", default="native", help="native, langgraph, openhands, or mini_swe_agent")
    parser.add_argument("--agent-strategy-id", default="repair_with_critic_v3")
    parser.add_argument("--policy-version-id", default="policy_default_v1")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--min-success-rate", type=float, default=0.8)
    parser.add_argument("--allow-test-changes", action="store_true")
    parser.add_argument("--max-total-cost", type=float)
    parser.add_argument("--max-average-duration-seconds", type=float)
    parser.add_argument("--api-key-env", help="Environment variable containing X-API-Key")
    parser.add_argument("--token-env", help="Environment variable containing a Bearer token")
    parser.add_argument("--output", default=".run/repository-benchmark.json")
    args = parser.parse_args()
    if not 0 <= args.min_success_rate <= 1:
        parser.error("--min-success-rate must be between 0 and 1")
    if args.max_total_cost is not None and args.max_total_cost < 0:
        parser.error("--max-total-cost must be non-negative")
    if args.max_average_duration_seconds is not None and args.max_average_duration_seconds < 0:
        parser.error("--max-average-duration-seconds must be non-negative")
    agent_backend = _normalize_backend(args.agent_backend)
    headers = _auth_headers(args.api_key_env, args.token_env)

    started = time.time()
    repository = _request(args.base_url, "GET", f"/api/v1/integrations/repositories/{args.repository_id}", headers=headers)
    health = _request(args.base_url, "GET", f"/api/v1/integrations/repositories/{args.repository_id}/health", headers=headers)
    if repository.get("provider") != "local" or not health.get("path_exists"):
        sync = _request(args.base_url, "POST", f"/api/v1/integrations/repositories/{args.repository_id}/sync", headers=headers)
        job = sync.get("job") or {}
        if job.get("id") and str(job.get("status")) not in {"completed", "failed"}:
            _poll(args.base_url, f"/api/v1/jobs/{job['id']}", timeout=args.timeout_seconds, terminal={"completed", "failed", "cancelled"}, headers=headers)
        health = _request(args.base_url, "GET", f"/api/v1/integrations/repositories/{args.repository_id}/health", headers=headers)
    if not health.get("cache_path") or not health.get("path_exists"):
        raise RuntimeError(f"repository is not ready: {health}")

    tasks = _load_tasks(args.tasks_json)
    items: list[dict[str, Any]] = []
    for index, item in enumerate(tasks, start=1):
        execution_config = {"source": "repository_benchmark", "benchmark_index": index, "agent_backend": agent_backend}
        if item.get("setup_commands"):
            execution_config["setup_commands"] = item["setup_commands"]
        if agent_backend == "langgraph":
            execution_config["runtime"] = "langgraph"
        task_payload = {
            "type": "coding",
            "title": item.get("title") or f"Repository benchmark task {index}",
            "workspace_id": repository.get("workspace_id") or "workspace_default",
            "repo_path": health["cache_path"],
            "test_command": item.get("test_command", "pytest -q"),
            "test_timeout_seconds": int(item.get("test_timeout_seconds", 120)),
            "goal": item.get("goal") or item.get("description") or "修复仓库问题并通过测试。",
            "execution_config": execution_config,
            "budget": item.get("budget") or {},
        }
        task = _request(args.base_url, "POST", "/api/v1/tasks", task_payload, headers=headers)
        run = _request(args.base_url, "POST", f"/api/v1/tasks/{task['id']}/runs", {
            "agent_strategy_id": args.agent_strategy_id,
            "policy_version_id": args.policy_version_id,
            "model_name": args.model_name,
        }, headers=headers)
        final = _poll(
            args.base_url,
            f"/api/v1/runs/{run['id']}",
            timeout=args.timeout_seconds,
            terminal={"completed", "failed", "blocked", "cancelled", "paused"},
            headers=headers,
        )
        metrics = final.get("metrics") or {}
        items.append({
            "index": index,
            "title": task_payload["title"],
            "task_id": task["id"],
            "run_id": final.get("id"),
            "status": final.get("status"),
            "model_name": final.get("model_name"),
            "agent_backend": metrics.get("agent_backend", agent_backend),
            "duration_ms": final.get("duration_ms", 0),
            "total_tokens": final.get("total_tokens", 0),
            "total_cost": final.get("total_cost", 0),
            "validation_retry_count": metrics.get("validation_retry_count", 0),
            "changed_files": metrics.get("changed_files", 0),
            "changed_lines": metrics.get("changed_lines", 0),
            "touched_tests": metrics.get("touched_tests", False),
            "fallback_count": metrics.get("model_fallback_count", 0),
            "external_agent_uninstrumented": metrics.get("external_agent_uninstrumented", False),
            "regression": bool(metrics.get("test_passed_delta", 0) < 0),
            "trace_completeness": metrics.get("trace_completeness", 0),
            "error_summary": final.get("error_summary"),
        })
        print(f"[{index}/{len(tasks)}] {items[-1]['status']} {items[-1]['title']}")

    successful = [item for item in items if item["status"] == "completed"]
    total = len(items)
    total_cost = round(sum(float(item["total_cost"] or 0) for item in items), 6)
    average_duration_ms = round(sum(float(item["duration_ms"] or 0) for item in items) / total, 2) if total else 0
    touched_tests_count = sum(bool(item["touched_tests"]) for item in items)
    fallback_count = sum(int(item["fallback_count"] or 0) for item in items)
    cost_within_budget = args.max_total_cost is None or total_cost <= args.max_total_cost
    duration_within_budget = (
        args.max_average_duration_seconds is None
        or average_duration_ms / 1000 <= args.max_average_duration_seconds
    )
    test_changes_allowed = args.allow_test_changes or touched_tests_count == 0
    report = {
        "schema_version": "repository-benchmark.v1",
        "repository_id": args.repository_id,
        "repository": repository,
        "health": health,
        "model_name": args.model_name,
        "agent_backend": agent_backend,
        "strategy_id": args.agent_strategy_id,
        "task_count": total,
        "success_count": len(successful),
        "success_rate": round(len(successful) / total, 4) if total else 0,
        "first_pass_success_rate": round(sum(item["validation_retry_count"] == 0 and item["status"] == "completed" for item in items) / total, 4) if total else 0,
        "average_duration_ms": average_duration_ms,
        "total_tokens": sum(int(item["total_tokens"] or 0) for item in items),
        "total_cost": total_cost,
        "average_repair_rounds": round(1 + sum(int(item["validation_retry_count"] or 0) for item in items) / total, 2) if total else 0,
        "changed_files": sum(int(item["changed_files"] or 0) for item in items),
        "changed_lines": sum(int(item["changed_lines"] or 0) for item in items),
        "touched_tests_count": touched_tests_count,
        "regression_count": sum(bool(item["regression"]) for item in items),
        "fallback_count": fallback_count,
        "acceptance_gate": {
            "passed": bool(
                total
                and len(successful) / total >= args.min_success_rate
                and fallback_count == 0
                and test_changes_allowed
                and cost_within_budget
                and duration_within_budget
            ),
            "min_success_rate": args.min_success_rate,
            "requires_zero_fallback": True,
            "requires_zero_test_changes": not args.allow_test_changes,
            "test_changes_allowed": test_changes_allowed,
            "max_total_cost": args.max_total_cost,
            "cost_within_budget": cost_within_budget,
            "max_average_duration_seconds": args.max_average_duration_seconds,
            "duration_within_budget": duration_within_budget,
        },
        "duration_seconds": round(time.time() - started, 2),
        "items": items,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "acceptance_gate": report["acceptance_gate"]}, ensure_ascii=False))
    return 0 if report["acceptance_gate"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
