import importlib.util
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    module_name = f"researchforge_test_script_{name}"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_load_probe_calculates_percentiles_and_failures(monkeypatch):
    probe = _load("load_probe")
    results = iter(
        [
            probe.Sample(True, 200, 10.0),
            probe.Sample(True, 200, 20.0),
            probe.Sample(False, 503, 30.0, "HTTP_503"),
            probe.Sample(True, 200, 40.0),
        ]
    )
    monkeypatch.setattr(probe, "request_once", lambda *_args, **_kwargs: next(results))

    report = probe.run_probe("https://researchforge.example/health", {}, requests=4, concurrency=2, timeout=1)

    assert report["failed"] == 1
    assert report["error_rate"] == 0.25
    assert report["errors"] == {"HTTP_503": 1}
    assert report["latency_ms"]["p95"] == 38.5


def test_cluster_preflight_accepts_ready_production_resources():
    preflight = _load("cluster_preflight")

    def runner(command):
        resource = " ".join(command)
        if " version " in resource:
            body = {"clientVersion": {"gitVersion": "v1.37.1"}, "serverVersion": {"gitVersion": "v1.36.2"}}
        elif " get deployment " in resource:
            name = "api" if "researchforge-api" in resource else "worker" if "researchforge-worker" in resource else "frontend"
            replicas = 2 if name in {"api", "frontend"} else 1
            body = {
                "spec": {"replicas": replicas, "template": {"spec": {"securityContext": {"runAsNonRoot": True}, "containers": [{"name": name, "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}}, "volumeMounts": [{"name": "tmp"}]}], "volumes": [{"name": "tmp"}]}}},
                "status": {"availableReplicas": replicas},
            }
        elif " get configmap researchforge-config " in resource:
            body = {"data": {"RESEARCHFORGE_ENV": "production", "RESEARCHFORGE_ALLOW_MOCK_MODELS": "0", "RESEARCHFORGE_SANDBOX_BACKEND": "kubernetes", "RESEARCHFORGE_SANDBOX_NETWORK_ENABLED": "0", "RESEARCHFORGE_STORE_BACKEND": "postgres"}}
        elif " get role researchforge-sandbox-manager " in resource:
            body = {"rules": [{"resources": ["pods", "pods/log", "networkpolicies", "jobs", "pytorchjobs"]}]}
        elif " get externalsecret " in resource or "cluster.postgresql.cnpg.io" in resource:
            body = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        else:
            body = {"metadata": {"name": "ready"}}
        return type("Result", (), {"returncode": 0, "stdout": __import__("json").dumps(body), "stderr": ""})()

    checks = preflight.collect_checks("researchforge", runner=runner, production=True)

    assert checks
    assert all(item.ok for item in checks)


def test_dr_and_gpu_preflight_accept_ready_resources():
    dr_preflight = _load("dr_preflight")
    gpu_preflight = _load("gpu_preflight")

    def runner(command):
        resource = " ".join(command)
        if " get nodes " in resource:
            body = {
                "items": [{"status": {"allocatable": {"nvidia.com/gpu": "4"}, "conditions": [{"type": "Ready", "status": "True"}]}}]
            }
        elif " get externalsecret " in resource or "cluster.postgresql.cnpg.io" in resource:
            body = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        else:
            body = {"metadata": {"name": "ready"}}
        return type("Result", (), {"returncode": 0, "stdout": __import__("json").dumps(body), "stderr": ""})()

    assert all(item.ok for item in dr_preflight.collect_checks("researchforge", runner=runner))
    checks = gpu_preflight.collect_checks(runner=runner, min_gpus=2, require_pytorch_operator=True)
    assert all(item.ok for item in checks)


def test_benchmark_suite_validation_rejects_incomplete_task(tmp_path):
    validator = _load("validate_benchmark_suite")
    task = tmp_path / "task_001_example"
    task.mkdir()
    (task / "task.yaml").write_text("id: duplicate\ntitle: example\ntest_command: pytest\ntimeout_seconds: 60\nmax_steps: 2\nsuccess_criteria: [all_tests_pass]\n", encoding="utf-8")
    assert validator.validate_suite(tmp_path, minimum_tasks=1)


def test_dr_promotion_patch_disables_replication_and_records_change_id():
    promote = _load("dr_promote")
    patch = promote.promotion_patch("INC-123")

    assert patch["spec"]["replica"]["enabled"] is False
    assert patch["metadata"]["annotations"]["researchforge.io/dr-change-id"] == "INC-123"


def test_platform_preflight_reduces_api_payloads_to_safe_operator_evidence():
    preflight = _load("run_platform_preflight")
    payload = {
        "status": "not_ready",
        "passed": 2,
        "total": 4,
        "checks": [
            {"id": "sandbox", "passed": False, "evidence": "do not copy raw payload"},
            {"id": "model", "passed": True},
        ],
        "credentials": {"token": "must-not-be-reported"},
    }

    assert preflight._summary(payload, "status", "passed", "missing") == {
        "status": "not_ready",
        "passed": 2,
    }
    assert preflight._failed_check_ids(payload) == ["sandbox"]
