import asyncio
import re
import secrets

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from app.domain.schemas import (
    AuthSessionResponse,
    CreateUserRequest,
    CreateWorkspaceRequest,
    UpdateUserRequest,
    UpdateWorkspaceRequest,
    UserResponse,
    WorkspaceResponse,
)
from app.config import get_settings
from app.api.context import (
    require_workspace_access,
    require_workspace_object,
    workspace_id_for_request,
)
from app.infra.store import store
from app.services.authentication import (
    CSRF_COOKIE,
    authenticate,
    configured_api_key,
    create_csrf_token,
    development_auth,
    session_token,
    token_digest,
)
from app.services.memberships import WORKSPACE_COOKIE, selected_workspace
from app.services.github_oauth import (
    GitHubOAuthError,
    build_authorization_url,
    create_session_cookie,
    exchange_code,
    fetch_identity,
    oauth_enabled,
    verify_session_cookie,
    verify_state,
)

router = APIRouter(tags=["auth"])
GITHUB_USER_COOKIE = "researchforge_user_id"
GITHUB_STATE_COOKIE = "researchforge_oauth_state"


def _value_error_status(message: str) -> int:
    return 409 if "already exists" in message else 400


def _cookie_secure() -> bool:
    return not development_auth()


def _cookie_user_id(request: Request) -> str | None:
    raw = request.cookies.get(GITHUB_USER_COOKIE)
    if not raw:
        return None
    try:
        return verify_session_cookie(raw, get_settings())
    except GitHubOAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

def _github_identity_key(external_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]", "_", external_id).strip("_")
    return value or "unknown"


def _ensure_github_user(identity: dict[str, str]) -> UserResponse:
    key = _github_identity_key(identity["external_id"])
    user_id = f"user_github_{key}"
    workspace_id = f"workspace_github_{key}"
    workspace = store.get_workspace(workspace_id)
    if workspace is None:
        workspace = store.create_workspace(
            CreateWorkspaceRequest(
                id=workspace_id,
                name=f"GitHub / {identity['login']}",
                owner_id=user_id,
            )
        )
    if workspace.status != "active":
        raise GitHubOAuthError("该 GitHub 工作区已停用。")
    user = store.get_user(user_id)
    if user is None:
        return store.create_user(
            CreateUserRequest(
                id=user_id,
                workspace_id=workspace_id,
                email=identity["email"],
                name=identity["name"],
                role="operator",
            )
        )
    if user.status != "active":
        raise GitHubOAuthError("该 GitHub 用户已停用。")
    changes: dict[str, str] = {}
    if identity.get("email") and user.email != identity["email"]:
        changes["email"] = identity["email"]
    if identity.get("name") and user.name != identity["name"]:
        changes["name"] = identity["name"]
    if changes:
        updated = store.update_user(user.id, UpdateUserRequest(**changes))
        if updated is not None:
            user = updated
    return user


@router.get("/auth/session", response_model=AuthSessionResponse)
async def auth_session(
    request: Request,
    response: Response,
    x_user_id: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> AuthSessionResponse:
    settings = get_settings()
    effective_user_id = authenticate(request, store)
    active_token = session_token(request)
    if effective_user_id:
        user = store.get_user(effective_user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="用户不存在。")
        if user.status != "active":
            raise HTTPException(status_code=403, detail="用户已停用。")
    api_key = configured_api_key(settings)
    if api_key and x_api_key and secrets.compare_digest(x_api_key, api_key):
        active_token = create_session_cookie(effective_user_id, settings)
        response.set_cookie(
            GITHUB_USER_COOKIE,
            active_token,
            max_age=7 * 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=_cookie_secure(),
        )
    response.set_cookie(
        CSRF_COOKIE,
        create_csrf_token(active_token),
        max_age=30 * 24 * 60 * 60,
        httponly=False,
        samesite="lax",
        secure=_cookie_secure(),
    )
    try:
        selected = selected_workspace(request, store, store.get_user(effective_user_id))
        return store.auth_session(effective_user_id, selected)
    except ValueError:
        available = store.accessible_workspaces(effective_user_id)
        if not available:
            raise HTTPException(403, "WORKSPACE_ACCESS_DENIED")
        response.delete_cookie(WORKSPACE_COOKIE)
        return store.auth_session(effective_user_id, available[0].id)


@router.get("/auth/config")
def auth_config():
    return {
        "development_auth": development_auth(),
        "github_enabled": oauth_enabled(),
        "api_key_enabled": bool(configured_api_key()),
    }


@router.get("/auth/github/status")
async def github_oauth_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "provider": "github",
        "enabled": oauth_enabled(settings),
        "redirect_uri": settings.github_oauth_redirect_uri,
        "scopes": settings.github_oauth_scopes.split(),
    }


