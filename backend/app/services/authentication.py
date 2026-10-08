from __future__ import annotations

import hashlib
import hmac
import secrets
from fastapi import HTTPException, Request

from app.config import get_settings
from app.services.github_oauth import GitHubOAuthError, verify_session_cookie, _state_secret
from app.services.secrets import resolve_secret

CSRF_COOKIE = "researchforge_csrf"


def development_auth() -> bool:
    settings = get_settings()
    return settings.auth_mode == "development" or (
        settings.auth_mode == "auto" and settings.environment in {"local", "test", "container"}
    )


def session_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return request.cookies.get("researchforge_user_id")


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_bearer_session(request: Request) -> bool:
    return request.headers.get("authorization", "").startswith("Bearer ")


def configured_api_key(settings=None) -> str | None:
    current = settings or get_settings()
    if not current.api_key_env:
        return current.api_key
    try:
        return resolve_secret(current.api_key_env) or None
    except Exception:
        return None


def metrics_token_valid(request: Request) -> bool:
    """Authenticate the Prometheus scrape without creating an application session."""
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return False
    try:
        expected = resolve_secret(get_settings().metrics_token_env)
    except Exception:
        return False
    supplied = authorization[7:]
    return bool(expected and supplied and secrets.compare_digest(supplied, expected))


def csrf_valid(request: Request) -> bool:
    if is_bearer_session(request) or request.headers.get("x-api-key"):
        return True
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get("x-csrf-token")
    token = session_token(request)
    return bool(cookie and header and token and secrets.compare_digest(cookie, header)
                and secrets.compare_digest(cookie, create_csrf_token(token)))


def create_csrf_token(token: str | None) -> str:
    secret = _state_secret(get_settings())
    if not token or not secret:
        return secrets.token_urlsafe(32)
    return hmac.new(secret.encode(), ("csrf:" + token).encode(), hashlib.sha256).hexdigest()


def authenticate(request: Request, store) -> str:
    settings = get_settings()
    api_key = configured_api_key(settings)
    token = session_token(request)
    header_user = request.headers.get("x-user-id")
    explicit_key = request.headers.get("x-api-key")
    if explicit_key:
        if not api_key or not secrets.compare_digest(explicit_key, api_key):
            raise HTTPException(401, "UNAUTHENTICATED")
        if not development_auth() and header_user and header_user != "user_admin":
            raise HTTPException(401, "SESSION_USER_MISMATCH")
        return header_user or "user_admin" if development_auth() else "user_admin"
    if token:
        try:
            user_id = verify_session_cookie(token, settings)
        except (GitHubOAuthError, UnicodeError, ValueError) as exc:
            raise HTTPException(401, "SESSION_INVALID") from exc
        if token_digest(token) in store.revoked_sessions:
            raise HTTPException(401, "SESSION_REVOKED")
        if header_user and header_user != user_id:
            raise HTTPException(401, "SESSION_USER_MISMATCH")
        return user_id
    provided = request.headers.get("x-api-key") or request.cookies.get("researchforge_api_key") or ""
    key_valid = bool(api_key and secrets.compare_digest(provided, api_key))
    if not development_auth():
        if not key_valid:
            raise HTTPException(401, "AUTHENTICATION_REQUIRED")
        if header_user and header_user != "user_admin":
            raise HTTPException(401, "SESSION_USER_MISMATCH")
        return "user_admin"
    if api_key and not key_valid:
        raise HTTPException(401, "UNAUTHENTICATED")
    return header_user or "user_admin"
