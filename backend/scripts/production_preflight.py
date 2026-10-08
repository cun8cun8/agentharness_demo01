"""Fail-fast validation for a concrete ResearchForge production deployment."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER_RE = re.compile(r"REPLACE_WITH|example\.invalid|203\.0\.113\.|YOUR_[A-Z0-9_]+", re.I)
MUTABLE_IMAGE_TAG_RE = re.compile(r":(?:latest|stable|main|master|dev)(?:$|[\"'\s])", re.I)
REQUIRED_PRODUCTION_CONFIG = {
    "RESEARCHFORGE_ENV": "production",
    "RESEARCHFORGE_AUTH_MODE": "production",
    "RESEARCHFORGE_ALLOW_MOCK_MODELS": "0",
    "RESEARCHFORGE_STORE_BACKEND": "postgres",
    "RESEARCHFORGE_JOB_QUEUE_BACKEND": "redis",
    "RESEARCHFORGE_SANDBOX_BACKEND": "kubernetes",
    "RESEARCHFORGE_NETWORK_ENABLED": "1",
    "RESEARCHFORGE_SANDBOX_NETWORK_ENABLED": "0",
    "RESEARCHFORGE_ARTIFACT_STORE_BACKEND": "s3",
    "RESEARCHFORGE_ARTIFACT_STORE_AUTO_CREATE_BUCKET": "0",
}


def render_kustomize(path: Path) -> list[dict[str, Any]]:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl is required to render the production overlay")
    result = subprocess.run([kubectl, "kustomize", str(path)], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return [item for item in yaml.safe_load_all(result.stdout) if item]


def _index(resources: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(item.get("kind", "")), str((item.get("metadata") or {}).get("name", ""))): item
        for item in resources
        if isinstance(item.get("metadata"), dict)
    }


def _config_map(index: dict[tuple[str, str], dict[str, Any]]) -> dict[str, str]:
    resource = index.get(("ConfigMap", "researchforge-config"))
    if not resource:
        raise ValueError("production overlay must render ConfigMap/researchforge-config")
    data = resource.get("data")
    if not isinstance(data, dict):
        raise ValueError("researchforge-config.data must be an object")
    return {str(key): str(value) for key, value in data.items()}


def _https_url(value: object, *, required_path: str | None = None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    if required_path is not None and parsed.path.rstrip("/") != required_path.rstrip("/"):
        return False
    return True


def _https_origins(value: object) -> bool:
    if not isinstance(value, str):
        return False
    origins = [item.strip() for item in value.split(",") if item.strip()]
    return bool(origins) and all(_https_url(item) and urlsplit(item).path in {"", "/"} for item in origins)


def _image_values(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "image" and isinstance(item, str):
                found.append(item)
            found.extend(_image_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_image_values(item))
    return found


def _placeholder_values(value: object, path: str = "") -> list[str]:
    """Return bounded, path-aware placeholder findings for operator output."""
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            findings.extend(_placeholder_values(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_placeholder_values(item, f"{path}[{index}]"))
    elif isinstance(value, str) and PLACEHOLDER_RE.search(value):
        findings.append(f"{path}={value}")
    return findings


def validate_rendered_production(resources: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    serialized = yaml.safe_dump_all(resources, allow_unicode=True, sort_keys=True)
    if PLACEHOLDER_RE.search(serialized):
        details: list[str] = []
        for resource in resources:
            kind = str(resource.get("kind", "resource"))
            name = str((resource.get("metadata") or {}).get("name", "unnamed"))
            details.extend(f"{kind}/{name}.{item}" for item in _placeholder_values(resource))
        suffix = "; ".join(details[:20])
        if len(details) > 20:
            suffix += f"; ... and {len(details) - 20} more"
        findings.append(
            "rendered Kubernetes resources still contain deployment placeholders"
            + (f": {suffix}" if suffix else "")
        )
    mutable_images = [image for resource in resources for image in _image_values(resource) if MUTABLE_IMAGE_TAG_RE.search(image)]
    if mutable_images:
        findings.append("rendered Kubernetes resources must use immutable image tags or digests")
    index = _index(resources)
    config = _config_map(index)
    for key, expected in REQUIRED_PRODUCTION_CONFIG.items():
        if config.get(key) != expected:
            findings.append(f"{key} must be {expected!r}, got {config.get(key)!r}")
    for key in (
        "RESEARCHFORGE_CORS_ORIGINS",
        "RESEARCHFORGE_ARTIFACT_STORE_BUCKET",
        "RESEARCHFORGE_MODEL_BASE_URL",
        "RESEARCHFORGE_MODEL_NAME",
        "RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL",
        "RESEARCHFORGE_GITHUB_REDIRECT_URI",
    ):
        if not config.get(key):
            findings.append(f"{key} must be configured")
    if config.get("RESEARCHFORGE_CORS_ORIGINS") and not _https_origins(config["RESEARCHFORGE_CORS_ORIGINS"]):
        findings.append("RESEARCHFORGE_CORS_ORIGINS must contain only HTTPS origins")
    webhook_url = config.get("RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL")
    if webhook_url and not _https_url(webhook_url, required_path="/api/v1/integrations/github/webhook"):
        findings.append("RESEARCHFORGE_GITHUB_WEBHOOK_PUBLIC_URL must be an HTTPS webhook URL")
    redirect_uri = config.get("RESEARCHFORGE_GITHUB_REDIRECT_URI")
    if redirect_uri and not _https_url(redirect_uri):
        findings.append("RESEARCHFORGE_GITHUB_REDIRECT_URI must be an HTTPS callback URL")
    for key in ("RESEARCHFORGE_MODEL_BASE_URL", "RESEARCHFORGE_ARTIFACT_STORE_ENDPOINT_URL"):
        if config.get(key) and not _https_url(config[key]):
            findings.append(f"{key} must be an HTTPS endpoint")
    encryption = config.get("RESEARCHFORGE_ARTIFACT_STORE_SERVER_SIDE_ENCRYPTION", "")
    if config.get("RESEARCHFORGE_ARTIFACT_STORE_BACKEND") == "s3" and str(encryption).lower() not in {"aes256", "aws:kms"}:
        findings.append("S3 artifact storage must enable AES256 or aws:kms server-side encryption")
    if str(encryption).lower() == "aws:kms" and not config.get("RESEARCHFORGE_ARTIFACT_STORE_KMS_KEY_ID"):
        findings.append("RESEARCHFORGE_ARTIFACT_STORE_KMS_KEY_ID is required for aws:kms encryption")
    if ("ExternalSecret", "researchforge-application-secrets") not in index:
        findings.append("production must use ExternalSecret/researchforge-application-secrets")
    if any(kind == "Secret" for kind, _name in index):
        findings.append("production overlay must not render static Secret resources")
    for workload in ("researchforge-api", "researchforge-worker", "researchforge-frontend"):
        deployment = index.get(("Deployment", workload))
        if not deployment:
            findings.append(f"missing Deployment/{workload}")
            continue
        pod_spec = (deployment.get("spec") or {}).get("template", {}).get("spec", {})
        containers = pod_spec.get("containers") or []
        if not containers:
            findings.append(f"Deployment/{workload} has no containers")
            continue
        security = containers[0].get("securityContext") or {}
        if security.get("readOnlyRootFilesystem") is not True:
            findings.append(f"Deployment/{workload} must use a read-only root filesystem")
        if security.get("allowPrivilegeEscalation") is not False:
            findings.append(f"Deployment/{workload} must disable privilege escalation")
        if (security.get("capabilities") or {}).get("drop") != ["ALL"]:
            findings.append(f"Deployment/{workload} must drop all Linux capabilities")
        if "tmp" not in {str(item.get("name")) for item in pod_spec.get("volumes", [])}:
            findings.append(f"Deployment/{workload} must provide a bounded tmp volume")
        if int((deployment.get("spec") or {}).get("replicas", 0) or 0) < 2:
            findings.append(f"Deployment/{workload} must start at least two replicas")
        container = containers[0]
        resources = container.get("resources") or {}
        if not resources.get("requests") or not resources.get("limits"):
            findings.append(f"Deployment/{workload} must define resource requests and limits")
        strategy = deployment.get("spec", {}).get("strategy") or {}
        rolling = strategy.get("rollingUpdate") or {}
        if strategy.get("type", "RollingUpdate") != "RollingUpdate" or rolling.get("maxUnavailable") not in {0, "0"}:
            findings.append(f"Deployment/{workload} must use RollingUpdate with maxUnavailable=0")
        if workload in {"researchforge-api", "researchforge-frontend"}:
            for probe_name in ("startupProbe", "readinessProbe", "livenessProbe"):
                if not isinstance(container.get(probe_name), dict):
                    findings.append(f"Deployment/{workload} must define {probe_name}")
    for workload in ("researchforge-api", "researchforge-worker", "researchforge-frontend"):
        pdb = index.get(("PodDisruptionBudget", workload))
        selector = (pdb or {}).get("spec", {}).get("selector", {}).get("matchLabels") if pdb else None
        if not pdb or not isinstance(selector, dict) or not selector:
            findings.append(f"PodDisruptionBudget/{workload} must protect the production workload")
    for kind, name in (
        ("Cluster", "researchforge-db"),
        ("ScheduledBackup", "researchforge-daily"),
        ("CronJob", "researchforge-restore-drill"),
    ):
        if (kind, name) not in index:
            findings.append(f"production must render {kind}/{name}")
    for name in ("researchforge-application-secrets", "researchforge-backup-s3", "researchforge-restore-secrets"):
        if ("ExternalSecret", name) not in index:
            findings.append(f"production must render ExternalSecret/{name}")
    ingress = index.get(("Ingress", "researchforge"))
    if not ingress:
        findings.append("missing Ingress/researchforge")
    else:
        ingress_spec = ingress.get("spec") or {}
        rules = ingress_spec.get("rules") or []
        host = (rules[0].get("host") or "").strip() if rules else ""
        if not host:
            findings.append("production ingress host must be configured")
        if not str(ingress_spec.get("ingressClassName") or "").strip():
            findings.append("production ingressClassName must be configured")
        tls = ingress_spec.get("tls") or []
        if not tls or not (tls[0].get("secretName") or "").strip() or host not in (tls[0].get("hosts") or []):
            findings.append("production ingress must bind the configured host to a TLS secret")
        annotations = (ingress.get("metadata") or {}).get("annotations") or {}
        if not str(annotations.get("cert-manager.io/cluster-issuer") or "").strip():
            findings.append("production ingress must configure a cert-manager cluster issuer")
    sandbox_policy = index.get(("NetworkPolicy", "researchforge-sandbox-default-deny"))
    policy_types = set((sandbox_policy or {}).get("spec", {}).get("policyTypes") or []) if sandbox_policy else set()
    if not sandbox_policy or not {"Ingress", "Egress"}.issubset(policy_types):
        findings.append("production must render an ingress and egress default-deny policy for sandbox pods")
    return findings


def validate_concrete_file(path: Path, label: str) -> list[str]:
    if not path.is_file():
        return [f"{label} does not exist: {path}"]
    if PLACEHOLDER_RE.search(path.read_text(encoding="utf-8")):
        return [f"{label} contains deployment placeholders: {path}"]
    return []


def validate_terraform_values(path: Path, label: str) -> list[str]:
    findings = validate_concrete_file(path, label)
    if findings:
        return findings
    content = path.read_text(encoding="utf-8")
    if re.search(r"cluster_public_access_cidrs\s*=\s*\[\s*\]", content):
        return [f"{label} must restrict cluster_public_access_cidrs when public access is enabled"]
    if re.search(r"cluster_endpoint_public_access\s*=\s*true", content) and "cluster_public_access_cidrs" not in content:
        return [f"{label} enables public EKS access without an allowlist"]
    return []


def validate_helm_values(values: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    config = values.get("config") or {}
    runtime = values.get("runtime") or {}
    ingress = values.get("ingress") or {}
    database = values.get("database") or {}
    restore_drill = values.get("restoreDrill") or {}
    for key in (
        "modelBaseUrl",
        "modelName",
        "researchModelName",
        "artifactBucket",
        "artifactEndpointUrl",
        "githubWebhookPublicUrl",
        "githubRedirectUri",
    ):
        if not config.get(key):
            findings.append(f"config.{key} must be configured")
    if config.get("corsOrigins") and not _https_origins(config["corsOrigins"]):
        findings.append("config.corsOrigins must contain only HTTPS origins")
    if config.get("githubWebhookPublicUrl") and not _https_url(
        config["githubWebhookPublicUrl"], required_path="/api/v1/integrations/github/webhook"
    ):
        findings.append("config.githubWebhookPublicUrl must be an HTTPS webhook URL")
    if config.get("githubRedirectUri") and not _https_url(config["githubRedirectUri"]):
        findings.append("config.githubRedirectUri must be an HTTPS callback URL")
    for key in ("modelBaseUrl", "artifactEndpointUrl"):
        if config.get(key) and not _https_url(config[key]):
            findings.append(f"config.{key} must be an HTTPS endpoint")
    encryption = str(config.get("artifactServerSideEncryption") or "").lower()
    if encryption not in {"aes256", "aws:kms"}:
        findings.append("config.artifactServerSideEncryption must be AES256 or aws:kms")
    if encryption == "aws:kms" and not config.get("artifactKmsKeyId"):
        findings.append("config.artifactKmsKeyId is required for aws:kms encryption")
    if "REPLACE_WITH" in str(runtime.get("sandboxImage", "")):
        findings.append("runtime.sandboxImage contains a placeholder")
    image_values = [
        runtime.get("sandboxImage"),
        runtime.get("trainerImage"),
        (values.get("api") or {}).get("image", {}).get("repository", "") + ":" + str((values.get("api") or {}).get("image", {}).get("tag", "")),
        (values.get("worker") or {}).get("image", {}).get("repository", "") + ":" + str((values.get("worker") or {}).get("image", {}).get("tag", "")),
        (values.get("frontend") or {}).get("image", {}).get("repository", "") + ":" + str((values.get("frontend") or {}).get("image", {}).get("tag", "")),
    ]
    if any(isinstance(image, str) and MUTABLE_IMAGE_TAG_RE.search(image) for image in image_values):
        findings.append("production images must use immutable tags or digests")
    if not database.get("backupBucket"):
        findings.append("database.backupBucket must be configured")
    cnpg = database.get("cnpg") or {}
    if cnpg.get("enabled") is not True or int(cnpg.get("instances", 0) or 0) < 3:
        findings.append("database.cnpg must be enabled with at least three instances")
    if restore_drill.get("enabled") is not True or not str(restore_drill.get("schedule") or "").strip():
        findings.append("restoreDrill must be enabled with a schedule")
    if not ingress.get("host") or not ingress.get("clusterIssuer") or not ingress.get("tlsSecretName"):
        findings.append("ingress.host, ingress.clusterIssuer and ingress.tlsSecretName must be configured")
    return findings


def run(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, findings: list[str]) -> None:
        checks.append({"name": name, "passed": not findings, "findings": findings})

    try:
        add("kustomize", validate_rendered_production(render_kustomize(Path(args.kustomize).resolve())))
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        add("kustomize", [str(exc)])
    for argument, label in ((args.helm_values, "Helm production values"), (args.terraform_values, "Terraform AWS values"), (args.state_values, "Terraform state values")):
        if argument:
            add(label, validate_concrete_file(Path(argument).resolve(), label))
    if args.helm_values:
        path = Path(args.helm_values).resolve()
        if path.is_file():
            try:
                values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                add("helm_values_schema", validate_helm_values(values) if isinstance(values, dict) else ["Helm values must be a YAML object"])
            except yaml.YAMLError as exc:
                add("helm_values_schema", [f"invalid YAML: {exc}"])
    if args.terraform_values:
        add("terraform_values_security", validate_terraform_values(Path(args.terraform_values).resolve(), "Terraform AWS values"))
    return {"status": "ready" if all(item["passed"] for item in checks) else "not_ready", "checks": checks, "passed": sum(1 for item in checks if item["passed"]), "total": len(checks)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a concrete ResearchForge production deployment")
    parser.add_argument("--kustomize", default=str(ROOT / "infra" / "kubernetes" / "production"))
    parser.add_argument("--helm-values")
    parser.add_argument("--terraform-values")
    parser.add_argument("--state-values")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    result = run(args)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for check in result["checks"]:
            print(f"[{'PASS' if check['passed'] else 'FAIL'}] {check['name']}")
            for finding in check["findings"]:
                print(f"  - {finding}")
        print(f"Production preflight: {result['passed']}/{result['total']} checks passed")
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
