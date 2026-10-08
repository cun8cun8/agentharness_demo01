"""Validate repository files required for a safe open-source release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = (
    "LICENSE",
    "NOTICE",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    "SUPPORT.md",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/codeql.yml",
    ".github/workflows/dependency-audit.yml",
    ".github/workflows/dependency-review.yml",
    "backend/requirements.txt",
    "frontend/package-lock.json",
    "backend/scripts/release_manifest.py",
    "backend/scripts/release_preflight.py",
)


def collect_findings() -> dict[str, object]:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).is_file()]
    license_path = ROOT / "LICENSE"
    license_text = license_path.read_text(encoding="utf-8") if license_path.is_file() else ""
    notice_text = (ROOT / "NOTICE").read_text(encoding="utf-8") if (ROOT / "NOTICE").is_file() else ""
    lock_path = ROOT / "frontend/package-lock.json"
    lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else ""
    checks = {
        "license_present": "Apache License" in license_text and "Version 2.0" in license_text,
        "notice_names_project": "ResearchForge" in notice_text,
        "dependency_lock_present": lock_path.is_file(),
        "npm_lock_uses_public_registry": "registry.npmjs.org/" in lock_text and "registry.npmmirror.com/" not in lock_text,
    }
    failures = list(missing)
    if not checks["license_present"]:
        failures.append("LICENSE must contain the canonical Apache License, Version 2.0 marker")
    if not checks["notice_names_project"]:
        failures.append("NOTICE does not identify ResearchForge")
    if not checks["npm_lock_uses_public_registry"]:
        failures.append("frontend/package-lock.json must use the public npm registry")
    return {"ok": not failures, "missing": missing, "checks": checks, "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = collect_findings()
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OSS preflight: " + ("PASS" if result["ok"] else "FAIL"))
        for failure in result["failures"]:
            print(f"- {failure}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
