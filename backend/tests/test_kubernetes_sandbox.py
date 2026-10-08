import json
from types import SimpleNamespace

import pytest
from app.config import get_settings
from app.main import app
from fastapi.testclient import TestClient
from app.services import sandbox_runner as sandbox_runner_module
from app.services.sandbox_runner import SandboxRunner


def test_kubernetes_manifest_and_probe(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_KUBERNETES_NAMESPACE", "researchforge")
    get_settings.cache_clear()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if "version" in command:
            return SimpleNamespace(returncode=0, stdout="{\"clientVersion\":{\"gitVersion\":\"v1.31.0\"}}", stderr="")
        return SimpleNamespace(returncode=0, stdout="yes\n", stderr="")

    monkeypatch.setattr(sandbox_runner_module.subprocess, "run", fake_run)
    try:
        runner = SandboxRunner()
        repo = tmp_path / "repo"
        repo.mkdir()
        manifest = runner.build_kubernetes_manifest(["python", "-m", "pytest", "-q"], repo)
        container = manifest["spec"]["containers"][0]
        assert manifest["metadata"]["namespace"] == "researchforge"
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert container["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
        policy = runner.build_kubernetes_network_policy(manifest["metadata"]["name"])
        assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
        assert policy["spec"]["ingress"] == []
        assert policy["spec"]["egress"] == []
        probe = runner.probe()
        assert probe["ready"] is True
        assert probe["version"] == "v1.31.0"
        assert any("auth" in command for command in calls)
    finally:
        get_settings.cache_clear()



def test_kubernetes_run_applies_polls_logs_and_deletes(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    get_settings.cache_clear()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        if "get" in command:
            body = {"status": {"phase": "Succeeded", "containerStatuses": [{"state": {"terminated": {"exitCode": 0}}}]}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        if "logs" in command:
            return SimpleNamespace(returncode=0, stdout="k8s-ok\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sandbox_runner_module.subprocess, "run", fake_run)
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        result = SandboxRunner().run(["python", "-c", "print(1)"], cwd=repo, workspace_root=repo, timeout_seconds=1)
        assert result.backend == "kubernetes"
        assert result.returncode == 0
        assert result.stdout == "k8s-ok\n"
        assert any("apply" in command for command in calls)
        assert any("get" in command for command in calls)
        assert any("logs" in command for command in calls)
        assert any("delete" in command for command in calls)
    finally:
        get_settings.cache_clear()



def test_kubernetes_manifest_api(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    get_settings.cache_clear()
    try:
        response = TestClient(app).get("/api/v1/sandbox/kubernetes-manifest", params={"repo_path": "/workspace/project"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["backend"] == "kubernetes"
        assert payload["manifest"]["kind"] == "Pod"
        assert payload["manifest"]["spec"]["volumes"][0]["hostPath"]["path"] == "/workspace/project"
        assert len(payload["resources"]) == 2
        assert payload["resources"][1]["kind"] == "NetworkPolicy"
    finally:
        get_settings.cache_clear()

def test_kubernetes_manifest_uses_workspace_pvc_when_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC", "researchforge-workspace")
    get_settings.cache_clear()
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        manifest = SandboxRunner().build_kubernetes_manifest(
            ["python", "-m", "pytest", "-q"],
            repo,
        )
        volume = manifest["spec"]["volumes"][0]
        assert volume["persistentVolumeClaim"] == {
            "claimName": "researchforge-workspace",
            "readOnly": False,
        }
        assert "hostPath" not in volume
        assert SandboxRunner().policy_summary()["kubernetes"]["workspace_pvc"] == "researchforge-workspace"
    finally:
        get_settings.cache_clear()


def test_kubernetes_manifest_uses_shared_workspace_subpath(monkeypatch, tmp_path) -> None:
    shared = tmp_path / "shared"
    repo = shared / "run-123"
    repo.mkdir(parents=True)
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC", "researchforge-workspace")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_SHARED_WORKSPACE_ROOT", str(shared))
    get_settings.cache_clear()
    try:
        manifest = SandboxRunner().build_kubernetes_manifest(["python", "-c", "print(1)"], repo)
        volume = manifest["spec"]["volumes"][0]
        assert volume["persistentVolumeClaim"]["subPath"] == "run-123"
        assert manifest["spec"]["containers"][0]["workingDir"] == "/workspace"
    finally:
        get_settings.cache_clear()


def test_production_kubernetes_requires_pvc_and_explicit_egress(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("RESEARCHFORGE_ENV", "production")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_BACKEND", "kubernetes")
    monkeypatch.setenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", "1")
    get_settings.cache_clear()
    try:
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(Exception, match="PRODUCTION_KUBERNETES_PVC_REQUIRED"):
            SandboxRunner().build_kubernetes_manifest(["python", "-c", "print(1)"], repo)

        monkeypatch.setenv("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC", "workspace-pvc")
        get_settings.cache_clear()
        manifest = SandboxRunner().build_kubernetes_manifest(["python", "-c", "print(1)"], repo)
        assert manifest["spec"]["containers"][0]["securityContext"]["runAsNonRoot"] is True
        assert SandboxRunner().build_kubernetes_network_policy("sandbox") ["spec"]["egress"] == []
    finally:
        monkeypatch.setenv("RESEARCHFORGE_ENV", "local")
        monkeypatch.delenv("RESEARCHFORGE_SANDBOX_NETWORK_ENABLED", raising=False)
        monkeypatch.delenv("RESEARCHFORGE_SANDBOX_KUBERNETES_WORKSPACE_PVC", raising=False)
        get_settings.cache_clear()
