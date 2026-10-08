from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.domain.schemas import CreateWorkspaceRequest, ModelProviderConfig
from app.infra.store import WorkspaceQuotaExceeded, store
from app.main import app
from app.services import billing_import


def test_model_invocation_records_workspace_usage_ledger():
    client = TestClient(app)
    workspace = store.create_workspace(
        CreateWorkspaceRequest(name=f"模型账本 {uuid4().hex}", max_daily_cost=1.0)
    )
    model_id = f"ledger-model-{uuid4().hex}"
    model_name = f"ledger-mock-{uuid4().hex}"
    configured = client.post(
        "/api/v1/models",
        json={
            "id": model_id,
            "provider": "mock",
            "model_name": model_name,
            "role": "coding",
            "cost_per_1k_tokens": 0.02,
            "status": "active",
        },
    )
    assert configured.status_code == 200

    response = client.post(
        f"/api/v1/models/invoke?workspace_id={workspace.id}",
        json={"task_type": "coding", "requested_model": model_name, "prompt": "Ledger test", "max_tokens": 32},
    )
    assert response.status_code == 200
    assert response.json()["fallback_used"] is True

    ledger = client.get(f"/api/v1/models/usage?workspace_id={workspace.id}")
    assert ledger.status_code == 200
    assert ledger.json()["total"] == 1
    entry = ledger.json()["items"][0]
    assert entry["model_id"] == model_id
    assert entry["billable_cost"] == 0
    assert ledger.json()["summary"]["fallback_count"] == 1
    usage = store.workspace_usage(workspace.id)
    assert usage["usage"]["direct_model_call_count"] == 1


def test_model_budget_precheck_rejects_projected_spend():
    workspace = store.create_workspace(
        CreateWorkspaceRequest(name=f"模型预算 {uuid4().hex}", max_daily_cost=0.001)
    )
    with pytest.raises(WorkspaceQuotaExceeded, match="WORKSPACE_DAILY_COST_QUOTA_EXCEEDED"):
        store.ensure_workspace_model_budget(workspace.id, 0.0011)


