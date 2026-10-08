"""Run the repeatable source and release checks as one machine-readable gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .oss_preflight import collect_findings
    from .release_manifest import build_manifest
except ImportError:  # pragma: no cover - supports direct script execution
    from oss_preflight import collect_findings
    from release_manifest import build_manifest


ROOT = Path(__file__).resolve().parents[2]
SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*[^\s,;]+")
BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")
URL_CREDENTIAL_RE = re.compile(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@")


def _tail(output: str, limit: int = 20) -> list[str]:
    lines = output.splitlines()
    return [_redact(line) for line in lines[-limit:]]


def _redact(value: str) -> str:
    value = URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", value)
    value = BEARER_RE.sub("Bearer [REDACTED]", value)
    return SECRET_RE.sub(r"\1=[REDACTED]", value)


def _run(label: str, command: list[str], *, cwd: Path = ROOT, timeout: int = 900) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        output = "\n".join(_tail((result.stdout or "") + (result.stderr or "")))
        return {
            "name": label,
            "passed": result.returncode == 0,
            "returncode": result.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": output,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "name": label,
            "passed": False,
            "returncode": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": [type(exc).__name__, str(exc)],
        }


def run_gate(*, skip_tests: bool, skip_frontend: bool, require_production: bool, output: Path | None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    oss = collect_findings()
    checks.append({"name": "oss_preflight", "passed": bool(oss["ok"]), "failures": oss["failures"]})
    manifest = build_manifest(version=os.getenv("RESEARCHFORGE_VERSION"))
    checks.append({"name": "release_manifest", "passed": bool(manifest["release_gate"]["passed"]), "errors": manifest["release_gate"]["errors"]})
    checks.append(_run("python_compile", [sys.executable, "-m", "compileall", "-q", "backend/app", "backend/scripts"]))
    if not skip_tests:
        checks.append(_run("backend_tests", [sys.executable, "-m", "pytest", "-q"]))
    if not skip_frontend:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        checks.append(_run("frontend_check", [npm, "run", "check"], cwd=ROOT / "frontend"))
        checks.append(_run("frontend_build", [npm, "run", "build"], cwd=ROOT / "frontend"))
    checks.append(_run("benchmark_suite", [sys.executable, "backend/scripts/validate_benchmark_suite.py"]))
    checks.append(_run("kubernetes_overlays", [sys.executable, "backend/scripts/validate_kubernetes_overlays.py"]))
    if require_production:
        checks.append(_run("production_preflight", [sys.executable, "backend/scripts/production_preflight.py", "--json"]))
    failed = [item["name"] for item in checks if not item.get("passed")]
    report = {
        "schema_version": "researchforge-release-preflight.v1",
        "passed": not failed,
        "failed_checks": failed,
        "checks": checks,
        "release_manifest": manifest,
    }
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--require-production", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".run/acceptance/release-preflight.json"))
    args = parser.parse_args()
    report = run_gate(
        skip_tests=args.skip_tests,
        skip_frontend=args.skip_frontend,
        require_production=args.require_production,
        output=args.output,
    )
    print(json.dumps({"passed": report["passed"], "failed_checks": report["failed_checks"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
