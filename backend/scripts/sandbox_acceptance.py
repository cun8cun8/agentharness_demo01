"""Fail-fast acceptance check for a real Docker/Kubernetes sandbox."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--require-backend", choices=("docker", "kubernetes"), default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    headers = {"Accept": "application/json", **({"X-API-Key": args.api_key} if args.api_key else {})}

    def get(path: str) -> dict[str, object]:
        request = urllib.request.Request(args.base_url.rstrip("/") + path, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        result = get("/api/v1/sandbox/check?verify_execution=true")
        status = get("/api/v1/sandbox/status")
        policy = get("/api/v1/sandbox/docker-command") if result.get("backend") == "docker" else get("/api/v1/sandbox/kubernetes-manifest")
    except (OSError, urllib.error.HTTPError) as exc:
        print(f"sandbox acceptance request failed: {exc}", file=sys.stderr)
        return 2
    backend = str(result.get("backend") or "")
    limits = result.get("limits") or status.get("limits") or {}
    passed = bool(result.get("execution_ready")) and backend in {"docker", "kubernetes"}
    checks = {
        "execution": bool(result.get("execution_ready")),
        "backend": backend in {"docker", "kubernetes"},
        "requested_backend": not args.require_backend or backend == args.require_backend,
        "limits": bool(limits),
        "policy_preview": bool(policy),
    }
    passed = passed and all(checks.values())
    report = {
        "schema_version": "sandbox-acceptance.v1",
        "passed": passed,
        "backend": backend,
        "reason": result.get("reason"),
        "checks": checks,
        "probe": result,
        "status": status,
        "policy": policy,
    }
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
