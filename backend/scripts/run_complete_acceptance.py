"""Run the complete local acceptance workflow with one command.

The workflow is intentionally API-driven so it exercises the same contracts as
the frontend and worker. It performs preflight checks, runs the strict Golden
Task acceptance, and can optionally run a connected-repository repair. GitHub
pushes and pull requests are opt-in flags and are never enabled by default.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
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
        suffix = f": {detail.decode('utf-8', errors='replace')[:800]}" if detail else ""
        raise RuntimeError(f"{method} {path} failed{suffix}") from exc


def auth_headers(api_key_env: str, token_env: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    api_key = os.getenv(api_key_env, "").strip() if api_key_env else ""
    token = os.getenv(token_env, "").strip() if token_env else ""
    if api_key:
        headers["X-API-Key"] = api_key
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def poll_job(
    base_url: str,
    job_id: str,
    headers: dict[str, str],
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = request_json(base_url, f"/api/v1/jobs/{urllib.parse.quote(job_id)}", headers=headers)
        status = str(last.get("status") or "").lower()
        if status in {"completed", "failed", "cancelled", "paused"}:
            return last
        time.sleep(3)
    raise RuntimeError(f"job {job_id} did not finish within {timeout_seconds} seconds")


def run_check(
    checks: list[dict[str, Any]],
    name: str,
    operation,
) -> Any:
    try:
        result = operation()
    except Exception as exc:  # Keep all checks in the final report.
        checks.append({"name": name, "passed": False, "error": str(exc)})
        return None
    checks.append({"name": name, "passed": True})
    return result


def preflight(args: argparse.Namespace, headers: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    details: dict[str, Any] = {}

    health = run_check(checks, "api_health", lambda: request_json(args.base_url, "/health", headers=headers))
    if health is not None:
        details["health"] = health
        if health.get("status") != "ok":
            checks[-1].update(passed=False, error="API health status is not ok")

    require_isolated_sandbox = bool(getattr(args, "require_isolated_sandbox", False))
    sandbox_check_path = "/api/v1/sandbox/check?verify_execution=true" if require_isolated_sandbox else "/api/v1/sandbox/check"
    for name, path in (
        ("api_ready", "/health/ready"),
        ("system_readiness", "/api/v1/system/readiness"),
        ("sandbox_check", sandbox_check_path),
        ("sandbox_status", "/api/v1/sandbox/status"),
        ("adapters", "/api/v1/adapters"),
    ):
        value = run_check(checks, name, lambda path=path: request_json(args.base_url, path, headers=headers))
        if value is not None:
            details[name] = value
            if name == "sandbox_check" and require_isolated_sandbox:
                backend = str(value.get("backend") or "")
                if backend not in {"docker", "kubernetes"} or not value.get("execution_ready"):
                    checks[-1].update(
                        passed=False,
                        error=f"isolated sandbox is not ready: backend={backend or 'unknown'}",
                    )

    validation = run_check(
        checks,
        "golden_task_validation",
        lambda: request_json(
            args.base_url,
            "/api/v1/benchmarks/golden-tasks/validation?benchmark_name=coding_golden_v1",
            headers=headers,
        ),
    )
    if validation is not None:
        details["golden_task_validation"] = validation
        if not validation.get("all_valid") or int(validation.get("task_count", 0)) < args.limit:
            checks[-1].update(
                passed=False,
                error=f"coding_golden_v1 has fewer than {args.limit} valid tasks",
            )

    a2a_card = run_check(
        checks,
        "a2a_agent_card",
        lambda: request_json(args.base_url, "/api/v1/a2a/agent-card", headers=headers),
    )
    if a2a_card is not None:
        capabilities = a2a_card.get("capabilities") or {}
        if not capabilities.get("delegation") or int(capabilities.get("max_delegation_depth") or 0) < 1:
            checks[-1].update(passed=False, error="A2A delegation card is incomplete")

    models = run_check(
        checks,
        "coding_model_list",
        lambda: request_json(
            args.base_url,
            "/api/v1/models?role=coding&status=active&limit=100",
            headers=headers,
        ),
    )
    if models is not None:
        items = list(models.get("items") or [])
        model = next((item for item in items if item.get("model_name") == args.model_name), None)
        details["model"] = model
        if model is None:
            checks[-1].update(passed=False, error=f"active coding model is missing: {args.model_name}")
        elif not args.allow_mock and str(model.get("provider", "")).lower() == "mock":
            checks[-1].update(passed=False, error="mock model is not allowed for complete acceptance")

    model_health = run_check(
        checks,
        "coding_model_connectivity",
        lambda: request_json(
            args.base_url,
            "/api/v1/models/health?role=coding&status=active&verify_connectivity=true&limit=100",
            headers=headers,
        ),
    )
    if model_health is not None:
        selected = next(
            (item for item in model_health.get("items", []) if item.get("model_name") == args.model_name),
            None,
        )
        details["model_health"] = selected
        if selected is None or (not args.allow_mock and not selected.get("healthy")):
            checks[-1].update(passed=False, error=f"model connectivity failed: {selected}")

    return checks, details


def _transient_acceptance_failure(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    return any(
        marker in combined
        for marker in (
            "duplicate_after_local_poll_interruption",
            "did not complete",
            "did not finish within",
            "timed out",
            "connection reset",
            "connection refused",
            "http 5",
        )
    )


def run_golden_acceptance(args: argparse.Namespace, headers: dict[str, str]) -> dict[str, Any]:
    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    runner = ROOT / "backend" / "scripts" / "run_coding_v1_acceptance.py"
    attempts: list[dict[str, Any]] = []
    max_attempts = max(1, args.acceptance_retries + 1)
    resume_job_id: str | None = args.resume_job_id

    for attempt in range(1, max_attempts + 1):
        attempt_output = output_path.with_name(f"{output_path.stem}-attempt-{attempt}{output_path.suffix}")
        command = [
            sys.executable,
            str(runner),
            "--base-url",
            args.base_url,
            "--model-name",
            args.model_name,
            "--limit",
            str(args.limit),
            "--require-all-passed",
            "--job-timeout-seconds",
            str(args.job_timeout_seconds),
            "--output",
            str(attempt_output),
        ]
        if args.fresh_golden and not resume_job_id:
            command.append("--fresh")
        if resume_job_id:
            command.extend(["--job-id", resume_job_id])
        if args.allow_mock:
            command.append("--allow-mock")
        if args.api_key_env:
            command.extend(["--api-key-env", args.api_key_env])
        if args.token_env:
            command.extend(["--token-env", args.token_env])
        try:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=args.job_timeout_seconds + 180,
                check=False,
            )
            stdout = completed.stdout[-4000:]
            stderr = completed.stderr[-4000:]
            parsed = None
            if attempt_output.exists():
                parsed = json.loads(attempt_output.read_text(encoding="utf-8"))
            attempts.append(
                {
                    "attempt": attempt,
                    "returncode": completed.returncode,
                    "report_path": str(attempt_output),
                    "gate": (parsed or {}).get("gate"),
                    "stdout_tail": stdout,
                    "stderr_tail": stderr,
                }
            )
            if parsed and (parsed.get("gate") or {}).get("passed"):
                output_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return {"passed": True, "report": parsed, "attempts": attempts, "report_path": str(output_path)}
            if _transient_acceptance_failure(stdout, stderr) and parsed:
                candidate = (parsed.get("acceptance") or {}).get("job_id")
                if candidate:
                    try:
                        existing = request_json(
                            args.base_url,
                            f"/api/v1/jobs/{urllib.parse.quote(str(candidate))}",
                            headers=headers,
                        )
                    except Exception:
                        existing = {}
                    if str(existing.get("status") or "").lower() in {"queued", "running"}:
                        resume_job_id = str(candidate)
            if not _transient_acceptance_failure(stdout, stderr) or attempt >= max_attempts:
                break
        except subprocess.TimeoutExpired as exc:
            attempts.append(
                {
                    "attempt": attempt,
                    "returncode": None,
                    "timed_out": True,
                    "stdout_tail": str(exc.stdout or "")[-4000:],
                    "stderr_tail": str(exc.stderr or "")[-4000:],
                }
            )
            if attempt >= max_attempts:
                break

    summary = {"passed": False, "attempts": attempts, "report_path": str(output_path)}
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def run_repository_repair(args: argparse.Namespace, headers: dict[str, str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "goal": args.repository_goal,
        "test_command": args.repository_test_command,
        "test_timeout_seconds": args.repository_test_timeout_seconds,
        "agent_strategy_id": "repair_with_critic_v3",
        "policy_version_id": "policy_default_v1",
        "model_name": args.model_name,
        "publish": args.publish,
        "branch": args.repository_branch,
        "title": args.repository_title,
        "body": args.repository_body,
        "push": args.push,
        "create_pull_request": args.create_pull_request,
    }
    # Reuse an identical active request so retries from a disconnected client
    # cannot create two model runs against the same repository.
    existing_items: list[dict[str, Any]] = []
    for status in ("queued", "running", "completed"):
        existing = request_json(
            args.base_url,
            "/api/v1/jobs?kind=repository_repair&resource_id="
            + urllib.parse.quote(args.repository_id)
            + f"&status={status}&limit=100",
            headers=headers,
        )
        existing_items.extend(existing.get("items") or [])
    request_signature = {
        key: payload[key]
        for key in ("goal", "test_command", "model_name", "publish", "push", "create_pull_request", "branch")
    }
    reusable = next(
        (
            item
            for item in existing_items
            if all((item.get("metadata") or {}).get("request", {}).get(key) == value for key, value in request_signature.items())
        ),
        None,
    )
    response = {"job": reusable, "status": "reused"} if reusable else request_json(
        args.base_url,
        f"/api/v1/integrations/repositories/{urllib.parse.quote(args.repository_id)}/repair",
        method="POST",
        payload=payload,
        headers=headers,
        timeout=60,
    )
    job = response.get("job") or {}
    job_id = str(job.get("id") or "")
    if not job_id:
        raise RuntimeError("repository repair returned no job id")
    final = poll_job(args.base_url, job_id, headers, args.repository_timeout_seconds)
    result_json = final.get("result_json") or {}
    metrics = result_json.get("metrics") or {}
    publish = result_json.get("publish") or {}
    repository_summary = {
        "job_id": job_id,
        "task_id": result_json.get("task_id"),
        "run_id": result_json.get("agent_run_id"),
        "status": final.get("status"),
        "tests_passed": metrics.get("tests_passed", metrics.get("validation_tests_passed")),
        "tests_total": metrics.get("tests_total", metrics.get("validation_tests_total")),
        "changed_files": metrics.get("changed_files"),
        "changed_lines": metrics.get("changed_lines"),
        "touched_tests": metrics.get("touched_tests", False),
        "fallback_used": metrics.get("fallback_used", False),
        "total_tokens": metrics.get("total_tokens", metrics.get("tokens_used")),
        "total_cost": metrics.get("total_cost", metrics.get("model_cost_used")),
        "branch": publish.get("branch"),
        "commit": publish.get("commit"),
        "base_commit": publish.get("base_commit"),
        "pull_request": publish.get("pull_request"),
        "publish_reused": publish.get("push_reused", False),
    }
    result = {
        "request": {key: value for key, value in payload.items() if key not in {"body"}},
        "job": final,
        "reused_existing": bool(reusable),
        "summary": repository_summary,
        "passed": str(final.get("status") or "").lower() == "completed",
    }
    return result


def markdown_report(report: dict[str, Any]) -> str:
    checks = report.get("checks") or []
    lines = [
        "# ResearchForge 自动验收报告",
        "",
        f"- 总结：{'通过' if report.get('passed') else '未通过'}",
        f"- API：`{report.get('base_url')}`",
        f"- 模型：`{report.get('model_name')}`",
        "",
        "## 预检",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
    ]
    for item in checks:
        lines.append(f"| {item.get('name')} | {'通过' if item.get('passed') else '失败'} |")
    acceptance = report.get("golden_acceptance") or {}
    gate = (acceptance.get("report") or {}).get("gate") or {}
    if acceptance.get("skipped"):
        golden_result = f"已跳过（{acceptance.get('reason', '按请求跳过')}）"
        golden_counts = "未执行"
        golden_rate = "未执行"
        golden_cost = "未执行"
    else:
        golden_result = "通过" if acceptance.get("passed") else "失败"
        golden_counts = f"{gate.get('passed_count', '-')}/{gate.get('task_count', '-')}"
        golden_rate = gate.get("success_rate", "-")
        golden_cost = ((acceptance.get("report") or {}).get("usage") or {}).get("estimated_cost", "-")
    lines.extend(
        [
            "",
            "## Golden Task",
            "",
            f"- 结果：{golden_result}",
            f"- 通过数：`{golden_counts}`",
            f"- 成功率：`{golden_rate}`",
            f"- 估算成本：`{golden_cost}`",
        ]
    )
    repository = report.get("repository_repair")
    if repository is not None:
        summary = repository.get("summary") or {}
        lines.extend(
            [
                "",
                "## 真实仓库修复",
                "",
                f"- 结果：{'通过' if repository.get('passed') else '失败'}",
                f"- Job：`{summary.get('job_id') or '-'}`",
                f"- Run：`{summary.get('run_id') or '-'}`",
                f"- 测试：`{summary.get('tests_passed', '-')}/{summary.get('tests_total', '-')}`",
                f"- 代码变更：`{summary.get('changed_files', '-')} 个文件 / {summary.get('changed_lines', '-')} 行`",
                f"- 测试文件变更：`{'是' if summary.get('touched_tests') else '否'}`",
                f"- 模型回退：`{'是' if summary.get('fallback_used') else '否'}`",
                f"- Token / 成本：`{summary.get('total_tokens', '-')} / {summary.get('total_cost', '-')}`",
            ]
        )
        if summary.get("branch"):
            lines.append(f"- 分支：`{summary['branch']}`")
        if summary.get("commit"):
            lines.append(f"- 提交：`{summary['commit']}`")
        pull_request = summary.get("pull_request")
        if isinstance(pull_request, dict) and pull_request.get("url"):
            lines.append(f"- Draft PR：{pull_request['url']}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the complete ResearchForge local acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--model-name", default="qwen-plus")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 11))
    parser.add_argument("--job-timeout-seconds", type=int, default=3600)
    parser.add_argument("--acceptance-retries", type=int, default=1)
    parser.add_argument(
        "--fresh-golden",
        action="store_true",
        help="force a new Golden Task acceptance batch and record its unique batch ID",
    )
    parser.add_argument(
        "--resume-job-id",
        help="resume polling an existing coding acceptance Job instead of creating a new batch",
    )
    parser.add_argument("--allow-mock", action="store_true")
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--token-env", default="RESEARCHFORGE_PROBE_TOKEN")
    parser.add_argument("--output", default=".run/acceptance/complete-latest.json")
    parser.add_argument("--skip-golden", action="store_true")
    parser.add_argument(
        "--require-isolated-sandbox",
        action="store_true",
        help="require Docker/Kubernetes execution during preflight instead of accepting local-tempdir mode",
    )
    parser.add_argument(
        "--with-golden",
        action="store_true",
        help="also run Golden Task acceptance before a repository repair; otherwise repository mode skips it",
    )
    parser.add_argument("--repository-id")
    parser.add_argument("--repository-goal", default="修复仓库缺陷，不修改测试文件，并让测试全部通过。")
    parser.add_argument("--repository-test-command", default="pytest -q")
    parser.add_argument("--repository-test-timeout-seconds", type=int, default=180)
    parser.add_argument("--repository-timeout-seconds", type=int, default=1800)
    parser.add_argument("--repository-branch", default="researchforge/repair")
    parser.add_argument("--repository-title", default="ResearchForge automated repair")
    parser.add_argument("--repository-body", default="Automated repair generated by ResearchForge Agent Harness.")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--create-pull-request", action="store_true")
    args = parser.parse_args()
    if args.acceptance_retries < 0:
        parser.error("--acceptance-retries must be non-negative")
    if args.resume_job_id and args.fresh_golden:
        parser.error("--resume-job-id cannot be combined with --fresh-golden")
    if args.create_pull_request and not args.push:
        parser.error("--create-pull-request requires --push")
    if (args.publish or args.push or args.create_pull_request) and not args.repository_id:
        parser.error("publication flags require --repository-id")
    if args.create_pull_request and not args.publish:
        parser.error("--create-pull-request requires --publish")

    headers = auth_headers(args.api_key_env, args.token_env)
    checks, details = preflight(args, headers)
    report: dict[str, Any] = {
        "schema_version": "complete-acceptance.v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "model_name": args.model_name,
        "limit": args.limit,
        "checks": checks,
        "preflight": details,
        "passed": False,
    }

    if all(item["passed"] for item in checks):
        run_golden = not args.skip_golden and (not args.repository_id or args.with_golden)
        if run_golden:
            report["golden_acceptance"] = run_golden_acceptance(args, headers)
        else:
            report["golden_acceptance"] = {
                "passed": True,
                "skipped": True,
                "reason": "repository_mode_uses_existing_golden_baseline" if args.repository_id else "requested",
            }
        if args.repository_id and report["golden_acceptance"].get("passed"):
            try:
                report["repository_repair"] = run_repository_repair(args, headers)
            except Exception as exc:
                report["repository_repair"] = {"passed": False, "error": str(exc)}
    else:
        report["golden_acceptance"] = {"passed": False, "skipped": True, "reason": "preflight_failed"}

    report["passed"] = bool(
        all(item["passed"] for item in checks)
        and report.get("golden_acceptance", {}).get("passed")
        and (not args.repository_id or report.get("repository_repair", {}).get("passed"))
    )
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = output.with_suffix(".md")
    markdown.write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "output": str(output), "markdown": str(markdown)}, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"complete acceptance failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
