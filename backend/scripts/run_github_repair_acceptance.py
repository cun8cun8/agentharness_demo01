"""Run one connected GitHub repository repair through the public API.

The script never reads a GitHub token. Credentials stay in the API/worker
runtime, so this is suitable for CI runners and operator laptops alike. It
performs a real repository access check, queues the repair workflow, polls its
job, and writes an auditable JSON report.
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
        suffix = f": {detail.decode('utf-8', errors='replace')[:800]}" if detail else ""
        raise RuntimeError(f"request failed for {method} {path}{suffix}") from exc


def auth_headers(api_key_env: str, token_env: str) -> dict[str, str]:
    if os.getenv(token_env):
        return {"Authorization": f"Bearer {os.environ[token_env]}"}
    if os.getenv(api_key_env):
        return {"X-API-Key": os.environ[api_key_env]}
    return {}


def poll_job(base_url: str, job_id: str, headers: dict[str, str], timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            job = request_json(base_url, f"/api/v1/jobs/{urllib.parse.quote(job_id)}", headers=headers)
        except RuntimeError:
            time.sleep(3)
            continue
        if str(job.get("status") or "").lower() in {"completed", "failed", "cancelled", "paused"}:
            return job
        time.sleep(3)
    raise RuntimeError(f"repository repair job {job_id} did not finish within {timeout} seconds")


def repair_gate(job: dict[str, Any], *, publish: bool = False, push: bool = False, create_pull_request: bool = False,
                evidence: dict[str, Any] | None = None, model_name: str | None = None) -> dict[str, Any]:
    result = job.get("result_json") or {}
    metrics = result.get("metrics") or {}
    publication = result.get("publish") or {}
    pr = publication.get("pull_request") or {}
    tests_passed = metrics.get("tests_passed", metrics.get("validation_tests_passed"))
    tests_total = metrics.get("tests_total", metrics.get("validation_tests_total"))
    checks = {
        "job_completed": job.get("status") == "completed",
        "run_completed": result.get("run_status") == "completed" and bool(result.get("agent_run_id")),
        "tests_passed": isinstance(tests_total, int) and tests_total > 0 and tests_passed == tests_total,
        "no_test_changes": metrics.get("touched_tests") is False,
        "no_fallback": metrics.get("model_fallback_count") == 0 and not metrics.get("fallback_used", False),
        "no_policy_violations": metrics.get("policy_violation_count") == 0,
        "trace_complete": float(metrics.get("trace_completeness", 0)) >= 0.95,
    }
    record = evidence or {}
    run = record.get("run") or {}
    calls = [artifact.get("metadata") or {} for artifact in record.get("artifacts") or []
             if str(artifact.get("name") or "").startswith("model-assist-")]
    checks["real_model_evidence"] = bool(
        model_name and run.get("id") == result.get("agent_run_id") and run.get("model_name") == model_name
        and run.get("status") == "completed" and calls
        and all(call.get("provider") and call["provider"] != "mock"
                and call.get("fallback_used") is False and int(call.get("total_tokens", 0)) > 0 for call in calls)
    )
    if publish:
        checks["clean_source"] = metrics.get("source_clean") is True and bool(metrics.get("source_revision"))
        checks["publication_created"] = bool(publication.get("commit")) and publication.get("base_commit") == metrics.get("source_revision")
    if push:
        checks["branch_pushed"] = publication.get("pushed") is True
    if create_pull_request:
        checks["draft_pr_created"] = bool(pr.get("url") and pr.get("number")) and pr.get("draft") is True and not publication.get("pull_request_error")
    return {"checks": checks, "passed": all(checks.values())}


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run one ResearchForge GitHub repository repair acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--test-command", default="pytest -q")
    parser.add_argument("--model-name", default="qwen-plus")
    parser.add_argument("--branch", default="researchforge/automated-repair")
    parser.add_argument("--title", default="fix: ResearchForge automated repair")
    parser.add_argument("--body", default="由 ResearchForge 自动生成，等待人工审查。")
    parser.add_argument("--job-timeout-seconds", type=int, default=3_600)
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--token-env", default="RESEARCHFORGE_PROBE_TOKEN")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="opt in to pushing a protected researchforge/* branch and opening a draft PR",
    )
    args = parser.parse_args()
    if args.job_timeout_seconds < 1:
        parser.error("--job-timeout-seconds must be positive")
    if args.publish and not args.branch.startswith("researchforge/"):
        parser.error("--branch must start with researchforge/ when --publish is used")

    headers = auth_headers(args.api_key_env, args.token_env)
    repository_path = urllib.parse.quote(args.repository_id, safe="")
    health = request_json(
        args.base_url,
        f"/api/v1/integrations/repositories/{repository_path}/health?verify_access=true",
        headers=headers,
    )
    if health.get("provider") != "github":
        raise RuntimeError("repository must use the github provider for GitHub repair acceptance")
    if not health.get("remote_reachable"):
        raise RuntimeError(f"repository remote is not reachable: {health.get('message') or health.get('capability_message')}")
    if args.publish and not (health.get("write_access") and health.get("pull_request_access")):
        raise RuntimeError("GitHub write and pull-request access are required for --publish")

    repair_request = {
        "goal": args.goal,
        "test_command": args.test_command,
        "model_name": args.model_name,
        "publish": args.publish,
        "push": args.publish,
        "create_pull_request": args.publish,
        "branch": args.branch,
        "title": args.title,
        "body": args.body,
    }
    queued = request_json(
        args.base_url,
        f"/api/v1/integrations/repositories/{repository_path}/repair",
        method="POST",
        payload=repair_request,
        headers=headers,
    )
    job = queued.get("job")
    if not isinstance(job, dict) or not job.get("id"):
        raise RuntimeError(f"repair did not return a job: {json.dumps(queued, ensure_ascii=False)}")
    final_job = poll_job(args.base_url, str(job["id"]), headers, args.job_timeout_seconds)
    evidence = {}
    run_id = (final_job.get("result_json") or {}).get("agent_run_id")
    if run_id:
        run_path = f"/api/v1/runs/{urllib.parse.quote(str(run_id), safe='')}"
        evidence = {"run": request_json(args.base_url, run_path, headers=headers),
                    "artifacts": request_json(args.base_url, run_path + "/artifacts", headers=headers).get("items", [])}
    publications = request_json(
        args.base_url,
        f"/api/v1/integrations/repositories/{repository_path}/publications",
        headers=headers,
    )
    gate = repair_gate(final_job, publish=args.publish, push=args.publish, create_pull_request=args.publish,
                       evidence=evidence, model_name=args.model_name)
    report = {
        "repository_id": args.repository_id,
        "repository_health": health,
        "request": repair_request,
        "queue_response": queued,
        "job": final_job,
        "evidence": evidence,
        "publications": publications,
        "gate": gate,
        "passed": gate["passed"],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 2


def main() -> int:
    output_parser = argparse.ArgumentParser(add_help=False)
    output_parser.add_argument("--output", type=Path)
    output_args, _ = output_parser.parse_known_args()
    try:
        return _main()
    except RuntimeError as exc:
        if output_args.output is not None:
            output_args.output.parent.mkdir(parents=True, exist_ok=True)
            output_args.output.write_text(json.dumps({"schema_version": "repository-acceptance.failure.v1",
                                                     "passed": False, "error": str(exc)}, indent=2) + "\n",
                                          encoding="utf-8")
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"repository acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
