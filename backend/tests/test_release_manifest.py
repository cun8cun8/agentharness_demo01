# Copyright 2026 ResearchForge contributors
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from backend.scripts.release_manifest import build_manifest, project_version, validate_image_ref


def test_release_manifest_is_apache_and_hashes_inputs():
    manifest = build_manifest(source_date_epoch=1_700_000_000)
    assert manifest["schema_version"] == "researchforge-release-manifest.v1"
    assert manifest["license"]["spdx_id"] == "Apache-2.0"
    assert manifest["license"]["sha256"]
    assert manifest["files"]["frontend/package-lock.json"]["sha256"]
    assert manifest["release_gate"]["passed"]
    assert manifest["version"] == project_version()


@pytest.mark.parametrize(
    "ref",
    [
        "ghcr.io/example/researchforge-api:latest",
        "https://example.invalid/image:abc1234",
        "ghcr.io/example/image@sha256:abc",
        "REPLACE_WITH_IMAGE",
    ],
)
def test_release_manifest_rejects_mutable_or_placeholder_images(ref):
    assert validate_image_ref("api", ref)


def test_release_manifest_requires_all_release_images():
    manifest = build_manifest(image_refs={"api": "ghcr.io/example/api:abc1234"}, require_images=True)
    assert not manifest["release_gate"]["passed"]
    assert any("must include" in error for error in manifest["release_gate"]["errors"])


def test_failed_benchmark_blocks_release(tmp_path: Path):
    report = tmp_path / "benchmark.json"
    report.write_text(json.dumps({"schema_version": "repository-benchmark.v1", "acceptance_gate": {"passed": False}}), encoding="utf-8")
    manifest = build_manifest(benchmark_report=report)
    assert not manifest["release_gate"]["passed"]
    assert "benchmark acceptance gate failed" in manifest["release_gate"]["errors"]
