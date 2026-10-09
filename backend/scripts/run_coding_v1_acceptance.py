"""Run the production-oriented ten-task Coding Harness V1 acceptance.

The command requires an explicitly configured non-mock model by default. It is
intentionally API-driven so it works against the local full stack or a remote
deployment without handling provider credentials in the client process.
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
import uuid
from pathlib import Path
from typing import Any


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    body = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        detail = getattr(exc, "read", lambda: b"")()
        suffix = f": {detail.decode('utf-8', errors='replace')[:500]}" if detail else ""
        raise RuntimeError(f"request failed for {method} {path}{suffix}") from exc


def auth_headers(api_key_env: str, token_env: str) -> dict[str, str]:
    if os.getenv(token_env):
        return {"Authorization": f"Bearer {os.environ[token_env]}"}
    if os.getenv(api_key_env):
        return {"X-API-Key": os.environ[api_key_env]}
    return {}


def find_model(models: list[dict[str, Any]], model_name: str) -> dict[str, Any] | None:
    return next((item for item in models if item.get("model_name") == model_name), None)


def poll_job(base_url: str, job_id: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            job = request_json(base_url, f"/api/v1/jobs/{job_id}", headers=headers)
            last_error = None
        except RuntimeError as exc:
            # Keep polling through a brief API/container restart. The Job is
            # persisted, so a single failed status request must not discard a
            # valid long-running acceptance.
            last_error = exc
            time.sleep(3)
            continue
        status = str(job.get("status") or "").lower()
        if status in {"completed", "failed", "cancelled", "paused"}:
            return job
        time.sleep(3)
    if last_error is not None:
        raise RuntimeError(f"job {job_id} polling failed after {timeout} seconds: {last_error}") from last_error
    raise RuntimeError(f"job {job_id} did not finish within {timeout} seconds")


def write_failure_report(path: Path | None, *, acceptance: dict[str, Any] | None, error: Exception) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "coding-acceptance.failure.v1",
                "status": "failed",
                "acceptance": acceptance or {},
                "error": str(error),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def extract_evaluation_id(result: dict[str, Any]) -> str | None:
    acceptance = result.get("acceptance")
    if isinstance(acceptance, dict) and acceptance.get("evaluation_id"):
        return str(acceptance["evaluation_id"])
    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict) and evaluation.get("id"):
        return str(evaluation["id"])
    result_json = result.get("result_json")
    if isinstance(result_json, dict):
        for key in ("evaluation_id", "evaluation_run_id"):
            if result_json.get(key):
                return str(result_json[key])
    return None


def acceptance_gate(evaluation, evidence, *, expected_count, model_name, min_success_rate, require_all_passed, allow_mock=False):
    items = list(evaluation.get("items") or [])
    passed_count = sum(bool(item.get("success")) for item in items)
    success_rate = passed_count / len(items) if items else 0.0
    run_ids = [str(item.get("agent_run_id") or "") for item in items]
    complete = len(items) == expected_count and len(set(run_ids)) == expected_count and all(run_ids)
    matching = complete and all(
        evidence.get(run_id, {}).get("run", {}).get("id") == run_id
        and evidence[run_id]["run"].get("task_id") == item.get("task_id")
        and evidence[run_id]["run"].get("model_name") == model_name
        for run_id, item in zip(run_ids, items)
    )
    traces = [float(item.get("metrics", {}).get("trace_completeness", 0)) for item in items]
    trace_completeness = sum(traces) / len(traces) if traces else 0.0
    policy_violations = sum(int(item.get("metrics", {}).get("policy_violation_count", 0)) for item in items)
    real_runs = 0
    fallback_count = 0
    touched_tests = False
    for run_id in run_ids:
        record = evidence.get(run_id, {})
        metrics = record.get("run", {}).get("metrics", {})
        touched_tests = touched_tests or metrics.get("touched_tests") is not False
        calls = [a.get("metadata", {}) for a in record.get("artifacts", []) if a.get("name", "").startswith("model-assist-")]
        fallback_count += max(int(metrics.get("model_fallback_count", 0)), sum(bool(call.get("fallback_used")) for call in calls))
        real_runs += bool(calls) and all(
            call.get("provider") and call["provider"] != "mock"
            and call.get("fallback_used") is False and int(call.get("total_tokens", 0)) > 0
            for call in calls
        )
    all_passed = bool(items) and passed_count == len(items)
    return {
        "success_rate": round(success_rate, 4), "min_success_rate": min_success_rate,
        "task_count": len(items), "expected_task_count": expected_count, "passed_count": passed_count,
        "all_passed": all_passed, "require_all_passed": require_all_passed,
        "evidence_matches_batch": bool(matching), "real_model_run_count": real_runs,
        "fallback_count": fallback_count, "avg_trace_completeness": round(trace_completeness, 4),
        "policy_violation_count": policy_violations, "touched_tests": bool(touched_tests),
        "passed": bool(matching and success_rate >= min_success_rate
                       and trace_completeness >= 0.95 and policy_violations == 0 and not touched_tests
                       and (allow_mock or (real_runs == expected_count and fallback_count == 0))
                       and (not require_all_passed or all_passed)),
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run ResearchForge Coding Harness V1 acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--model-name", default="qwen-plus")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 11))
    parser.add_argument("--min-success-rate", type=float, default=0.70)
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--token-env", default="RESEARCHFORGE_PROBE_TOKEN")
    parser.add_argument("--job-timeout-seconds", type=int, default=3600)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--require-all-passed",
        action="store_true",
        help="fail unless every selected Golden Task satisfies its full contract",
    )
    parser.add_argument(
        "--allow-mock",
        action="store_true",
        help="allow mock model configuration; intended only for offline debugging",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="force a new acceptance batch instead of reusing an identical completed job",
    )
    parser.add_argument(
        "--job-id",
        help="resume polling an existing coding_acceptance Job instead of creating a new batch",
    )
    args = parser.parse_args()
    if not 0 <= args.min_success_rate <= 1:
        parser.error("--min-success-rate must be between 0 and 1")
    if args.job_id and args.fresh:
        parser.error("--job-id cannot be combined with --fresh")

    headers = auth_headers(args.api_key_env, args.token_env)
    validation = request_json(
        args.base_url,
        "/api/v1/benchmarks/golden-tasks/validation?benchmark_name=coding_golden_v1",
        headers=headers,
    )
    if not validation.get("all_valid") or int(validation.get("task_count", 0)) < args.limit:
        raise RuntimeError(f"Golden Task validation failed: {json.dumps(validation, ensure_ascii=False)}")

    models_response = request_json(
        args.base_url,
        "/api/v1/models?role=coding&status=active&limit=100",
        headers=headers,
    )
    model = find_model(list(models_response.get("items") or []), args.model_name)
    if model is None:
        raise RuntimeError(
            f"active coding model {args.model_name!r} is not registered; configure it before acceptance"
        )
    if not args.allow_mock and str(model.get("provider", "")).lower() == "mock":
        raise RuntimeError("mock model is not allowed; acceptance requires a real provider")

    health_response = request_json(
        args.base_url,
        "/api/v1/models/health?role=coding&status=active&verify_connectivity=true&limit=100",
        headers=headers,
    )
    model_health = next(
        (item for item in health_response.get("items", []) if item.get("model_id") == model.get("id")),
        None,
    )
    if not args.allow_mock and (not model_health or not model_health.get("healthy")):
        raise RuntimeError(f"model connectivity check failed: {json.dumps(model_health, ensure_ascii=False)}")

    acceptance_request = {
        "benchmark_name": "coding_golden_v1",
        "agent_strategy_id": "repair_with_critic_v3",
        "policy_version_id": "policy_default_v1",
        "model_name": args.model_name,
        "limit": args.limit,
    }
    if args.fresh:
        acceptance_request["acceptance_id"] = f"fresh-{uuid.uuid4().hex}"
    if args.job_id:
        acceptance_request["job_id"] = args.job_id
        result = request_json(
            args.base_url,
            f"/api/v1/jobs/{urllib.parse.quote(args.job_id)}",
            headers=headers,
        )
        if result.get("kind") != "coding_acceptance":
            raise RuntimeError(f"job {args.job_id} is not a coding acceptance job")
    else:
        try:
            result = request_json(
                args.base_url,
                "/api/v1/benchmarks/golden-tasks/acceptance",
                method="POST",
                payload=acceptance_request,
                headers=headers,
            )
        except RuntimeError as exc:
            write_failure_report(args.output, acceptance=acceptance_request, error=exc)
            raise
    if str(result.get("status", "")).lower() in {"queued", "running"}:
        job_id = str(result.get("job_id") or result.get("id") or "")
        if not job_id:
            error = RuntimeError("acceptance was queued without a job id")
            write_failure_report(args.output, acceptance=acceptance_request, error=error)
            raise error
        try:
            result = poll_job(args.base_url, job_id, headers, args.job_timeout_seconds)
        except RuntimeError as exc:
            write_failure_report(args.output, acceptance={**acceptance_request, "job_id": job_id}, error=exc)
            raise
        if str(result.get("status", "")).lower() != "completed":
            error = RuntimeError(f"acceptance job did not complete: {json.dumps(result, ensure_ascii=False)}")
            write_failure_report(args.output, acceptance={**acceptance_request, "job_id": job_id}, error=error)
            raise error
    elif str(result.get("status", "")).lower() in {"failed", "cancelled", "paused"}:
        error = RuntimeError(f"acceptance job did not complete: {json.dumps(result, ensure_ascii=False)}")
        write_failure_report(
            args.output,
            acceptance={**acceptance_request, "job_id": args.job_id or result.get("id")},
            error=error,
        )
        raise error

    evaluation_id = extract_evaluation_id(result)
    evaluation = (
        request_json(args.base_url, f"/api/v1/evaluations/runs/{evaluation_id}", headers=headers)
        if evaluation_id
        else result.get("evaluation")
    )
    if not isinstance(evaluation, dict):
        raise RuntimeError("acceptance completed without an evaluation result")

    evidence = {}
    for item in evaluation.get("items") or []:
        run_id = item.get("agent_run_id")
        if run_id:
            path = f"/api/v1/runs/{urllib.parse.quote(str(run_id), safe='')}"
            evidence[str(run_id)] = {
                "run": request_json(args.base_url, path, headers=headers),
                "artifacts": request_json(args.base_url, path + "/artifacts", headers=headers).get("items", []),
            }
    usage_summary = {
        "total_tokens": sum(int(record["run"].get("total_tokens", 0)) for record in evidence.values()),
        "estimated_cost": round(sum(float(record["run"].get("total_cost", 0)) for record in evidence.values()), 6),
        "duration_ms": sum(int(record["run"].get("duration_ms", 0)) for record in evidence.values()),
        "scope": "evaluation_runs",
    }
    report = {
        "schema_version": "coding-acceptance.v1",
        "acceptance": acceptance_request,
        "validation": validation,
        "model": model,
        "model_health": model_health,
        "evaluation": evaluation,
        "usage": usage_summary,
        "evidence": evidence,
        "gate": acceptance_gate(evaluation, evidence, expected_count=args.limit, model_name=args.model_name,
                                min_success_rate=args.min_success_rate, require_all_passed=args.require_all_passed,
                                allow_mock=args.allow_mock),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["gate"]["passed"] else 2


def main() -> int:
    output_parser = argparse.ArgumentParser(add_help=False)
    output_parser.add_argument("--output", type=Path)
    output_args, _ = output_parser.parse_known_args()
    try:
        return _main()
    except RuntimeError as exc:
        if output_args.output is not None:
            previous = {}
            try:
                previous = json.loads(output_args.output.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            acceptance = previous.get("acceptance") if previous.get("error") == str(exc) else None
            write_failure_report(output_args.output, acceptance=acceptance, error=exc)
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
