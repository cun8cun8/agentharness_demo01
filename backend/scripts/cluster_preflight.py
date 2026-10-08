"""Read-only Kubernetes production preflight for ResearchForge releases."""

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


def _json(runner: Runner, kubectl: str, args: list[str]) -> tuple[dict[str, object] | None, str]:
    result = runner([kubectl, *args, "-o", "json"])
    if result.returncode:
        return None, (result.stderr or result.stdout or "kubectl command failed").strip()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "kubectl returned invalid JSON"
    return payload if isinstance(payload, dict) else None, ""


def _condition_is_true(payload: dict[str, object], condition_type: str) -> bool:
    status = payload.get("status")
    if not isinstance(status, dict):
        return False
    conditions = status.get("conditions")
    if not isinstance(conditions, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") == condition_type
        and str(item.get("status", "")).lower() == "true"
        for item in conditions
    )


def _exists(runner: Runner, kubectl: str, check_id: str, args: list[str]) -> Check:
    payload, detail = _json(runner, kubectl, args)
    return Check(check_id, payload is not None, "available" if payload is not None else detail)


def _deployment_ready(runner: Runner, kubectl: str, namespace: str, name: str) -> Check:
    payload, detail = _json(runner, kubectl, ["-n", namespace, "get", "deployment", name])
    if payload is None:
        return Check(f"deployment:{name}", False, detail)
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    desired = int(spec.get("replicas", 1))
    available = int(status.get("availableReplicas", 0))
    return Check(f"deployment:{name}", available >= desired, f"available={available}, desired={desired}")


def _external_secret_ready(runner: Runner, kubectl: str, namespace: str, name: str) -> Check:
    payload, detail = _json(runner, kubectl, ["-n", namespace, "get", "externalsecret", name])
    if payload is None:
        return Check(f"external_secret:{name}", False, detail)
    return Check(f"external_secret:{name}", _condition_is_true(payload, "Ready"), "Ready=True" if _condition_is_true(payload, "Ready") else "Ready condition is not True")


def _cnpg_ready(runner: Runner, kubectl: str, namespace: str) -> Check:
    payload, detail = _json(runner, kubectl, ["-n", namespace, "get", "cluster.postgresql.cnpg.io", "researchforge-db"])
    if payload is None:
        return Check("cnpg:researchforge-db", False, detail)
    return Check("cnpg:researchforge-db", _condition_is_true(payload, "Ready"), "Ready=True" if _condition_is_true(payload, "Ready") else "Ready condition is not True")


def _deployment_hardened(runner: Runner, kubectl: str, namespace: str, name: str) -> Check:
    payload, detail = _json(runner, kubectl, ["-n", namespace, "get", "deployment", name])
    if payload is None:
        return Check(f"security:{name}", False, detail)
    spec = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
    template = spec.get("template") if isinstance(spec.get("template"), dict) else {}
    pod_spec = template.get("spec") if isinstance(template.get("spec"), dict) else {}
    pod_security = pod_spec.get("securityContext") if isinstance(pod_spec.get("securityContext"), dict) else {}
    containers = pod_spec.get("containers") if isinstance(pod_spec.get("containers"), list) else []
    container = next((item for item in containers if isinstance(item, dict) and item.get("name") in {"api", "worker", "frontend"}), None)
    security = container.get("securityContext") if isinstance(container, dict) and isinstance(container.get("securityContext"), dict) else {}
    capabilities = security.get("capabilities") if isinstance(security.get("capabilities"), dict) else {}
    drops = capabilities.get("drop") if isinstance(capabilities.get("drop"), list) else []
    seccomp = security.get("seccompProfile") if isinstance(security.get("seccompProfile"), dict) else {}
    mounts = container.get("volumeMounts") if isinstance(container, dict) and isinstance(container.get("volumeMounts"), list) else []
    volumes = pod_spec.get("volumes") if isinstance(pod_spec.get("volumes"), list) else []
    hardened = (
        pod_security.get("runAsNonRoot") is True
        and security.get("allowPrivilegeEscalation") is False
        and security.get("readOnlyRootFilesystem") is True
        and "ALL" in drops
        and seccomp.get("type") == "RuntimeDefault"
        and any(isinstance(item, dict) and item.get("name") == "tmp" for item in mounts)
        and any(isinstance(item, dict) and item.get("name") == "tmp" for item in volumes)
    )
    return Check(f"security:{name}", hardened, "hardened" if hardened else "missing non-root, read-only filesystem, seccomp, capability drop, or tmpfs")