def test_model_billing_reconciliation_records_actual_cost_variance():
    client = TestClient(app)
    workspace = store.create_workspace(CreateWorkspaceRequest(name=f"账单对账 {uuid4().hex}"))
    model = ModelProviderConfig(
        id=f"billing-model-{uuid4().hex}",
        model_name="billing-model",
        provider="openai_compatible",
        status="active",
    )
    store.model_configs[model.id] = model
    store.record_model_usage(
        workspace_id=workspace.id,
        model=model,
        usage={"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
        estimated_cost=0.04,
        billable_cost=0.04,
        fallback_used=False,
        source="test",
        reference_type="test",
        reference_id=None,
        actor_id="user_admin",
    )
    response = client.post(
        "/api/v1/models/billing/reconciliations",
        json={
            "workspace_id": workspace.id,
            "provider": "openai_compatible",
            "model_name": model.model_name,
            "period_start": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "period_end": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            "actual_cost": 0.06,
            "total_tokens": 60,
            "invoice_reference": "invoice-2026-09",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "under_estimated"
    assert payload["variance_cost"] == 0.02
    listed = client.get(f"/api/v1/models/billing/reconciliations?workspace_id={workspace.id}")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["invoice_reference"] == "invoice-2026-09"


def test_model_billing_csv_import_is_audited_and_reports_invalid_rows():
    client = TestClient(app)
    workspace = store.create_workspace(CreateWorkspaceRequest(name=f"批量账单 {uuid4().hex}"))
    now = datetime.now(timezone.utc)
    csv_content = "period_start,period_end,actual_cost,total_tokens,invoice_reference\n"
    csv_content += f"{(now - timedelta(hours=1)).isoformat()},{now.isoformat()},0.03,30,invoice-csv-1\n"
    csv_content += f"{now.isoformat()},{(now - timedelta(hours=1)).isoformat()},0.04,40,invoice-csv-2\n"
    response = client.post(
        "/api/v1/models/billing/imports",
        json={
            "workspace_id": workspace.id,
            "provider": "openai_compatible",
            "format": "csv",
            "content": csv_content,
            "strict": False,
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["total_rows"] == 2
    assert payload["imported_rows"] == 1
    assert payload["rejected_rows"] == 1
    assert payload["issues"][0]["code"] == "MODEL_BILLING_PERIOD_INVALID"
    imports = client.get(f"/api/v1/models/billing/imports?workspace_id={workspace.id}")
    assert imports.status_code == 200
    assert imports.json()["items"][0]["id"] == payload["id"]


def test_strict_model_billing_import_writes_nothing_when_a_row_is_invalid():
    client = TestClient(app)
    workspace = store.create_workspace(CreateWorkspaceRequest(name=f"严格账单 {uuid4().hex}"))
    now = datetime.now(timezone.utc)
    response = client.post(
        "/api/v1/models/billing/imports",
        json={
            "workspace_id": workspace.id,
            "provider": "openai_compatible",
            "format": "json",
            "content": '[{"period_start": "' + (now - timedelta(minutes=1)).isoformat() + '", "period_end": "' + now.isoformat() + '", "actual_cost": 0.02}, {"period_start": "invalid", "period_end": "invalid", "actual_cost": 0.03}]',
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MODEL_BILLING_IMPORT_INVALID"
    assert not store.list_model_billing_reconciliations(workspace.id)


def test_model_billing_preserves_source_currency_and_reconciles_in_usd():
    client = TestClient(app)
    workspace = store.create_workspace(CreateWorkspaceRequest(name=f"多币种账单 {uuid4().hex}"))
    now = datetime.now(timezone.utc)
    response = client.post(
        "/api/v1/models/billing/reconciliations",
        json={
            "workspace_id": workspace.id,
            "provider": "anthropic",
            "period_start": (now - timedelta(minutes=1)).isoformat(),
            "period_end": now.isoformat(),
            "actual_cost": 2.0,
            "currency": "EUR",
            "fx_rate_to_usd": 1.08,
            "invoice_reference": "invoice-eur-1",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["currency"] == "EUR"
    assert payload["actual_cost"] == 2.0
    assert payload["actual_cost_usd"] == 2.16
    assert payload["fx_rate_source"] == "statement"


def test_configured_billing_export_pull_uses_server_side_credential(monkeypatch):
    client = TestClient(app)
    workspace = store.create_workspace(CreateWorkspaceRequest(name=f"自动账单 {uuid4().hex}"))
    model = ModelProviderConfig(
        id=f"billing-export-{uuid4().hex}",
        model_name="export-model",
        provider="openai_compatible",
        status="active",
        config={"billing_export": {"url": "https://billing.example.test/export.csv", "format": "csv", "api_key_env": "TEST_BILLING_EXPORT_TOKEN"}},
    )
    store.model_configs[model.id] = model
    monkeypatch.setenv("TEST_BILLING_EXPORT_TOKEN", "secret-token")
    now = datetime.now(timezone.utc)
    csv_content = "period_start,period_end,actual_cost,invoice_reference\n"
    csv_content += f"{(now - timedelta(hours=1)).isoformat()},{now.isoformat()},0.08,invoice-pull-1\n"

    class FakeResponse:
        content = csv_content.encode("utf-8")
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def get(self, url, headers):
            assert url == "https://billing.example.test/export.csv"
            assert headers == {"Authorization": "Bearer secret-token"}
            return FakeResponse()

    monkeypatch.setattr(billing_import.httpx, "Client", FakeClient)
    response = client.post(
        "/api/v1/models/billing/imports/pull",
        json={"workspace_id": workspace.id, "provider": "openai_compatible", "model_config_id": model.id},
    )
    assert response.status_code == 201
    assert response.json()["source"] == "configured_pull"
    assert response.json()["imported_rows"] == 1
