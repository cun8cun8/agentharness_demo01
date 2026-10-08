"""Parse immutable provider billing exports into audited reconciliation records."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.domain.schemas import (
    ModelBillingImportIssue,
    ModelBillingImportRequest,
    ModelBillingImportResponse,
    ModelBillingImportPullRequest,
    ModelBillingStatementRequest,
)
from app.infra.idgen import id_generator
from app.services.secrets import resolve_secret


class BillingImportValidationError(ValueError):
    def __init__(self, issues: list[ModelBillingImportIssue]) -> None:
        super().__init__("MODEL_BILLING_IMPORT_INVALID")
        self.issues = issues


def _rows(request: ModelBillingImportRequest) -> list[dict[str, Any]]:
    if request.format == "csv":
        parsed = list(csv.DictReader(io.StringIO(request.content)))
        if not parsed or not all(isinstance(row, dict) for row in parsed):
            raise ValueError("MODEL_BILLING_IMPORT_EMPTY")
        return parsed
    try:
        parsed = json.loads(request.content)
    except json.JSONDecodeError as exc:
        raise ValueError("MODEL_BILLING_IMPORT_JSON_INVALID") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("items")
    if not isinstance(parsed, list) or not parsed or not all(isinstance(row, dict) for row in parsed):
        raise ValueError("MODEL_BILLING_IMPORT_JSON_ROWS_REQUIRED")
    return parsed


def _value(row: dict[str, Any], name: str) -> Any:
    value = row.get(name)
    return value.strip() if isinstance(value, str) else value


def _statement(request: ModelBillingImportRequest, row: dict[str, Any]) -> ModelBillingStatementRequest:
    provider = str(_value(row, "provider") or request.provider).lower()
    if provider != request.provider.lower():
        raise ValueError("MODEL_BILLING_IMPORT_PROVIDER_MISMATCH")
    model_name = _value(row, "model_name") or None
    total_tokens = _value(row, "total_tokens")
    statement = ModelBillingStatementRequest(
        workspace_id=request.workspace_id,
        provider=provider,
        model_name=str(model_name) if model_name else None,
        period_start=_value(row, "period_start"),
        period_end=_value(row, "period_end"),
        actual_cost=float(_value(row, "actual_cost")),
        total_tokens=int(total_tokens) if total_tokens not in {None, ""} else None,
        currency=str(_value(row, "currency") or request.default_currency).upper(),
        fx_rate_to_usd=float(_value(row, "fx_rate_to_usd") or request.default_fx_rate_to_usd) if (_value(row, "fx_rate_to_usd") or request.default_fx_rate_to_usd) else None,
        invoice_reference=str(_value(row, "invoice_reference") or "").strip() or None,
        tolerance=float(_value(row, "tolerance") or 0.01),
        notes=str(_value(row, "notes") or request.notes or "").strip() or None,
    )
    if statement.period_end <= statement.period_start:
        raise ValueError("MODEL_BILLING_PERIOD_INVALID")
    return statement


def _duplicate(store, statement: ModelBillingStatementRequest) -> bool:
    if not statement.invoice_reference:
        return False
    return any(
        item.workspace_id == statement.workspace_id
        and item.provider == statement.provider.lower()
        and item.invoice_reference == statement.invoice_reference
        for item in store.model_billing_reconciliations.values()
    )


def import_model_billing(store, request: ModelBillingImportRequest, actor_id: str, *, source: str = "manual") -> ModelBillingImportResponse:
    rows = _rows(request)
    if len(rows) > 500:
        raise ValueError("MODEL_BILLING_IMPORT_TOO_LARGE")
    if request.workspace_id not in store.workspaces:
        raise ValueError("WORKSPACE_NOT_FOUND")
    statements: list[tuple[int, ModelBillingStatementRequest]] = []
    issues: list[ModelBillingImportIssue] = []
    invoice_references: set[str] = set()
    for number, row in enumerate(rows, start=1):
        try:
            statement = _statement(request, row)
            store.resolve_billing_exchange_rate(statement)
            if statement.invoice_reference and statement.invoice_reference in invoice_references:
                raise ValueError("MODEL_BILLING_IMPORT_DUPLICATE_INVOICE")
            if _duplicate(store, statement):
                raise ValueError("MODEL_BILLING_IMPORT_DUPLICATE_INVOICE")
            if statement.invoice_reference:
                invoice_references.add(statement.invoice_reference)
            statements.append((number, statement))
        except (TypeError, ValueError) as exc:
            issues.append(ModelBillingImportIssue(row_number=number, code=str(exc) or "MODEL_BILLING_IMPORT_ROW_INVALID"))
    if request.strict and issues:
        raise BillingImportValidationError(issues)

    reconciliation_ids = [store.reconcile_model_billing(statement, actor_id).id for _, statement in statements]
    result = ModelBillingImportResponse(
        id=id_generator.next("model_billing_import"),
        workspace_id=request.workspace_id,
        provider=request.provider.lower(),
        format=request.format,
        source=source,
        source_sha256=hashlib.sha256(request.content.encode("utf-8")).hexdigest(),
        strict=request.strict,
        total_rows=len(rows),
        imported_rows=len(reconciliation_ids),
        rejected_rows=len(issues),
        reconciliation_ids=reconciliation_ids,
        issues=issues,
        notes=request.notes,
        submitted_by=actor_id,
    )
    return store.record_model_billing_import(result)


def _billing_export_config(store, request: ModelBillingImportPullRequest) -> tuple[dict[str, Any], str | None]:
    candidates = [
        item for item in store.model_configs.values()
        if item.provider.lower() == request.provider.lower() and isinstance(item.config.get("billing_export"), dict)
    ]
    if request.model_config_id:
        candidates = [item for item in candidates if item.id == request.model_config_id]
    if not candidates:
        raise ValueError("MODEL_BILLING_EXPORT_NOT_CONFIGURED")
    if len(candidates) > 1:
        raise ValueError("MODEL_BILLING_EXPORT_CONFIG_AMBIGUOUS")
    return dict(candidates[0].config["billing_export"]), candidates[0].id


def _billing_export_headers(config: dict[str, Any]) -> dict[str, str]:
    env_name = config.get("api_key_env")
    if not env_name:
        return {}
    if not isinstance(env_name, str) or not env_name or len(env_name) > 200:
        raise ValueError("MODEL_BILLING_EXPORT_CREDENTIAL_INVALID")
    value = resolve_secret(env_name)
    if not value:
        raise ValueError("MODEL_BILLING_EXPORT_CREDENTIAL_MISSING")
    header = str(config.get("header_name") or "Authorization")
    if header.lower() not in {"authorization", "x-api-key", "api-key"}:
        raise ValueError("MODEL_BILLING_EXPORT_HEADER_INVALID")
    prefix = str(config.get("authorization_prefix") or ("Bearer " if header.lower() == "authorization" else ""))
    return {header: prefix + value}


def pull_configured_model_billing(store, request: ModelBillingImportPullRequest, actor_id: str) -> ModelBillingImportResponse:
    config, model_config_id = _billing_export_config(store, request)
    url = str(config.get("url") or "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("MODEL_BILLING_EXPORT_URL_INVALID")
    export_format = str(config.get("format") or "csv").lower()
    if export_format not in {"csv", "json"}:
        raise ValueError("MODEL_BILLING_EXPORT_FORMAT_INVALID")
    timeout = float(config.get("timeout_seconds") or 20)
    if not 1 <= timeout <= 60:
        raise ValueError("MODEL_BILLING_EXPORT_TIMEOUT_INVALID")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, headers=_billing_export_headers(config))
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError("MODEL_BILLING_EXPORT_FETCH_FAILED") from exc
    if len(response.content) > 500_000:
        raise ValueError("MODEL_BILLING_IMPORT_TOO_LARGE")
    imported = import_model_billing(
        store,
        ModelBillingImportRequest(
            workspace_id=request.workspace_id,
            provider=request.provider,
            format=export_format,
            content=response.content.decode("utf-8-sig"),
            default_currency=str(config.get("default_currency") or "USD"),
            default_fx_rate_to_usd=config.get("default_fx_rate_to_usd"),
            strict=request.strict,
            notes=request.notes or f"configured billing export: {model_config_id}",
        ),
        actor_id,
        source="configured_pull",
    )
    return imported
