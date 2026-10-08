import hashlib
import json
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.infra.store import InMemoryStore
from app.services import training


@pytest.fixture(autouse=True)
def settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _record(monkeypatch, tmp_path):
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_BACKEND", "kubernetes")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_ROOT", str(tmp_path / "training"))
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_KUBERNETES_WORKSPACE_PVC", "researchforge-workspace")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_KUBERNETES_SHARED_ROOT", str(tmp_path))
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_KUBERNETES_NAMESPACE", "researchforge")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_GPUS", "2")
    path = training.job_dir("train_" + "a" * 32)
    (path / "input").mkdir(parents=True)
    (path / "output").mkdir()
    dataset = b'{"prompt":"repair","completion":"patch"}\n'
    (path / "input" / "dataset.jsonl").write_bytes(dataset)
    model = tmp_path / "models" / "tiny"
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}", encoding="utf-8")
    return {
        "id": "train_" + "a" * 32,
        "workspace_id": "workspace_default",
        "status": "prepared",
        "max_seconds": 3600,
        "dataset_sha256": hashlib.sha256(dataset).hexdigest(),
        "base_path": str(model),
    }


def test_kubernetes_training_manifest_lifecycle_and_artifact_validation(monkeypatch, tmp_path):
    record = _record(monkeypatch, tmp_path)
    record.update(gpu_count=2, world_size=2)
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_KUBERNETES_NODE_SELECTOR", '{"researchforge.io/workload":"gpu-training"}')
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_KUBERNETES_TOLERATIONS", '[{"key":"nvidia.com/gpu","operator":"Equal","value":"true","effect":"NoSchedule"}]')
    get_settings.cache_clear()
    manifest = training.build_kubernetes_manifest(record)
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert manifest["kind"] == "Job"
    assert manifest["metadata"]["namespace"] == "researchforge"
    assert container["resources"]["limits"]["nvidia.com/gpu"] == "2"
    assert container["command"] == ["torchrun", "--standalone", "--nproc_per_node", "2", "/app/train.py"]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert {mount["mountPath"] for mount in container["volumeMounts"]} == {"/input", "/output", "/model", "/tmp"}
    pod_spec = manifest["spec"]["template"]["spec"]
    assert pod_spec["nodeSelector"] == {"researchforge.io/workload": "gpu-training"}
    assert pod_spec["tolerations"][0]["key"] == "nvidia.com/gpu"

    state = {"phase": "missing"}
    calls = []

    def fake_kubectl(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("get", "job", training.training_job_name(record)):
            if state["phase"] == "missing":
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            status = {"active": 1} if state["phase"] == "running" else {"succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
            return SimpleNamespace(returncode=0, stdout=json.dumps({"status": status}), stderr="")
        if args[0] == "apply":
            state["phase"] = "running"
            return SimpleNamespace(returncode=0, stdout="job applied", stderr="")
        if args[0] == "logs":
            return SimpleNamespace(returncode=0, stdout="trainer finished", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(training, "kubectl", fake_kubectl)
    store = InMemoryStore()
    store.training_jobs[record["id"]] = record
    assert training.start_training(store, record)["status"] == "running"
    assert record["backend"] == "kubernetes"
    output = training.job_dir(record["id"]) / "output"
    (output / "metrics.json").write_text('{"train_loss":0.2}', encoding="utf-8")
    (output / "model").mkdir()
    (output / "model" / "config.json").write_text("{}", encoding="utf-8")
    (output / "model" / "model.safetensors").write_bytes(b"weights")
    state["phase"] = "succeeded"
    assert training.refresh_training(store, record)["status"] == "succeeded"
    assert record["backend_log"] == "trainer finished"
    assert record["version_id"] in store.model_versions
    assert any(call[0] == "delete" for call in calls)


def test_kubernetes_training_rejects_non_shared_model_path(monkeypatch, tmp_path):
    record = _record(monkeypatch, tmp_path)
    outside = tmp_path.parent / "outside-model"
    outside.mkdir(exist_ok=True)
    (outside / "config.json").write_text("{}", encoding="utf-8")
    record["base_path"] = str(outside)
    with pytest.raises(ValueError, match="TRAINING_KUBERNETES_PATH_NOT_SHARED"):
        training.build_kubernetes_manifest(record)


def test_distributed_training_requires_sufficient_gpu_capacity(monkeypatch, tmp_path):
    record = _record(monkeypatch, tmp_path)
    record.update(gpu_count=1, world_size=2)
    with pytest.raises(ValueError, match="TRAINING_DISTRIBUTED_GPU_CAPACITY_REQUIRED"):
        training.build_kubernetes_manifest(record)


def test_multinode_training_builds_pytorchjob_and_uses_operator_logs(monkeypatch, tmp_path):
    record = _record(monkeypatch, tmp_path)
    record.update(gpu_count=2, world_size=2, node_count=3)
    manifest = training.build_kubernetes_manifest(record)
    assert manifest["apiVersion"] == "kubeflow.org/v1"
    assert manifest["kind"] == "PyTorchJob"
    assert manifest["spec"]["pytorchReplicaSpecs"]["Worker"]["replicas"] == 2
    launch = manifest["spec"]["pytorchReplicaSpecs"]["Master"]["template"]["spec"]["containers"][0]["command"]
    assert "--nnodes=3" in launch[-1]
    assert "${MASTER_ADDR}" in launch[-1]

    calls = []
    def fake_kubectl(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("get", "pytorchjob", training.training_job_name(record)):
            return SimpleNamespace(returncode=0, stdout=json.dumps({"status": {"conditions": [{"type": "Succeeded", "status": "True"}]}}), stderr="")
        if args[0] == "logs":
            return SimpleNamespace(returncode=0, stdout="rank-0 complete", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(training, "kubectl", fake_kubectl)
    output = training.job_dir(record["id"]) / "output"
    (output / "metrics.json").write_text('{"train_loss":0.2}', encoding="utf-8")
    (output / "model").mkdir()
    (output / "model" / "config.json").write_text("{}", encoding="utf-8")
    (output / "model" / "model.safetensors").write_bytes(b"weights")
    record.update(status="running", backend="kubernetes", backend_resource="pytorchjob", started_at=training.now())
    store = InMemoryStore(); store.training_jobs[record["id"]] = record
    assert training.refresh_training(store, record)["status"] == "succeeded"
    assert any(call[:2] == ("logs", "-l") for call in calls)
