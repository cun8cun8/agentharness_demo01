# Copyright 2026 ResearchForge contributors
# SPDX-License-Identifier: Apache-2.0
"""Create a reproducible, secret-free manifest for an Apache-2.0 release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "researchforge-release-manifest.v1"
TRACKED_FILES = (
    "LICENSE",
    "NOTICE",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "backend/requirements.txt",
    "backend/scripts/oss_preflight.py",
    "backend/scripts/release_manifest.py",
    "backend/scripts/release_preflight.py",
    "frontend/package.json",
    "frontend/package-lock.json",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
)
PLACEHOLDER_RE = re.compile(r"(?:REPLACE_WITH|example\.invalid|CHANGEME|TODO)", re.IGNORECASE)
MUTABLE_TAG_RE = re.compile(r":(?:latest|stable|main|master|dev)(?:$|@)", re.IGNORECASE)
IMAGE_NAME_RE = re.compile(r"^(api|frontend|sandbox|trainer)$")
IMAGE_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$", re.IGNORECASE)
IMAGE_COMMIT_TAG_RE = re.compile(r":[0-9a-f]{7,64}$", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_version(root: Path = ROOT) -> str:
    package_path = root / "frontend/package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        version = package.get("version") if isinstance(package, dict) else None
        if isinstance(version, str) and version.strip():
            return version.strip()
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return "0.0.0"


def validate_image_ref(name: str, ref: str) -> str | None:
    if not IMAGE_NAME_RE.fullmatch(name):
        return f"unsupported image name: {name}"
    if not ref.strip():
        return f"image {name} is empty"
    if PLACEHOLDER_RE.search(ref) or MUTABLE_TAG_RE.search(ref):
        return f"image {name} uses a placeholder or mutable tag"
    if not IMAGE_DIGEST_RE.search(ref) and not IMAGE_COMMIT_TAG_RE.search(ref):
        return f"image {name} must use a commit tag or digest"
    return None


def load_benchmark(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("benchmark report must be a JSON object")
    gate = data.get("acceptance_gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("passed"), bool):
        raise ValueError("benchmark report must contain acceptance_gate.passed")
    return {
        "path": path.as_posix(),
        "sha256": sha256_file(path),
        "schema_version": data.get("schema_version"),
        "acceptance_gate_passed": gate["passed"],
        "success_rate": data.get("success_rate"),
        "task_count": data.get("task_count"),
    }


def build_manifest(
    *,
    root: Path = ROOT,
    version: str | None = None,
    source_date_epoch: int | None = None,
    image_refs: dict[str, str] | None = None,
    benchmark_report: Path | None = None,
    require_images: bool = False,
) -> dict[str, Any]:
    image_refs = image_refs or {}
    version = version or project_version(root)
    errors: list[str] = []
    files: dict[str, dict[str, Any]] = {}
    for relative in TRACKED_FILES:
        path = root / relative
        if not path.is_file():
            errors.append(f"missing release file: {relative}")
            continue
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha256_file(path)}

    license_path = root / "LICENSE"
    license_text = license_path.read_text(encoding="utf-8") if license_path.is_file() else ""
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        errors.append("LICENSE is not Apache License, Version 2.0")

    images: dict[str, str] = {}
    for name, ref in sorted(image_refs.items()):
        error = validate_image_ref(name, ref)
        if error:
            errors.append(error)
        else:
            images[name] = ref
    if require_images and set(images) != {"api", "frontend", "sandbox", "trainer"}:
        errors.append("release images must include api, frontend, sandbox and trainer")

    benchmark: dict[str, Any] | None = None
    if benchmark_report is not None:
        try:
            benchmark = load_benchmark(benchmark_report)
            if not benchmark["acceptance_gate_passed"]:
                errors.append("benchmark acceptance gate failed")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"invalid benchmark report: {exc}")

    epoch = source_date_epoch if source_date_epoch is not None else int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    generated_at = datetime.fromtimestamp(max(0, epoch), tz=UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": SCHEMA,
        "project": "ResearchForge",
        "version": version,
        "license": {"spdx_id": "Apache-2.0", "sha256": files.get("LICENSE", {}).get("sha256")},
        "source_date_epoch": max(0, epoch),
        "generated_at": generated_at,
        "files": files,
        "images": images,
        "benchmark": benchmark,
        "release_gate": {"passed": not errors, "errors": errors},
    }


def parse_images(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--image must use NAME=REF: {value}")
        name, ref = value.split("=", 1)
        if name in result:
            raise ValueError(f"duplicate image: {name}")
        result[name] = ref
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", default=os.environ.get("RESEARCHFORGE_VERSION"))
    parser.add_argument("--source-date-epoch", type=int)
    parser.add_argument("--image", action="append", default=[], metavar="NAME=REF")
    parser.add_argument("--benchmark-report", type=Path)
    parser.add_argument("--require-images", action="store_true")
    args = parser.parse_args()
    try:
        images = parse_images(args.image)
    except ValueError as exc:
        parser.error(str(exc))
    manifest = build_manifest(
        version=args.version,
        source_date_epoch=args.source_date_epoch,
        image_refs=images,
        benchmark_report=args.benchmark_report,
        require_images=args.require_images,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest["release_gate"], ensure_ascii=False))
    return 0 if manifest["release_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
