from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import Settings, get_settings
from app.services.secrets import resolve_secret


class GitHubOAuthError(RuntimeError):
    pass


def _client_secret(settings: Settings) -> str | None:
    return resolve_secret(settings.github_oauth_client_secret_env) or None


def _state_secret(settings: Settings) -> str | None:
    api_key = (
        resolve_secret(settings.api_key_env)
        if settings.api_key_env
        else settings.api_key
    )
    return resolve_secret(settings.github_oauth_state_secret_env) or _client_secret(settings) or api_key or None


def oauth_enabled(settings: Settings | None = None) -> bool:
    current = settings or get_settings()
    return bool(current.github_oauth_client_id and _client_secret(current))


def build_authorization_url(settings: Settings | None = None) -> tuple[str, str]:
    current = settings or get_settings()
    if not oauth_enabled(current):
        raise GitHubOAuthError("GitHub OAuth 尚未配置客户端 ID 和客户端密钥。")
    state = create_state(current)
    query = urlencode({
        "client_id": current.github_oauth_client_id,
        "redirect_uri": current.github_oauth_redirect_uri,
        "scope": current.github_oauth_scopes,
        "state": state,
    })
    return f"{current.github_oauth_authorize_url}?{query}", state


def create_state(settings: Settings | None = None) -> str:
    current = settings or get_settings()
    secret = _state_secret(current)
    if not secret:
        raise GitHubOAuthError("GitHub OAuth 状态签名密钥未配置。")
    payload = {"iat": int(time.time()), "nonce": secrets.token_urlsafe(18)}
    encoded = _encode_json(payload)
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_state(state: str, settings: Settings | None = None) -> dict[str, Any]:
    current = settings or get_settings()
    secret = _state_secret(current)
    if not secret or not state or "." not in state:
        raise GitHubOAuthError("OAuth 状态无效或已过期。")
    encoded, signature = state.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _b64decode(signature)
    except ValueError as exc:
        raise GitHubOAuthError("OAuth 状态无效或已过期。") from exc
    if not hmac.compare_digest(expected, supplied):
        raise GitHubOAuthError("OAuth 状态签名校验失败。")
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        issued_at = int(payload["iat"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GitHubOAuthError("OAuth 状态无效或已过期。") from exc
    now = int(time.time())
    if issued_at > now + 60 or now - issued_at > max(60, current.github_oauth_state_ttl_seconds):
        raise GitHubOAuthError("OAuth 状态无效或已过期。")
    return payload


def create_session_cookie(user_id: str, settings: Settings | None = None) -> str:
    current = settings or get_settings()
    secret = _state_secret(current)
    if not secret:
        raise GitHubOAuthError("GitHub OAuth 会话签名密钥未配置。")
    issued_at = int(time.time())
    payload = {
        "sub": user_id,
        "iat": issued_at,
        "exp": issued_at + 30 * 24 * 60 * 60,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = _encode_json(payload)
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def verify_session_cookie(token: str, settings: Settings | None = None) -> str:
    current = settings or get_settings()
    secret = _state_secret(current)
    if not secret or not token or "." not in token:
        raise GitHubOAuthError("登录会话无效或已过期。")
    encoded, signature = token.rsplit(".", 1)
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        supplied = _b64decode(signature)
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        user_id = str(payload["sub"])
        issued_at = int(payload["iat"])
        expires_at = int(payload["exp"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GitHubOAuthError("登录会话无效或已过期。") from exc
    now = int(time.time())
    if not hmac.compare_digest(expected, supplied) or issued_at > now + 60 or expires_at < now:
        raise GitHubOAuthError("登录会话无效或已过期。")
    return user_id

def exchange_code(code: str, settings: Settings | None = None) -> str:
    current = settings or get_settings()
    secret = _client_secret(current)
    if not current.github_oauth_client_id or not secret:
        raise GitHubOAuthError("GitHub OAuth 尚未配置。")
    response = _request_json(
        current.github_oauth_token_url,
        method="POST",
        data={
            "client_id": current.github_oauth_client_id,
            "client_secret": secret,
            "code": code,
            "redirect_uri": current.github_oauth_redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=current.research_timeout_seconds,
    )
    token = response.get("access_token") if isinstance(response, dict) else None
    if not token:
        message = None
        if isinstance(response, dict):
            message = response.get("error_description") or response.get("error")
        raise GitHubOAuthError(str(message or "GitHub 未返回访问令牌。"))
    return str(token)


def fetch_identity(access_token: str, settings: Settings | None = None) -> dict[str, str]:
    current = settings or get_settings()
    base_url = current.github_api_base_url.rstrip("/")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "ResearchForge-Agent-Harness",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    profile = _request_json(f"{base_url}/user", headers=headers, timeout=current.research_timeout_seconds)
    if not isinstance(profile, dict) or not profile.get("id"):
        raise GitHubOAuthError("GitHub 用户资料无效。")
    emails: list[dict[str, Any]] = []
    try:
        raw_emails = _request_json(f"{base_url}/user/emails", headers=headers, timeout=current.research_timeout_seconds)
        if isinstance(raw_emails, list):
            emails = [item for item in raw_emails if isinstance(item, dict)]
    except GitHubOAuthError:
        pass
    email = next((str(item.get("email")) for item in emails if item.get("primary") and item.get("verified") and item.get("email")), None)
    email = email or next((str(item.get("email")) for item in emails if item.get("verified") and item.get("email")), None)
    email = email or str(profile.get("email") or "")
    login = str(profile.get("login") or profile["id"])
    return {
        "external_id": str(profile["id"]),
        "login": login,
        "name": str(profile.get("name") or login),
        "email": email or f"{login}@users.noreply.github.com",
    }


def _request_json(url: str, *, method: str = "GET", data: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: int = 8) -> Any:
    request_headers = {"Accept": "application/json", "User-Agent": "ResearchForge-Agent-Harness"}
    request_headers.update(headers or {})
    body = urlencode(data).encode("utf-8") if data is not None else None
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        response = urlopen(request, timeout=timeout)
        raw = response.read()
        closer = getattr(response, "close", None)
        if closer is not None:
            closer()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise GitHubOAuthError(f"GitHub 请求失败：{exc}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubOAuthError("GitHub 返回了无法解析的响应。") from exc


def _encode_json(value: dict[str, Any]) -> str:
    return _b64encode(json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
