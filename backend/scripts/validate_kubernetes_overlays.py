"""Render and assert the Kubernetes deployment contracts used by CI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
KUBERNETES = ROOT / "infra" / "kubernetes"


def render(overlay: str) -> list[dict[str, object]]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl is required to validate Kubernetes overlays")
    result = subprocess.run(
        [kubectl, "kustomize", str(KUBERNETES / overlay)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def resource_index(resources: list[dict[str, object]]) -> dict[tuple[str, str], dict[str, object]]:
    index: dict[tuple[str, str], dict[str, object]] = {}
    for resource in resources:
        metadata = resource.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        kind = str(resource.get("kind", ""))
        name = str(metadata.get("name", ""))
        index[(kind, name)] = resource
    return index


def config_data(index: dict[tuple[str, str], dict[str, object]]) -> dict[str, str]:
    config = index[("ConfigMap", "researchforge-config")]
    data = config.get("data", {})
    if not isinstance(data, dict):
        raise AssertionError("researchforge-config has no data")
    return {str(key): str(value) for key, value in data.items()}


def assert_development(resources: list[dict[str, object]]) -> None:
    index = resource_index(resources)
    assert ("Secret", "researchforge-secrets") in index
    assert ("StatefulSet", "researchforge-postgres") in index
    assert ("StatefulSet", "researchforge-redis") in index
    assert config_data(index)["RESEARCHFORGE_ENV"] == "development"


def assert_production(resources: list[dict[str, object]]) -> None:
    index = resource_index(resources)
    assert ("ExternalSecret", "researchforge-application-secrets") in index
    assert ("Cluster", "researchforge-db") in index
    assert ("StatefulSet", "researchforge-postgres") not in index
    assert ("StatefulSet", "researchforge-redis") not in index
    assert ("PersistentVolumeClaim", "researchforge-artifacts") not in index
    config = config_data(index)
    assert config["RESEARCHFORGE_ARTIFACT_STORE_BACKEND"] == "s3"
    assert config["RESEARCHFORGE_ARTIFACT_STORE_AUTO_CREATE_BUCKET"] == "0"
    assert config["RESEARCHFORGE_ALLOW_MOCK_MODELS"] == "0"
    assert config["RESEARCHFORGE_SANDBOX_NETWORK_ENABLED"] == "0"
    namespace = index[("Namespace", "researchforge")]
    labels = namespace["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    sandbox_policy = index[("NetworkPolicy", "researchforge-sandbox-default-deny")]
    assert sandbox_policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert sandbox_policy["spec"].get("ingress") is None
    assert sandbox_policy["spec"].get("egress") is None
    ingress = index[("Ingress", "researchforge")]["spec"]
    paths = ingress["rules"][0]["http"]["paths"]
    assert {path["path"] for path in paths} == {"/", "/api", "/health"}
    for deployment in ("researchforge-api", "researchforge-worker"):
        spec = index[("Deployment", deployment)]["spec"]
        pod_spec = spec["template"]["spec"]
        volumes = pod_spec.get("volumes", [])
        assert all(volume.get("name") != "artifacts" for volume in volumes)
    api_container = index[("Deployment", "researchforge-api")]["spec"]["template"]["spec"]["containers"][0]
    assert api_container["startupProbe"]["httpGet"]["path"] == "/health/live"
    assert api_container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert api_container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    _assert_workload_hardening(index)


def _assert_workload_hardening(index: dict[tuple[str, str], dict[str, object]]) -> None:
    for deployment in ("researchforge-api", "researchforge-worker", "researchforge-frontend"):
        pod_spec = index[("Deployment", deployment)]["spec"]["template"]["spec"]
        assert pod_spec["securityContext"]["runAsNonRoot"] is True
        container = pod_spec["containers"][0]
        security = container["securityContext"]
        assert security["allowPrivilegeEscalation"] is False
        assert security["readOnlyRootFilesystem"] is True
        assert security["capabilities"]["drop"] == ["ALL"]
        assert security["seccompProfile"]["type"] == "RuntimeDefault"
        if deployment == "researchforge-api":
            assert container["startupProbe"]["failureThreshold"] >= 30
            assert container["readinessProbe"]["periodSeconds"] <= 10
        mounts = {mount["name"] for mount in container.get("volumeMounts", [])}
        volumes = {volume["name"] for volume in pod_spec.get("volumes", [])}
        assert "tmp" in mounts and "tmp" in volumes


def assert_disaster_recovery(resources: list[dict[str, object]]) -> None:
    index = resource_index(resources)
    assert ("Namespace", "researchforge") in index
    assert ("ExternalSecret", "researchforge-dr-backup-s3") in index
    cluster = index[("Cluster", "researchforge-db-dr")]
    assert cluster["spec"]["replica"]["enabled"] is True
    assert cluster["spec"]["bootstrap"]["recovery"]["source"] == "researchforge-primary"
    assert not any(kind == "Deployment" for kind, _name in index)


def assert_gpu_training(resources: list[dict[str, object]]) -> None:
    assert_production(resources)
    config = config_data(resource_index(resources))
    assert config["RESEARCHFORGE_TRAINING_KUBERNETES_NODE_SELECTOR"] == '{"researchforge.io/workload":"gpu-training"}'
    assert '"nvidia.com/gpu"' in config["RESEARCHFORGE_TRAINING_KUBERNETES_TOLERATIONS"]


def assert_model_serving(resources: list[dict[str, object]]) -> None:
    index = resource_index(resources)
    namespace = index[("Namespace", "researchforge-models")]
    assert namespace["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"
    assert ("ExternalSecret", "researchforge-vllm-secrets") in index
    deployment = index[("Deployment", "researchforge-vllm")]
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["serviceAccountName"] == "researchforge-model-serving"
    assert pod_spec["nodeSelector"] == {"researchforge.io/workload": "gpu-training"}
    container = pod_spec["containers"][0]
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "1"
    security = container["securityContext"]
    assert security["allowPrivilegeEscalation"] is False
    assert security["readOnlyRootFilesystem"] is True
    init_container = pod_spec["initContainers"][0]
    assert init_container["name"] == "fetch-model-weights"
    assert init_container["securityContext"]["readOnlyRootFilesystem"] is True
    model_config = index[("ConfigMap", "researchforge-vllm-config")]["data"]
    assert "MODEL_S3_URI" in model_config
    policy = index[("NetworkPolicy", "researchforge-vllm")]["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    assert ("Service", "researchforge-vllm") in index


def main() -> int:
    development = render("development")
    production = render("production")
    disaster_recovery = render("disaster-recovery")
    gpu_training = render("gpu-training")
    model_serving = render("model-serving")
    assert_development(development)
    assert_production(production)
    assert_disaster_recovery(disaster_recovery)
    assert_gpu_training(gpu_training)
    assert_model_serving(model_serving)
    print("Kubernetes development, production, GPU training, model-serving, and disaster-recovery overlays are valid.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, RuntimeError) as exc:
        print(f"Kubernetes overlay validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
