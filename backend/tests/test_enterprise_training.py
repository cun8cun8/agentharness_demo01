import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.domain.schemas import Artifact, ArtifactType, CreateTaskRequest, CreateTraceDatasetItemRequest
from app.infra.store import InMemoryStore
from app.services.enterprise_identity import consume_login, provision_identity
from app.services.training import TrainingRequest, prepare_training, start_training, refresh_training


@pytest.fixture(autouse=True)
def settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_sso_subject_is_issuer_scoped_and_least_privilege(monkeypatch):
    store = InMemoryStore()
    with pytest.raises(HTTPException, match="SSO_USER_NOT_PROVISIONED"):
        provision_identity(store, "oidc", "https://a.example", "subject", "same@example.com", "Name")
    monkeypatch.setenv("RESEARCHFORGE_SSO_AUTO_PROVISION", "1")
    left = provision_identity(store, "oidc", "https://a.example", "subject", "same@example.com", "Name")
    right = provision_identity(store, "oidc", "https://b.example", "subject", "same@example.com", "Name")
    assert left.id != right.id and left.role == "viewer"
    left.status = "disabled"
    with pytest.raises(HTTPException, match="SSO_USER_DISABLED"):
        provision_identity(store, "oidc", "https://a.example", "subject", "same@example.com", "Name")


def test_sso_replay_rejected():
    from uuid import uuid4
    key = uuid4().hex
    consume_login(key)
    with pytest.raises(HTTPException, match="SSO_RESPONSE_REPLAYED"):
        consume_login(key)


def test_secret_resolution_uses_current_rotated_value(monkeypatch):
    from app.services.secrets import resolve_secret, SecretUnavailable
    monkeypatch.setenv("EXAMPLE_CREDENTIAL", "first")
    assert resolve_secret("EXAMPLE_CREDENTIAL") == "first"
    monkeypatch.setenv("EXAMPLE_CREDENTIAL", "second")
    assert resolve_secret("EXAMPLE_CREDENTIAL") == "second"
    monkeypatch.setenv("EXAMPLE_CREDENTIAL", "vault://secret/model#key")
    monkeypatch.setenv("VAULT_ADDR", "http://untrusted.invalid")
    with pytest.raises(SecretUnavailable, match="SECRET_RESOLUTION_FAILED"):
        resolve_secret("EXAMPLE_CREDENTIAL")


def test_training_freezes_approved_data_and_registers_successful_artifact(monkeypatch, tmp_path):
    from app.services import training
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_ROOT", str(tmp_path / "jobs"))
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_MODELS", json.dumps({"tiny": str(model)}))
    store = InMemoryStore()
    task = store.create_task(CreateTaskRequest(title="repair", goal="fix addition"))
    run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", None)
    store.add_artifact(Artifact(id="artifact_training", run_id=run.id, type=ArtifactType.DIFF, name="fix.patch", uri="", content="--- a/add.py\n+++ b/add.py\n@@ -1 +1 @@\n-return 0\n+return 1\n"))
    item = store.create_trace_dataset_item(CreateTraceDatasetItemRequest(agent_run_id=run.id, quality_label="good", trace_type="SUCCESS_TRACE", use_case="sft_candidate", usable_for_sft=True))
    request = TrainingRequest(base_model="tiny", item_ids=[item.id])
    with pytest.raises(ValueError, match="APPROVED_SFT"):
        prepare_training(store, request, "user_admin")
    item.status = "approved"
    job = prepare_training(store, request, "user_admin")
    assert job["status"] == "prepared" and job["sample_count"] == 1
    state = {"Status": "created", "Running": False, "ExitCode": 0}
    calls = []
    def fake_docker(*args, **kwargs):
        calls.append(args)
        if args[0] == "start":
            state.update(Status="running", Running=True)
        return SimpleNamespace(returncode=0, stdout=json.dumps([{"State": state}]), stderr="")
    monkeypatch.setattr(training, "docker", fake_docker)
    assert start_training(store, job)["status"] == "running"
    state.update(Status="exited", Running=False)
    output = training.job_dir(job["id"]) / "output"
    (output / "metrics.json").write_text('{"train_loss": 0.5}')
    (output / "model").mkdir()
    (output / "model/config.json").write_text("{}")
    (output / "model/model.safetensors").write_bytes(b"test artifact")
    assert refresh_training(store, job)["status"] == "succeeded"
    assert store.model_versions[job["version_id"]]["status"] == "candidate"
    other = InMemoryStore()
    other._restore_snapshot_data(store._snapshot_data())
    assert other.training_jobs[job["id"]]["dataset_sha256"] == job["dataset_sha256"]


