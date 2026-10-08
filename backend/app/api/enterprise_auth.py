from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, SecretStr

from app.config import get_settings
from app.infra.store import store
from app.services.authentication import CSRF_COOKIE, create_csrf_token, development_auth
from app.services.enterprise_identity import consume_login, ldap_identity, provision_identity
from app.services.github_oauth import create_session_cookie
from app.services.secrets import resolve_secret

router = APIRouter(prefix="/auth/enterprise", tags=["enterprise-auth"])


def _redirect_url():
    url = os.getenv("RESEARCHFORGE_SSO_FRONTEND_URL", "http://127.0.0.1:3010/")
    parsed = urlsplit(url)
    if f"{parsed.scheme}://{parsed.netloc}" not in get_settings().cors_origins:
        raise HTTPException(503, "SSO_FRONTEND_ORIGIN_NOT_ALLOWED")
    return url


def _login_response(user, redirect=True):
    from fastapi.responses import JSONResponse
    response = RedirectResponse(_redirect_url(), status_code=303) if redirect else JSONResponse({"status": "authenticated", "user_id": user.id})
    token = create_session_cookie(user.id)
    response.set_cookie("researchforge_user_id", token, httponly=True, secure=not development_auth(), samesite="lax", max_age=86400 * 30)
    response.set_cookie(CSRF_COOKIE, create_csrf_token(token), httponly=False, secure=not development_auth(), samesite="lax", max_age=86400 * 30)
    return response


def _oidc():
    from authlib.integrations.starlette_client import OAuth
    issuer = os.getenv("RESEARCHFORGE_OIDC_ISSUER", "").rstrip("/")
    if not issuer.startswith("https://") or not os.getenv("RESEARCHFORGE_OIDC_CLIENT_ID"):
        raise HTTPException(503, "OIDC_NOT_CONFIGURED")
    oauth = OAuth()
    return oauth.register("enterprise", client_id=os.environ["RESEARCHFORGE_OIDC_CLIENT_ID"],
                          client_secret=resolve_secret("RESEARCHFORGE_OIDC_CLIENT_SECRET"),
                          server_metadata_url=issuer + "/.well-known/openid-configuration",
                          client_kwargs={"scope": "openid profile email", "code_challenge_method": "S256", "timeout": 10})


@router.get("/status")
def status():
    return {"oidc": bool(os.getenv("RESEARCHFORGE_OIDC_ISSUER")), "ldap": bool(os.getenv("RESEARCHFORGE_LDAP_URL")), "saml": bool(os.getenv("RESEARCHFORGE_SAML_SETTINGS_FILE"))}


@router.get("/oidc/login")
async def oidc_login(request: Request):
    callback = os.getenv("RESEARCHFORGE_OIDC_REDIRECT_URI")
    if not callback or not callback.startswith("https://"):
        raise HTTPException(503, "OIDC_HTTPS_CALLBACK_REQUIRED")
    return await _oidc().authorize_redirect(request, callback)


@router.get("/oidc/callback")
async def oidc_callback(request: Request):
    try:
        token = await _oidc().authorize_access_token(request)
        claims = token.get("userinfo")
        issuer = os.environ["RESEARCHFORGE_OIDC_ISSUER"].rstrip("/")
        if not claims or claims.get("iss") != issuer or not claims.get("sub"):
            raise HTTPException(401, "OIDC_IDENTITY_INVALID")
        await asyncio.to_thread(consume_login, "oidc:" + request.query_params.get("state", ""))
        user = provision_identity(store, "oidc", issuer, claims["sub"], claims.get("email", "") if claims.get("email_verified") is True else "", claims.get("name", ""))
        return _login_response(user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, "OIDC_AUTHENTICATION_FAILED") from exc


class LDAPLogin(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=1024)


@router.post("/ldap/login")
async def ldap_login(payload: LDAPLogin):
    try:
        identity = await asyncio.to_thread(ldap_identity, payload.username, payload.password.get_secret_value())
        return _login_response(provision_identity(store, "ldap", **identity), redirect=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, "LDAP_AUTHENTICATION_FAILED") from exc


def _saml(request: Request, form=None):
    from onelogin.saml2.auth import OneLogin_Saml2_Auth
    path = os.getenv("RESEARCHFORGE_SAML_SETTINGS_FILE")
    if not path:
        raise HTTPException(503, "SAML_NOT_CONFIGURED")
    settings = json.loads(Path(path).read_text(encoding="utf-8"))
    settings["strict"] = True
    settings.setdefault("security", {}).update({"wantMessagesSigned": True, "wantAssertionsSigned": True, "rejectDeprecatedAlgorithm": True})
    public_url = urlsplit(settings["sp"]["assertionConsumerService"]["url"])
    if public_url.scheme != "https":
        raise HTTPException(503, "SAML_HTTPS_REQUIRED")
    return OneLogin_Saml2_Auth({"https": "on", "http_host": public_url.netloc, "script_name": public_url.path,
                                "get_data": dict(request.query_params), "post_data": form or {}}, settings)


@router.get("/saml/login")
async def saml_login(request: Request):
    auth = _saml(request)
    url = auth.login()
    request.session["saml_request_id"] = auth.get_last_request_id()
    return RedirectResponse(url)


@router.post("/saml/callback")
async def saml_callback(request: Request):
    request_id = request.session.pop("saml_request_id", None)
    if not request_id:
        raise HTTPException(401, "SAML_REQUEST_REQUIRED")
    try:
        auth = _saml(request, dict(await request.form()))
        auth.process_response(request_id=request_id)
        if auth.get_errors() or not auth.is_authenticated():
            raise HTTPException(401, "SAML_AUTHENTICATION_FAILED")
        assertion_id = auth.get_last_assertion_id()
        if not assertion_id:
            raise HTTPException(401, "SAML_ASSERTION_ID_REQUIRED")
        await asyncio.to_thread(consume_login, "saml:" + assertion_id, 86400)
        issuer = auth.get_settings().get_idp_data()["entityId"]
        return _login_response(provision_identity(store, "saml", issuer, auth.get_nameid(), "", auth.get_nameid()))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, "SAML_AUTHENTICATION_FAILED") from exc
