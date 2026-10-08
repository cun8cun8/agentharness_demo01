"""Resolve credential references at use time so external rotation takes effect."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class SecretUnavailable(RuntimeError):
    pass


_leases: dict[str, dict] = {}
_lease_lock = threading.RLock()


def _secret_file(path: str) -> str:
    file = Path(path)
    if not file.is_absolute() or not file.is_file() or file.stat().st_size > 65536:
        raise ValueError("INVALID_SECRET_FILE")
    return file.read_text(encoding="utf-8").strip()


def _vault_headers():
    token = _secret_file(os.environ["VAULT_TOKEN_FILE"]) if os.getenv("VAULT_TOKEN_FILE") else os.environ["VAULT_TOKEN"]
    return {"X-Vault-Token": token}


def _vault_address():
    address = os.environ["VAULT_ADDR"].rstrip("/")
    if not address.startswith("https://"):
        raise ValueError("VAULT_TLS_REQUIRED")
    return address


def _leased_secret(reference):
    path = f"{reference.netloc}/{reference.path.strip('/')}"
    if not reference.netloc or not reference.path.strip("/") or ".." in path.split("/"):
        raise ValueError("INVALID_SECRET_REFERENCE")
    address = _vault_address()
    key = f"{address}/{path}"
    with _lease_lock:
        cached = _leases.get(key)
        if cached and time.monotonic() >= cached["refresh_at"]:
            if cached["renewable"]:
                response = httpx.post(f"{address}/v1/sys/leases/renew", headers=_vault_headers(),
                                      json={"lease_id": cached["lease_id"]}, timeout=5)
                if response.is_success:
                    result = response.json()
                    duration = float(result.get("lease_duration", 0))
                    if duration > 0:
                        cached.update(refresh_at=time.monotonic() + duration * 0.7, renewable=bool(result.get("renewable")))
                    else:
                        cached = None
                else:
                    cached = None
            else:
                cached = None
        if not cached:
            response = httpx.get(f"{address}/v1/{path}", headers=_vault_headers(), timeout=5)
            response.raise_for_status()
            result = response.json()
            duration = float(result.get("lease_duration", 0))
            if not result.get("lease_id") or duration <= 0:
                raise ValueError("VAULT_LEASE_REQUIRED")
            cached = {"data": result["data"], "lease_id": result["lease_id"], "renewable": bool(result.get("renewable")),
                      "refresh_at": time.monotonic() + duration * 0.7}
        _leases[key] = cached
        return str(cached["data"][reference.fragment or "value"])


def resolve_secret(env_name: str) -> str | None:
    value = os.getenv(env_name)
    if not value:
        return None
    try:
        if value.startswith("file://"):
            return _secret_file(value[7:])
        if value.startswith("vault+lease://"):
            return _leased_secret(urlsplit(value))
        if value.startswith("vault://"):
            reference = urlsplit(value)
            mount = reference.netloc
            path = reference.path.strip("/")
            if not mount or not path or ".." in path.split("/"):
                raise ValueError("INVALID_SECRET_REFERENCE")
            address = _vault_address()
            response = httpx.get(f"{address}/v1/{mount}/data/{path}", headers=_vault_headers(), timeout=5)
            response.raise_for_status()
            return str(response.json()["data"]["data"][reference.fragment or "value"])
        if value.startswith("awssm://"):
            import boto3
            secret_id, _, field = value[8:].partition("#")
            result = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
            secret = result.get("SecretString")
            if secret is None:
                secret = bytes(result["SecretBinary"]).decode()
            return str(json.loads(secret)[field]) if field else secret
        if value.startswith("kms://"):
            import boto3
            result = boto3.client("kms").decrypt(CiphertextBlob=base64.b64decode(value[6:], validate=True),
                                                 EncryptionContext=json.loads(os.getenv("RESEARCHFORGE_KMS_CONTEXT", "{}")))
            return result["Plaintext"].decode()
        return value
    except Exception as exc:
        raise SecretUnavailable("SECRET_RESOLUTION_FAILED") from exc


def rotate_secret(env_name: str) -> dict:
    value = os.getenv(env_name, "")
    if not value.startswith("awssm://"):
        raise ValueError("ROTATION_REQUIRES_AWS_SECRETS_MANAGER")
    import boto3
    result = boto3.client("secretsmanager").rotate_secret(SecretId=value[8:].partition("#")[0])
    return {"status": "rotation_requested", "version_id": result.get("VersionId")}
