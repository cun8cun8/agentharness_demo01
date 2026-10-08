from __future__ import annotations

import hashlib
import os
import ssl
import time
from threading import Lock
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.config import get_settings
from app.domain.schemas import CreateUserRequest
from app.services.authentication import development_auth
from app.services.rate_limit import redis_limiter
from app.services.secrets import resolve_secret

_used: dict[str, float] = {}
_lock = Lock()


def consume_login(identifier: str, ttl: int = 600) -> None:
    key = "researchforge:sso:" + hashlib.sha256(identifier.encode()).hexdigest()
    if not development_auth() or get_settings().rate_limit_backend == "redis":
        try:
            fresh = redis_limiter(get_settings().redis_url).client.set(key, "1", nx=True, ex=ttl)
        except Exception as exc:
            raise HTTPException(503, "SSO_REPLAY_STORE_UNAVAILABLE") from exc
    else:
        with _lock:
            now = time.monotonic()
            for old in [name for name, expiry in _used.items() if expiry < now]:
                del _used[old]
            fresh = key not in _used
            _used[key] = now + ttl
    if not fresh:
        raise HTTPException(401, "SSO_RESPONSE_REPLAYED")


def provision_identity(store, provider: str, issuer: str, subject: str, email: str, name: str):
    if not subject or not issuer:
        raise HTTPException(401, "SSO_SUBJECT_REQUIRED")
    key = hashlib.sha256(f"{provider}\0{issuer}\0{subject}".encode()).hexdigest()[:32]
    user_id = f"user_{provider}_{key}"
    workspace_id = os.getenv("RESEARCHFORGE_SSO_WORKSPACE_ID", "workspace_default")
    workspace = store.get_workspace(workspace_id)
    if not workspace or workspace.status != "active":
        raise HTTPException(403, "SSO_WORKSPACE_UNAVAILABLE")
    user = store.get_user(user_id)
    if user is None:
        if os.getenv("RESEARCHFORGE_SSO_AUTO_PROVISION", "0") != "1":
            raise HTTPException(403, "SSO_USER_NOT_PROVISIONED")
        user = store.create_user(CreateUserRequest(id=user_id, workspace_id=workspace_id,
                                                   email=email or f"{key}@identity.invalid", name=name or subject, role="viewer"))
    if user.status != "active" or user.workspace_id != workspace_id:
        raise HTTPException(403, "SSO_USER_DISABLED")
    store.add_audit_log(action="auth.sso.login", resource_type="user", resource_id=user.id, actor_id=user.id, decision="allow", detail_json={"provider": provider})
    return user


def ldap_identity(username: str, password: str) -> dict:
    from ldap3 import AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND, Connection, Server, Tls
    from ldap3.utils.conv import escape_filter_chars
    url = urlsplit(os.getenv("RESEARCHFORGE_LDAP_URL", ""))
    if url.scheme not in {"ldap", "ldaps"} or not url.hostname or not username.strip() or not password:
        raise HTTPException(401, "LDAP_AUTHENTICATION_FAILED")
    tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=os.getenv("RESEARCHFORGE_LDAP_CA_FILE"))
    server = Server(url.hostname, port=url.port or (636 if url.scheme == "ldaps" else 389), use_ssl=url.scheme == "ldaps", tls=tls, connect_timeout=5)
    auto_bind = AUTO_BIND_NO_TLS if url.scheme == "ldaps" else AUTO_BIND_TLS_BEFORE_BIND
    bind_dn = os.getenv("RESEARCHFORGE_LDAP_BIND_DN")
    bind_password = resolve_secret("RESEARCHFORGE_LDAP_BIND_PASSWORD")
    if not bind_dn or not bind_password:
        raise HTTPException(503, "LDAP_SERVICE_ACCOUNT_REQUIRED")
    search = Connection(server, user=bind_dn, password=bind_password, auto_bind=auto_bind, receive_timeout=5, raise_exceptions=True)
    try:
        attribute = os.getenv("RESEARCHFORGE_LDAP_LOGIN_ATTRIBUTE", "uid")
        if attribute not in {"uid", "sAMAccountName", "userPrincipalName"}:
            raise HTTPException(503, "LDAP_ATTRIBUTE_NOT_ALLOWED")
        search.search(os.environ["RESEARCHFORGE_LDAP_BASE_DN"], f"({attribute}={escape_filter_chars(username)})", attributes=["mail", "displayName"], size_limit=2)
        if len(search.entries) != 1:
            raise HTTPException(401, "LDAP_AUTHENTICATION_FAILED")
        entry = search.entries[0]
        bound = Connection(server, user=entry.entry_dn, password=password, auto_bind=auto_bind, receive_timeout=5, raise_exceptions=True)
        bound.unbind()
        attributes = entry.entry_attributes_as_dict
        return {"issuer": url.geturl(), "subject": entry.entry_dn, "email": (attributes.get("mail") or [""])[0],
                "name": (attributes.get("displayName") or [username])[0]}
    finally:
        search.unbind()