def _production_config(runner: Runner, kubectl: str, namespace: str) -> Check:
    payload, detail = _json(runner, kubectl, ["-n", namespace, "get", "configmap", "researchforge-config"])
    if payload is None:
        return Check("production_config", False, detail)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    expected = {
        "RESEARCHFORGE_ENV": "production",
        "RESEARCHFORGE_ALLOW_MOCK_MODELS": "0",
        "RESEARCHFORGE_SANDBOX_BACKEND": "kubernetes",
        "RESEARCHFORGE_SANDBOX_NETWORK_ENABLED": "0",
        "RESEARCHFORGE_STORE_BACKEND": "postgres",
    }
    missing = [key for key, value in expected.items() if data.get(key) != value]
    return Check("production_config", not missing, "valid" if not missing else "unexpected: " + ", ".join(missing))


def _sandbox_rbac(runner: Runner, kubectl: str, namespace: str) -> Check:
    payload, detail = _json(runner, kubectl, ["-n", namespace, "get", "role", "researchforge-sandbox-manager"])
    if payload is None:
        return Check("security:sandbox_rbac", False, detail)
    rules = payload.get("rules") if isinstance(payload.get("rules"), list) else []
    resources = {
        resource
        for rule in rules
        if isinstance(rule, dict)
        for resource in rule.get("resources", [])
        if isinstance(resource, str)
    }
    required = {"pods", "pods/log", "networkpolicies", "jobs", "pytorchjobs"}
    missing = sorted(required - resources)
    return Check("security:sandbox_rbac", not missing, "least-privilege runtime rules present" if not missing else "missing resources: " + ", ".join(missing))


def collect_checks(namespace: str, *, kubectl: str = "kubectl", runner: Runner = _run, production: bool = True) -> list[Check]:
    checks = [
        _exists(runner, kubectl, "namespace", ["get", "namespace", namespace]),
        _exists(runner, kubectl, "service_account", ["-n", namespace, "get", "serviceaccount", "researchforge-runtime"]),
        _exists(runner, kubectl, "workspace_pvc", ["-n", namespace, "get", "pvc", "researchforge-sandbox-workspace"]),
        _exists(runner, kubectl, "ingress", ["-n", namespace, "get", "ingress", "researchforge"]),
        *[_deployment_ready(runner, kubectl, namespace, name) for name in ("researchforge-api", "researchforge-worker", "researchforge-frontend")],
    ]
    if not production:
        return checks
    for crd in ("externalsecrets.external-secrets.io", "clusters.postgresql.cnpg.io", "scaledobjects.keda.sh"):
        checks.append(_exists(runner, kubectl, f"crd:{crd}", ["get", "crd", crd]))
    checks.extend(
        [
            _external_secret_ready(runner, kubectl, namespace, "researchforge-application-secrets"),
            _external_secret_ready(runner, kubectl, namespace, "researchforge-backup-s3"),
            _external_secret_ready(runner, kubectl, namespace, "researchforge-restore-secrets"),
            _cnpg_ready(runner, kubectl, namespace),
            _exists(runner, kubectl, "hpa", ["-n", namespace, "get", "hpa", "researchforge-api"]),
            _exists(runner, kubectl, "keda", ["-n", namespace, "get", "scaledobject", "researchforge-worker"]),
            _production_config(runner, kubectl, namespace),
            _sandbox_rbac(runner, kubectl, namespace),
            *[_deployment_hardened(runner, kubectl, namespace, name) for name in ("researchforge-api", "researchforge-worker", "researchforge-frontend")],
        ]
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only ResearchForge Kubernetes preflight")
    parser.add_argument("--namespace", default="researchforge")
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--development", action="store_true", help="check only the development overlay resources")
    args = parser.parse_args()
    kubectl = shutil.which(args.kubectl) or args.kubectl
    checks = collect_checks(args.namespace, kubectl=kubectl, production=not args.development)
    report = {"namespace": args.namespace, "profile": "development" if args.development else "production", "checks": [asdict(item) for item in checks]}
    report["status"] = "ready" if all(item.ok for item in checks) else "not_ready"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "not_ready", "error": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
