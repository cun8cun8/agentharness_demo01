"""GitHub App installation-token authentication for repository operations."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx
import jwt

from app.config import Settings, get_settings
from app.services.secrets import resolve_secret


class GitHubAppError(RuntimeError):
    pass


_token_cache: dict[int, tuple[str, datetime]] = {}
_token_lock = Lock()


def clear_token_cache() -> None:
    with _token_lock:
        _token_cache.clear()


def app_enabled(settings: Settings | None = None) -> bool:
    current = settings or get_settings()
    return bool(current.github_app_id and resolve_secret(current.github_app_private_key_env))


def installation_token(installation_id: int, settings: Settings | None = None) -> str:
    current = settings or get_settings()
    if installation_id < 1:
        raise GitHubAppError("GITHUB_APP_INSTALLATION_ID_INVALID")
    app_id = current.github_app_id
    private_key = resolve_secret(current.github_app_private_key_env)
    if not app_id or not private_key:
        raise GitHubAppError("GITHUB_APP_NOT_CONFIGURED")
    now = datetime.now(timezone.utc)
    with _token_lock:
        cached = _token_cache.get(installation_id)
        if cached and cached[1] > now + timedelta(seconds=current.github_app_token_refresh_seconds):
            return cached[0]
    try:
        signed = jwt.encode({"iat": int(now.timestamp()) - 60, "exp": int((now + timedelta(minutes=9)).timestamp()), "iss": str(app_id)}, private_key, algorithm="RS256")
        endpoint = current.github_api_base_url.rstrip("/") + f"/app/installations/{installation_id}/access_tokens"
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.post(endpoint, headers={"Authorization": f"Bearer {signed}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError) as exc:
        raise GitHubAppError("GITHUB_APP_TOKEN_EXCHANGE_FAILED") from exc
    token = payload.get("token")
    expires_at = _parse_expiry(payload.get("expires_at"), now)
    if not isinstance(token, str) or not token or expires_at <= now + timedelta(seconds=current.github_app_token_refresh_seconds):
        raise GitHubAppError("GITHUB_APP_TOKEN_INVALID")
    with _token_lock:
        _token_cache[installation_id] = (token, expires_at)
    return token


def repository_token(repository, settings: Settings | None = None) -> str | None:
    installation_id = getattr(repository, "github_installation_id", None)
    if installation_id is not None:
        return installation_token(int(installation_id), settings)
    credential_ref = getattr(repository, "credential_ref", None)
    return resolve_secret(str(credential_ref)) if credential_ref else None


def _parse_expiry(value: object, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def installation_permissions(installation_id: int, settings: Settings | None = None) -> dict[str, str]:
    """Return permissions granted to an App installation, not misleading repo user permissions."""
    current = settings or get_settings()
    app_id = current.github_app_id
    private_key = resolve_secret(current.github_app_private_key_env)
    if not app_id or not private_key or installation_id < 1:
        raise GitHubAppError("GITHUB_APP_NOT_CONFIGURED")
    now = datetime.now(timezone.utc)
    try:
        signed = jwt.encode({"iat": int(now.timestamp()) - 60, "exp": int((now + timedelta(minutes=9)).timestamp()), "iss": str(app_id)}, private_key, algorithm="RS256")
        endpoint = current.github_api_base_url.rstrip("/") + f"/app/installations/{installation_id}"
        response = httpx.get(endpoint, headers={"Authorization": f"Bearer {signed}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}, timeout=20, follow_redirects=False)
        response.raise_for_status()
        permissions = response.json().get("permissions") or {}
        return {str(key): str(value) for key, value in permissions.items()}
    except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError) as exc:
        raise GitHubAppError("GITHUB_APP_INSTALLATION_LOOKUP_FAILED") from exc
