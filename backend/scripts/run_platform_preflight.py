"""Run a safe, machine-readable readiness check for a ResearchForge deployment.

The script calls public diagnostics only. It never prints credential values, sends
model inference requests, changes repositories, or creates sandbox workloads.
Use --verify-runtime when the deployment is already configured for an isolated
sandbox and a real model connectivity probe is desired.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _headers(api_key_env: str) -> dict[str, str]:
    token = os.getenv(api_key_env, "").strip()
    return {"X-API-Key": token} if token else {}


def _request(base_url: str, path: str, headers: dict[str, str]) -> tuple[bool, int | None, Any]:
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        headers={"Accept": "application/json", **headers},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            return True, response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail: Any = json.loads(body)
        except json.JSONDecodeError:
            detail = {"error": f"HTTP_{exc.code}"}
        return False, exc.code, detail
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, None, {"error": type(exc).__name__, "message": str(exc)}


def _check(name: str, ok: bool, status_code: int | None, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "status_code": status_code, "detail": detail}


def _summary(payload: Any, *keys: str) -> dict[str, Any]:
    """Keep preflight evidence concise and avoid copying arbitrary API payloads."""
    source = payload if isinstance(payload, dict) else {}
    return {key: source.get(key) for key in keys if key in source}


def _failed_check_ids(payload: Any) -> list[str]:
    source = payload if isinstance(payload, dict) else {}
    checks = source.get("checks")
    if not isinstance(checks, list):
        return []
    return [
        str(item.get("id") or item.get("name"))
        for item in checks
        if isinstance(item, dict) and not item.get("passed", item.get("ok", False))
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ResearchForge API and integration preflight checks")
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--api-key-env", default="RESEARCHFORGE_API_KEY")
    parser.add_argument("--repository-id")
    parser.add_argument("--verify-runtime", action="store_true")
    parser.add_argument(
        "--allow-offline",
        action="store_true",
        help="允许本地离线联调缺少真实模型；生产门禁仍会单独失败",
    )
    parser.add_argument("--require-production", action="store_true")
    parser.add_argument("--require-github-publish", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    headers = _headers(args.api_key_env)
    checks: list[dict[str, Any]] = []
    ok, status, payload = _request(args.base_url, "/health", headers)
    health = _summary(payload, "status")
    checks.append(_check("api_health", ok and health.get("status") == "ok", status, health))

    ok, status, payload = _request(args.base_url, "/api/v1/system/readiness", headers)
    application = _summary(payload, "status", "passed", "total", "completion_rate")
    application["failed_checks"] = _failed_check_ids(payload)
    checks.append(_check("application_readiness", ok and application.get("status") != "not_ready", status, application))

    model_path = "/api/v1/models/health?role=coding&status=active&verify_connectivity=true&limit=100"
    ok, status, payload = _request(args.base_url, model_path, headers)
    ready_models = [item for item in payload.get("items", []) if item.get("healthy") and item.get("provider") != "mock"] if isinstance(payload, dict) else []
    checks.append(_check("real_model_connectivity", ok and bool(ready_models), status, {"ready_models": [item.get("model_name") for item in ready_models]}))

    readiness_path = "/api/v1/system/production-readiness?verify_dependencies=true"
    if args.verify_runtime:
        readiness_path += "&verify_runtime=true"
    ok, status, payload = _request(args.base_url, readiness_path, headers)
    production_ready = ok and payload.get("status") == "ready" if isinstance(payload, dict) else False
    production = _summary(payload, "status", "passed", "total", "completion_rate")
    production["failed_checks"] = _failed_check_ids(payload)
    checks.append(_check("production_readiness", production_ready, status, production))

    sandbox_path = "/api/v1/sandbox/check"
    if args.verify_runtime:
        sandbox_path += "?verify_execution=true"
    ok, status, payload = _request(args.base_url, sandbox_path, headers)
    sandbox_ready = bool(payload.get("execution_ready")) if args.verify_runtime and isinstance(payload, dict) else bool(payload.get("ready")) if isinstance(payload, dict) else False
    sandbox = _summary(payload, "backend", "ready", "execution_ready", "reason", "message", "network")
    checks.append(_check("sandbox", ok and sandbox_ready, status, sandbox))

    ok, status, payload = _request(args.base_url, "/api/v1/integrations/github/automation-readiness", headers)
    github_ready = ok and payload.get("status") == "ready" if isinstance(payload, dict) else False
    github = _summary(payload, "status", "passed", "total")
    github["failed_checks"] = _failed_check_ids(payload)
    checks.append(_check("github_automation", github_ready, status, github))

    if args.repository_id:
        repository_id = urllib.parse.quote(args.repository_id, safe="")
        ok, status, payload = _request(args.base_url, f"/api/v1/integrations/repositories/{repository_id}/health?verify_access=true", headers)
        publish_ready = bool(payload.get("write_access") and payload.get("pull_request_access")) if isinstance(payload, dict) else False
        repository = _summary(
            payload,
            "provider",
            "status",
            "remote_reachable",
            "auth_configured",
            "read_access",
            "write_access",
            "pull_request_access",
            "access_checked",
            "capability_message",
        )
        checks.append(_check("repository_publish_access", ok and publish_ready, status, repository))

    required = {"api_health", "application_readiness"}
    if not args.allow_offline:
        required.add("real_model_connectivity")
    if args.require_production:
        required.update({"production_readiness", "sandbox", "github_automation"})
    if args.require_github_publish:
        required.add("repository_publish_access")
    failed_required = [item["name"] for item in checks if item["name"] in required and not item["ok"]]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "required_checks": sorted(required),
        "passed": not failed_required,
        "failed_required_checks": failed_required,
        "checks": checks,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if not args.quiet:
        print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
