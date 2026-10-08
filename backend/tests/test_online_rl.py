import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import Artifact, ArtifactType, CreateTaskRequest, CreateTraceDatasetItemRequest
from app.infra.store import InMemoryStore
from app.infra.store import store as app_store
from app.main import app
from app.services import online_rl, training


def prepared(monkeypatch):
    store = InMemoryStore()
    task = store.create_task(CreateTaskRequest(title="rl task", goal="修复返回值"))
    run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", "mock-coding-agent")
    store.add_artifact(Artifact(id="rl_patch", run_id=run.id, type=ArtifactType.DIFF, name="fix.patch", uri="", content="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-return 0\n+return 1\n"))
    item = store.create_trace_dataset_item(CreateTraceDatasetItemRequest(
        agent_run_id=run.id, quality_label="good", trace_type="SUCCESS_TRACE", use_case="sft_candidate",
        usable_for_rl=True,
    ))
    item.status = "approved"
    monkeypatch.setattr(online_rl, "invoke_configured_model", lambda *args, **kwargs: SimpleNamespace(
        output_text="--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-return 0\n+return 1\n",
        usage={"total_tokens": 12}, estimated_cost=0.01, fallback_used=False,
    ))
    return store, item


def test_online_rl_session_rollout_is_persistent_and_bounded(monkeypatch):
    store, item = prepared(monkeypatch)
    session = online_rl.create_session(store, online_rl.OnlineRLRequest(reference_item_ids=[item.id], max_rollouts=1), "user_admin")
    rollout = online_rl.rollout(store, session, "user_admin")
    assert rollout["reward"] > 0.9
    assert session["status"] == "completed"
    with pytest.raises(ValueError, match="ROLLOUT_LIMIT"):
        online_rl.rollout(store, session, "user_admin")
    exported = online_rl.export_dataset(store, session, online_rl.OnlineRLExportRequest(), "user_admin")
    assert exported["row_count"] == 1
    assert '"prompt": "修复返回值"' in exported["jsonl"]


def test_online_rl_rejects_unapproved_or_cross_workspace_reference(monkeypatch):
    store, item = prepared(monkeypatch)
    item.usable_for_rl = False
    with pytest.raises(ValueError, match="APPROVED_RL_DATA_REQUIRED"):
        online_rl.create_session(store, online_rl.OnlineRLRequest(reference_item_ids=[item.id]), "user_admin")


def test_frozen_online_rl_session_prepares_grpo_data(monkeypatch, tmp_path):
    store, item = prepared(monkeypatch)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("RESEARCHFORGE_TRAINING_MODELS", '{"tiny":"' + str(model).replace("\\", "\\\\") + '"}')
    session = online_rl.create_session(store, online_rl.OnlineRLRequest(reference_item_ids=[item.id], max_rollouts=2), "user_admin")
    online_rl.rollout(store, session, "user_admin")
    with pytest.raises(ValueError, match="NOT_FROZEN"):
        training.prepare_training(store, training.TrainingRequest(method="grpo", base_model="tiny", online_rl_session_id=session["id"]), "user_admin")
    exported = online_rl.export_dataset(store, session, online_rl.OnlineRLExportRequest(), "user_admin")
    job = training.prepare_training(store, training.TrainingRequest(method="grpo", base_model="tiny", online_rl_session_id=session["id"]), "user_admin")
    dataset = (training.job_dir(job["id"]) / "input" / "dataset.jsonl").read_text(encoding="utf-8")
    assert session["status"] == "frozen"
    assert json.loads(dataset)["reference_patch"].startswith("--- a/app.py")
    assert job["online_rl_export_sha256"] == exported["sha256"]
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        training.prepare_training(store, training.TrainingRequest(method="grpo", base_model="tiny", item_ids=[item.id], online_rl_session_id=session["id"]), "user_admin")


def test_online_rl_http_routes_persist_rollout_state(monkeypatch):
    task = app_store.create_task(CreateTaskRequest(title="http rl task", goal="修复 HTTP 入口"))
    run = app_store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", "mock-coding-agent")
    app_store.add_artifact(Artifact(id="http_rl_patch", run_id=run.id, type=ArtifactType.DIFF, name="fix.patch", uri="", content="--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"))
    item = app_store.create_trace_dataset_item(CreateTraceDatasetItemRequest(agent_run_id=run.id, quality_label="good", trace_type="SUCCESS_TRACE", use_case="sft_candidate", usable_for_rl=True))
    item.status = "approved"
    with TestClient(app) as client:
        response = client.post("/api/v1/training/online-rl", json={"reference_item_ids": [item.id], "max_rollouts": 1})
        assert response.status_code == 201
        session_id = response.json()["id"]
        rollout = client.post(f"/api/v1/training/online-rl/{session_id}/rollouts", json={})
        assert rollout.status_code == 200
        current = client.get(f"/api/v1/training/online-rl/{session_id}")
        assert current.status_code == 200 and current.json()["rollout_count"] == 1
