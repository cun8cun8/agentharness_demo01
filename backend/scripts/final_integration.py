"""Run the final API-level integration checks for a ResearchForge deployment.

The default mode is read-only. ``--run-smoke`` additionally seeds the Golden
Task catalog, starts one bounded coding Run, waits for its terminal state, and
checks that the Run produced trace evidence instead of merely returning HTTP
200 from the start endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "read", lambda: b"")()
        suffix = f": {detail.decode('utf-8', errors='replace')[:600]}" if detail else ""
        raise RuntimeError(f"{method} {path} failed{suffix}") from exc


def headers_for(args: argparse.Namespace) -> dict[str, str]:
    if os.getenv(args.token_env):
        return {"Authorization": f"Bearer {os.environ[args.token_env]}"}
    if os.getenv(args.api_key_env):
        return {"X-API-Key": os.environ[args.api_key_env]}
    return {}


def check(name: str, fn, checks: list[dict[str, Any]]) -> Any:
    try:
        result = fn()
        checks.append({"name": name, "passed": True})
        return result
    except Exception as exc:  # the report should include all failures
        checks.append({"name": name, "passed": False, "error": str(exc)})
        return None


def poll_run(base_url: str, run_id: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = request_json(base_url, f"/api/v1/runs/{urllib.parse.quote(run_id)}", headers=headers)
        if str(run.get("status") or "").lower() in {"completed", "failed", "cancelled", "blocked", "paused"}:
            return run
        time.sleep(2)
    raise RuntimeError(f"run {run_id} did not reach a terminal state within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ResearchForge final integration checks")
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--token-env", default="RESEARCHFORGE_PROBE_TOKEN")
    parser.add_argument("--run-smoke", action="store_true", help="start one real Golden Task Run")
    parser.add_argument("--model-name", default="mock-coding-agent")
    parser.add_argument(
        "--require-real-model",
        action="store_true",
        help="require an active non-mock model and reject fallback during the smoke run",
    )
    parser.add_argument("--smoke-timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=str)
    args = parser.parse_args()
    headers = headers_for(args)
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {"base_url": args.base_url, "checks": checks}

    health = check("health", lambda: request_json(args.base_url, "/health"), checks)
    if health is not None and health.get("status") != "ok":
        checks[-1].update(passed=False, error="health status is not ok")
    check("health_ready", lambda: request_json(args.base_url, "/health/ready", headers=headers), checks)
    readiness = check("system_readiness", lambda: request_json(args.base_url, "/api/v1/system/readiness", headers=headers), checks)
    check("sandbox_check", lambda: request_json(args.base_url, "/api/v1/sandbox/check", headers=headers), checks)
    validation = check(
        "golden_task_validation",
        lambda: request_json(args.base_url, "/api/v1/benchmarks/golden-tasks/validation?benchmark_name=coding_golden_v1", headers=headers),
        checks,
    )
    if validation is not None and (not validation.get("all_valid") or int(validation.get("task_count", 0)) < 10):
        checks[-1].update(passed=False, error="coding_golden_v1 must contain ten valid tasks")
    models = check("coding_models", lambda: request_json(args.base_url, "/api/v1/models?role=coding&status=active&limit=100", headers=headers), checks)
    if models is not None and not models.get("items"):
        checks[-1].update(passed=False, error="no active coding model is registered")
    if args.require_real_model and models is not None:
        real_models = [item for item in models.get("items", []) if str(item.get("provider", "")).lower() != "mock"]
        if not real_models:
            checks[-1].update(passed=False, error="no active non-mock coding model is registered")
    for name, path in (
        ("tools", "/api/v1/tools"),
        ("strategies", "/api/v1/strategies?status=active&limit=100"),
        ("a2a_agent_card", "/api/v1/a2a/agent-card"),
        ("extensions", "/api/v1/extensions"),
        ("job_summary", "/api/v1/jobs/summary"),
    ):
        check(name, lambda path=path: request_json(args.base_url, path, headers=headers), checks)

    if args.run_smoke:
        seed = check(
            "seed_golden_tasks",
            lambda: request_json(args.base_url, "/api/v1/benchmarks/golden-tasks/seed", method="POST", headers=headers),
            checks,
        )
        items = list((seed or {}).get("items") or [])
        if not items:
            checks.append({"name": "smoke_run", "passed": False, "error": "seed returned no tasks"})
        else:
            task_id = str(items[0].get("id"))
            started = check(
                "start_smoke_run",
                lambda: request_json(
                    args.base_url,
                    f"/api/v1/tasks/{urllib.parse.quote(task_id)}/runs",
                    method="POST",
                    payload={
                        "agent_strategy_id": "repair_with_critic_v3",
                        "policy_version_id": "policy_default_v1",
                        "model_name": args.model_name,
                    },
                    headers=headers,
                ),
                checks,
            )
            run_id = str((started or {}).get("id") or "")
            if not run_id:
                checks.append({"name": "smoke_run", "passed": False, "error": "run start returned no id"})
            else:
                run = check("smoke_run", lambda: poll_run(args.base_url, run_id, headers, args.smoke_timeout_seconds), checks)
                report["smoke_run"] = run
                if run is not None and str(run.get("status")) not in {"completed", "failed", "blocked", "paused", "cancelled"}:
                    checks[-1].update(passed=False, error="run did not reach a terminal status")
                if args.require_real_model and run is not None:
                    if str(run.get("status")) != "completed":
                        checks[-1].update(passed=False, error=f"real-model smoke run ended with {run.get('status')}")
                    metrics = run.get("metrics") or {}
                    if str(run.get("model_name") or "").lower().startswith("mock") or int(metrics.get("model_fallback_count", 0) or 0) > 0:
                        checks.append({"name": "smoke_real_model", "passed": False, "error": "smoke run used a mock model or fallback"})
                    else:
                        checks.append({"name": "smoke_real_model", "passed": True})
                steps = check("smoke_trace_steps", lambda: request_json(args.base_url, f"/api/v1/runs/{run_id}/steps", headers=headers), checks)
                calls = check("smoke_trace_tool_calls", lambda: request_json(args.base_url, f"/api/v1/runs/{run_id}/tool-calls", headers=headers), checks)
                artifacts = check("smoke_trace_artifacts", lambda: request_json(args.base_url, f"/api/v1/runs/{run_id}/artifacts", headers=headers), checks)
                if steps is not None and not steps.get("items", steps):
                    checks[-3].update(passed=False, error="run produced no agent steps")
                if calls is not None and not calls.get("items", calls):
                    checks[-2].update(passed=False, error="run produced no tool calls")
                if artifacts is not None and not artifacts.get("items", artifacts):
                    checks[-1].update(passed=False, error="run produced no artifacts")

    report["readiness"] = readiness
    report["validation"] = validation
    report["passed"] = all(item["passed"] for item in checks)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
