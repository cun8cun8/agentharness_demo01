"""Render the Helm chart and assert the deployment contracts used in CI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "infra" / "helm" / "researchforge"


def render() -> list[dict[str, object]]:
    helm = shutil.which("helm")
    if helm is None:
        raise RuntimeError("helm is required to validate the ResearchForge chart")
    result = subprocess.run(
        [helm, "template", "researchforge", str(CHART), "--namespace", "researchforge"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def index_resources(resources: list[dict[str, object]]) -> dict[tuple[str, str], dict[str, object]]:
    indexed: dict[tuple[str, str], dict[str, object]] = {}
    for resource in resources:
        metadata = resource.get("metadata", {})
        if isinstance(metadata, dict):
            indexed[(str(resource.get("kind", "")), str(metadata.get("name", "")))] = resource
    return indexed


def assert_contract(resources: list[dict[str, object]]) -> None:
    index = index_resources(resources)
    for key in (
        ("Deployment", "researchforge-api"),
        ("Deployment", "researchforge-worker"),
        ("Deployment", "researchforge-frontend"),
        ("PersistentVolumeClaim", "researchforge-sandbox-workspace"),
        ("ExternalSecret", "researchforge-application-secrets"),
        ("Cluster", "researchforge-db"),
        ("ScaledObject", "researchforge-worker"),
        ("Ingress", "researchforge"),
    ):
        assert key in index, f"missing {key[0]}/{key[1]}"
    assert not any(kind == "Secret" for kind, _name in index), "the chart must not render static Secret values"
    config = index[("ConfigMap", "researchforge-config")].get("data", {})
    assert isinstance(config, dict)
    assert config.get("RESEARCHFORGE_ARTIFACT_STORE_BACKEND") == "s3"
    assert config.get("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC") == "researchforge-sandbox-workspace"
    for workload in ("researchforge-api", "researchforge-worker"):
        spec = index[("Deployment", workload)]["spec"]["template"]["spec"]
        claims = [item.get("persistentVolumeClaim", {}).get("claimName") for item in spec.get("volumes", []) if item.get("persistentVolumeClaim")]
        assert claims == ["researchforge-sandbox-workspace"], f"{workload} has an unexpected workspace claim"
    for workload in ("researchforge-api", "researchforge-worker", "researchforge-frontend"):
        spec = index[("Deployment", workload)]["spec"]["template"]["spec"]
        security = spec["containers"][0]["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]
        assert "tmp" in {item["name"] for item in spec.get("volumes", [])}
    api_container = index[("Deployment", "researchforge-api")]["spec"]["template"]["spec"]["containers"][0]
    assert api_container["startupProbe"]["httpGet"]["path"] == "/health/live"
    assert api_container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert api_container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    paths = index[("Ingress", "researchforge")]["spec"]["rules"][0]["http"]["paths"]
    assert {item["path"] for item in paths} == {"/", "/api", "/health"}


def main() -> int:
    try:
        assert_contract(render())
    except (AssertionError, KeyError, RuntimeError) as exc:
        print(f"Helm chart validation failed: {exc}", file=sys.stderr)
        return 1
    print("ResearchForge Helm chart is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
