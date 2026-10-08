import json
import sys
from types import SimpleNamespace

import pytest

from app.domain.schemas import CreateTaskRequest, ModelProviderConfig, RunStatus
from app.infra.store import InMemoryStore
from app.services import canary, secrets


def prepared(monkeypatch):
    store = InMemoryStore()
    baseline = ModelProviderConfig(id="baseline", provider="openai_compatible", model_name="base")
    candidate = ModelProviderConfig(id="candidate", provider="openai_compatible", model_name="next", status="candidate")
    store.model_configs = {baseline.id: baseline, candidate.id: candidate}
    store.model_versions["version"] = {"id": "version", "workspace_id": "workspace_default"}
    monkeypatch.setattr(canary, "validate_model_release", lambda *args: (candidate, None))
    record = canary.create_canary(store, canary.CanaryRequest(version_id="version", candidate_model_id="candidate", baseline_model_id="baseline",
                                                              evaluation_id="eval", traffic_percent=50, min_samples=2), "user_admin")
    task = store.create_task(CreateTaskRequest(title="canary test", goal="test"))
    return store, task, record


def test_canary_traffic_is_stable_workspace_scoped_and_explicit_model_bypasses(monkeypatch):
    store, task, record = prepared(monkeypatch)
    baseline = store.model_configs["baseline"]
    assigned = [canary.route_canary(store, task, f"run_{i}", baseline)[1]["canary_arm"] for i in range(200)]
    assert 60 < assigned.count("candidate") < 140
    assert canary.route_canary(store, task, "stable", baseline) == canary.route_canary(store, task, "stable", baseline)
    other = task.model_copy(update={"workspace_id": "outside"})
    assert canary.route_canary(store, other, "x", baseline)[1] == {}
    run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", "base")
    assert not run.metrics.get("canary_id")


def test_canary_failure_gate_auto_rolls_back_and_does_not_double_count(monkeypatch):
    store, task, record = prepared(monkeypatch)
    for i in range(2):
        run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", "next")
        run.metrics.update(canary_id=record["id"], canary_arm="candidate")
        store.update_run(run.id, status=RunStatus.FAILED, finish=True)
    assert record["status"] == "rolled_back" and record["rollback_reason"] == "CANARY_FAILURE_RATE_EXCEEDED"
    canary.measure_canary(store, record)
    assert record["metrics"]["candidate"]["samples"] == 2


def test_canary_cost_gate_and_promotion_require_both_arms(monkeypatch):
    store, task, record = prepared(monkeypatch)
    with pytest.raises(ValueError, match="INSUFFICIENT_SAMPLES"):
        canary.promote_canary(store, record, "user_admin")
    for arm, model, cost in [("baseline", "base", 1), ("baseline", "base", 1), ("candidate", "next", 3), ("candidate", "next", 3)]:
        run = store.create_run(task.id, "repair_baseline_v1", "policy_default_v1", model)
        run.metrics.update(canary_id=record["id"], canary_arm=arm)
        store.update_run(run.id, status=RunStatus.COMPLETED, total_cost=cost, finish=True)
    assert record["rollback_reason"] == "CANARY_COST_INCREASE_EXCEEDED"


def test_vault_lease_renews_and_rotates_using_current_agent_token(monkeypatch, tmp_path):
    token = tmp_path / "vault-token"
    token.write_text("token-one")
    monkeypatch.setenv("VAULT_ADDR", "https://vault.test")
    monkeypatch.setenv("VAULT_TOKEN_FILE", str(token))
    monkeypatch.setenv("LEASE_TEST", "vault+lease://aws/creds/role#access_key")
    secrets._leases.clear()
    clock = [0]
    monkeypatch.setattr(secrets.time, "monotonic", lambda: clock[0])
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs["headers"]["X-Vault-Token"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"lease_id": "aws/lease", "lease_duration": 10, "renewable": True, "data": {"access_key": "key"}})
    monkeypatch.setattr(secrets.httpx, "get", get)
    monkeypatch.setattr(secrets.httpx, "post", lambda *args, **kwargs: SimpleNamespace(is_success=True, json=lambda: {"lease_duration": 10, "renewable": True}))
    assert secrets.resolve_secret("LEASE_TEST") == "key"
    clock[0] = 8
    assert secrets.resolve_secret("LEASE_TEST") == "key" and len(calls) == 1
    token.write_text("token-two")
    clock[0] = 16
    monkeypatch.setattr(secrets.httpx, "post", lambda *args, **kwargs: SimpleNamespace(is_success=False))
    assert secrets.resolve_secret("LEASE_TEST") == "key" and calls == ["token-one", "token-two"]
    secrets._leases.clear()


def test_s3_client_is_rebuilt_after_credential_rotation(monkeypatch):
    from app.infra.artifact_store import ArtifactBlobStore
    clients = []
    def make(*args, **kwargs):
        client = SimpleNamespace(credentials=kwargs)
        clients.append(client)
        return client
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=make))
    monkeypatch.setenv("MINIO_ROOT_USER", "first-key")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "first-secret")
    store = ArtifactBlobStore(SimpleNamespace())
    first = store._client()
    assert store._client() is first
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "rotated-secret")
    assert store._client() is not first and len(clients) == 2
    assert clients[-1].credentials["aws_secret_access_key"] == "rotated-secret"