def test_grpo_training_requires_explicit_rl_review_and_freezes_reference(monkeypatch, tmp_path):
    from app.services import training
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_ROOT", str(tmp_path / "jobs"))
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_MODELS", json.dumps({"tiny": str(model)}))
    store = InMemoryStore()
    task = store.create_task(CreateTaskRequest(title="RL repair", goal="repair patch"))
    run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", None)
    store.add_artifact(Artifact(id="artifact_rl", run_id=run.id, type=ArtifactType.DIFF, name="fix.patch", uri="", content="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"))
    item = store.create_trace_dataset_item(CreateTraceDatasetItemRequest(agent_run_id=run.id, quality_label="good", trace_type="SUCCESS_TRACE", use_case="sft_candidate", usable_for_sft=True))
    item.status = "approved"
    request = TrainingRequest(method="grpo", base_model="tiny", item_ids=[item.id])
    with pytest.raises(ValueError, match="APPROVED_RL_DATA_REQUIRED"):
        training.prepare_training(store, request, "user_admin")
    item.usable_for_rl = True
    job = training.prepare_training(store, request, "user_admin")
    dataset = (training.job_dir(job["id"]) / "input" / "dataset.jsonl").read_text(encoding="utf-8")
    assert json.loads(dataset)["reference_patch"].startswith("--- a/x.py")
    source = (Path(__file__).parents[1] / "trainer" / "train.py").read_text(encoding="utf-8")
    assert "GRPOTrainer" in source and "patch_reward" in source and "user supplied" in source


def test_model_promotion_rejects_missing_evaluation():
    from app.services.training import promote_model
    with pytest.raises(ValueError, match="REGISTER_CANDIDATE"):
        promote_model(InMemoryStore(), {"model_name": "candidate"}, "absent", "absent", "user_admin")


def test_model_release_validates_evidence_and_restores_previous_route():
    from datetime import datetime, timezone
    from app.domain.schemas import EvaluationRunResponse, EvaluationScore, ModelProviderConfig, RunStatus
    from app.services.training import promote_model, rollback_model
    store = InMemoryStore()
    candidate = ModelProviderConfig(id="candidate_config", model_name="version_candidate", provider="openai_compatible", status="candidate")
    store.model_configs[candidate.id] = candidate
    assert store.select_model_config("coding")[0].id != candidate.id
    previous = [item.id for item in store.model_configs.values() if item.role == candidate.role and item.status == "active"]
    task = store.create_task(CreateTaskRequest(title="release evidence", goal="verify"))
    run = store.create_run(task.id, "repair_with_critic_v3", "policy_default_v1", candidate.model_name)
    assert run.model_name == candidate.model_name
    store.update_run(run.id, status=RunStatus.COMPLETED)
    patch = Artifact(id="release_patch", run_id=run.id, type=ArtifactType.DIFF, name="fix.patch", uri="", content="model repair", metadata={"patch_source": "task_profile_fallback"})
    store.add_artifact(patch)
    evaluation = EvaluationRunResponse(id="release_eval", benchmark_name="test", policy_version_id="policy_default_v1", agent_strategy_id="repair_with_critic_v3", model_name=candidate.model_name, status=RunStatus.FAILED, finished_at=datetime.now(timezone.utc), items=[EvaluationScore(task_id=task.id, agent_run_id=run.id, success=True, score=1)])
    store.evaluation_runs[evaluation.id] = evaluation
    version = {"id": "release_version", "status": "candidate", "workspace_id": task.workspace_id, "model_name": candidate.model_name}
    store.model_versions[version["id"]] = version
    with pytest.raises(ValueError, match="COMPLETED_MODEL_EVALUATION_REQUIRED"):
        promote_model(store, version, candidate.id, evaluation.id, "admin")
    evaluation.status = RunStatus.COMPLETED
    with pytest.raises(ValueError, match="MODEL_ONLY_EVALUATION_REQUIRED"):
        promote_model(store, version, candidate.id, evaluation.id, "admin")
    patch.metadata["patch_source"] = "model"
    evaluation.summary["regression_count"] = 1
    with pytest.raises(ValueError, match="MODEL_RELEASE_GATE_FAILED"):
        promote_model(store, version, candidate.id, evaluation.id, "admin")
    evaluation.summary["regression_count"] = 0
    deployment = promote_model(store, version, candidate.id, evaluation.id, "admin")
    assert candidate.status == "active"
    assert all(store.model_configs[item].status == "inactive" for item in previous)
    with pytest.raises(ValueError, match="MODEL_VERSION_ALREADY_ACTIVE"):
        promote_model(store, version, candidate.id, evaluation.id, "admin")
    rollback_model(store, deployment, "admin")
    assert candidate.status == "inactive" and version["status"] == "candidate"
    assert all(store.model_configs[item].status == "active" for item in previous)
