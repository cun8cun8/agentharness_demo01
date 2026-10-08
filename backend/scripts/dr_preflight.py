"""Read-only readiness checks for a ResearchForge disaster-recovery cluster."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Callable


@dataclass(frozen=True)
class Check:
    id: str
    ok: bool
    detail: str


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)


def _resource(runner: Runner, kubectl: str, args: list[str]) -> tuple[dict[str, object] | None, str]:
    result = runner([kubectl, *args, "-o", "json"])
    if result.returncode:
        return None, (result.stderr or result.stdout or "kubectl command failed").strip()
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "kubectl returned invalid JSON"
    return (value, "") if isinstance(value, dict) else (None, "kubectl returned a non-object resource")


def _exists(runner: Runner, kubectl: str, check_id: str, args: list[str]) -> Check:
    payload, detail = _resource(runner, kubectl, args)
    return Check(check_id, payload is not None, "available" if payload is not None else detail)


def _ready(runner: Runner, kubectl: str, check_id: str, args: list[str]) -> Check:
    payload, detail = _resource(runner, kubectl, args)
    if payload is None:
        return Check(check_id, False, detail)
    status = payload.get("status")
    conditions = status.get("conditions", []) if isinstance(status, dict) else []
    ready = any(
        isinstance(item, dict)
        and item.get("type") == "Ready"
        and str(item.get("status", "")).lower() == "true"
        for item in conditions
    )
    return Check(check_id, ready, "Ready=True" if ready else "Ready condition is not True")


def collect_checks(namespace: str, *, kubectl: str = "kubectl", runner: Runner = _run) -> list[Check]:
    checks = [
        _exists(runner, kubectl, "namespace", ["get", "namespace", namespace]),
        _exists(runner, kubectl, "crd:cnpg", ["get", "crd", "clusters.postgresql.cnpg.io"]),
        _exists(runner, kubectl, "crd:external_secrets", ["get", "crd", "externalsecrets.external-secrets.io"]),
        _ready(runner, kubectl, "external_secret:dr_backup", ["-n", namespace, "get", "externalsecret", "researchforge-dr-backup-s3"]),
        _ready(runner, kubectl, "cnpg:dr_replica", ["-n", namespace, "get", "cluster.postgresql.cnpg.io", "researchforge-db-dr"]),
    ]
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ResearchForge DR cluster preflight")
    parser.add_argument("--namespace", default="researchforge")
    parser.add_argument("--kubectl", default="kubectl")
    args = parser.parse_args()
    checks = collect_checks(args.namespace, kubectl=shutil.which(args.kubectl) or args.kubectl)
    report = {"namespace": args.namespace, "checks": [asdict(check) for check in checks]}
    report["status"] = "ready" if all(check.ok for check in checks) else "not_ready"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "not_ready", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