@router.get("/auth/github/login")
async def github_login(response: Response) -> dict[str, object]:
    settings = get_settings()
    try:
        authorization_url, state = build_authorization_url(settings)
    except GitHubOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.set_cookie(
        GITHUB_STATE_COOKIE,
        state,
        max_age=max(60, settings.github_oauth_state_ttl_seconds),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return {
        "provider": "github",
        "enabled": True,
        "authorization_url": authorization_url,
        "state": state,
        "redirect_uri": settings.github_oauth_redirect_uri,
    }


@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    response: Response,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> dict[str, object]:
    if error:
        detail = error_description or error
        raise HTTPException(status_code=400, detail=f"GitHub 登录被取消：{detail}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="GitHub 回调缺少 code 或 state。")
    settings = get_settings()
    try:
        expected_state = request.cookies.get(GITHUB_STATE_COOKIE)
        if not expected_state or not secrets.compare_digest(expected_state, state):
            raise GitHubOAuthError("OAuth 状态 Cookie 校验失败，请重新登录。")
        verify_state(state, settings)
        access_token = await asyncio.to_thread(exchange_code, code, settings)
        identity = await asyncio.to_thread(fetch_identity, access_token, settings)
        user = _ensure_github_user(identity)
    except GitHubOAuthError as exc:
        status = 400 if "状态" in str(exc) or "回调" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    response.delete_cookie(GITHUB_STATE_COOKIE)
    response.set_cookie(
        GITHUB_USER_COOKIE,
        create_session_cookie(user.id, settings),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    session = store.auth_session(user.id)
    return {
        "message": "GitHub 登录成功。",
        "provider": "github",
        "user": session.user,
        "workspace": session.workspace,
        "permissions": session.permissions,
        "session": session,
    }


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    token = session_token(request)
    if token:
        try:
            user_id = verify_session_cookie(token)
            store.revoked_sessions[token_digest(token)] = {"user_id": user_id}
            store.add_audit_log(action="auth.logout", resource_type="user", resource_id=user_id, decision="revoked")
            store._persist()
        except GitHubOAuthError:
            pass
    response.delete_cookie(GITHUB_USER_COOKIE)
    response.delete_cookie(GITHUB_STATE_COOKIE)
    response.delete_cookie("researchforge_api_key")
    response.delete_cookie(WORKSPACE_COOKIE)
    return {"status": "logged_out"}


@router.get("/workspaces")
async def list_workspaces(
    request: Request,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    items = store.accessible_workspaces(request.state.user.id)
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/workspaces", response_model=WorkspaceResponse)
async def create_workspace(request: CreateWorkspaceRequest) -> WorkspaceResponse:
    try:
        return store.create_workspace(request)
    except ValueError as exc:
        raise HTTPException(status_code=_value_error_status(str(exc)), detail=str(exc)) from exc


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(request: Request, workspace_id: str) -> WorkspaceResponse:
    return require_workspace_object(request, store.get_workspace(workspace_id))


@router.get("/workspaces/{workspace_id}/usage")
async def get_workspace_usage(request: Request, workspace_id: str) -> dict[str, object]:
    require_workspace_access(request, workspace_id)
    try:
        return store.workspace_usage(workspace_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    request_context: Request,
    workspace_id: str,
    request: UpdateWorkspaceRequest,
) -> WorkspaceResponse:
    require_workspace_access(request_context, workspace_id)
    item = store.update_workspace(workspace_id, request)
    if item is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return item


@router.get("/users")
def list_users(
    request: Request,
    workspace_id: str | None = None,
    query: str | None = Query(default=None, max_length=200),
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    workspace_id = workspace_id_for_request(request, workspace_id)
    query_page = getattr(store, "query_users_page", None)
    if callable(query_page):
        return query_page(
            workspace_id=workspace_id,
            status=status,
            query=query,
            limit=limit,
            offset=offset,
        )
    # The management table is paginated from the first page. Keep the newest
    # accounts visible after creation instead of hiding them behind old records.
    items = sorted(store.list_users(workspace_id=workspace_id), key=lambda item: item.created_at, reverse=True)
    if status:
        items = [item for item in items if item.status == status]
    needle = (query or "").strip().lower()
    if needle:
        items = [
            item
            for item in items
            if needle in " ".join(
                str(value or "")
                for value in (item.id, item.name, item.email, item.role, item.workspace_id, item.status)
            ).lower()
        ]
    return {"items": items[offset : offset + limit], "total": len(items)}


@router.post("/users", response_model=UserResponse)
async def create_user(request: CreateUserRequest) -> UserResponse:
    try:
        return store.create_user(request)
    except ValueError as exc:
        raise HTTPException(status_code=_value_error_status(str(exc)), detail=str(exc)) from exc


@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(request: Request, user_id: str) -> UserResponse:
    item = store.get_user(user_id)
    if item is None:
        raise HTTPException(status_code=404, detail="User not found")
    require_workspace_access(request, item.workspace_id)
    return item


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: str, request: UpdateUserRequest) -> UserResponse:
    try:
        item = store.update_user(user_id, request)
    except ValueError as exc:
        raise HTTPException(status_code=_value_error_status(str(exc)), detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="User not found")
    return item
