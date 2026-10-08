from contextlib import asynccontextmanager
import asyncio
import logging
import re
import secrets
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware

from app.api import (
    approvals,
    a2a,
    adapters,
    auth,
    audit,
    benchmarks,
    datasets,
    evaluations,
    events,
    extensions,
    health_demo,
    integrations,
    jobs,
    knowledge,
    memory,
    models,
    notebooks,
    policies,
    research,
    runs,
    sandbox,
    strategies,
    system,
    tasks,
    telemetry,
    tools,
)
from app.services.job_queue import job_queue
from app.services.event_bus import event_publisher
from app.services.github_oauth import GitHubOAuthError, verify_session_cookie
from app.config import get_settings
from app.infra.store import store
from app.services.authentication import (
    authenticate,
    csrf_valid,
    development_auth,
    is_bearer_session,
    metrics_token_valid,
)
from app.services.request_limits import RequestLimitsMiddleware
from app.infra.records import StoreConflictError
from app.services.model_gateway import ModelInvocationError
from app.services.observability import configure_observability
from app.services.request_metrics import request_metrics
from app.api import enterprise_auth, training, memberships, scim
from app.services.memberships import selected_workspace
from app.services.training_controller import TrainingController
from app.services.sandbox_pool import sandbox_pool_controller

training_controller = TrainingController(store)
logger = logging.getLogger("researchforge.api")


_PUBLIC_AUTH_PATHS = {
    "/api/v1/models/bridge/chat/completions",
    "/api/v1/auth/config",
    "/api/v1/auth/session",
    "/api/v1/auth/github/status",
    "/api/v1/auth/github/login",
    "/api/v1/auth/github/callback",
    "/api/v1/auth/logout",
    "/api/v1/auth/enterprise/status",
    "/api/v1/auth/enterprise/oidc/login",
    "/api/v1/auth/enterprise/oidc/callback",
    "/api/v1/auth/enterprise/ldap/login",
    "/api/v1/auth/enterprise/saml/login",
    "/api/v1/auth/enterprise/saml/callback",
    "/api/v1/integrations/github/webhook",
}

_STATUS_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def _uses_targeted_postgres_mutation(request: Request) -> bool:
    """Avoid a full snapshot reload for endpoints with record-level writes."""
    if not getattr(store, "supports_targeted_mutations", False):
        return False
    path = request.url.path.rstrip("/")
    if path == "/api/v1/tasks" and request.method == "POST":
        return True
    if re.fullmatch(r"/api/v1/tasks/[^/]+/runs", path) and request.method == "POST":
        return True
    if path == "/api/v1/users" and request.method == "POST":
        return True
    if re.fullmatch(r"/api/v1/users/[^/]+", path) and request.method == "PATCH":
        return True
    if path == "/api/v1/datasets/trace-items" and request.method == "POST":
        return True
    if path == "/api/v1/models" and request.method == "POST":
        return True
    if path == "/api/v1/datasets/preference-pairs" and request.method == "POST":
        return True
    if re.fullmatch(r"/api/v1/datasets/preference-pairs/[^/]+", path) and request.method == "PATCH":
        return True
    return bool(
        re.fullmatch(r"/api/v1/datasets/trace-items/[^/]+", path)
        and request.method == "PATCH"
    )


def _normalize_error_code(detail: Any, status_code: int) -> str:
    if isinstance(detail, str):
        value = detail.strip()
        if value and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value):
            return value
    return _STATUS_ERROR_CODES.get(status_code, "API_ERROR")


def _normalize_error_message(detail: Any, fallback: str) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for key in ("message", "error", "detail"):
            value = detail.get(key)
            if isinstance(value, str):
                return value
    return fallback


def _error_payload(
    request: Request,
    status_code: int,
    detail: Any,
    *,
    code: str | None = None,
) -> dict[str, object]:
    error_code = code or _normalize_error_code(detail, status_code)
    return {
        "detail": jsonable_encoder(detail),
        "error": {
            "code": error_code,
            "message": _normalize_error_message(detail, error_code),
            "details": {
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "request_id": getattr(request.state, "request_id", None),
            },
        },
    }


def _required_permission(path: str, method: str) -> str | None:
    if method in {"GET", "HEAD"}:
        return None
    normalized = path.rstrip("/")
    if re.fullmatch(r"/api/v1/workspaces/[^/]+/(members|invitations)(/[^/]+)?", normalized):
        return "members:manage"
    if path.startswith(("/api/v1/training", "/api/v1/model-registry", "/api/v1/secrets")):
        return "admin"
    if normalized.endswith("/policy-preview") and path.startswith("/api/v1/tools"):
        return None
    if path.startswith("/api/v1/workspaces") or path.startswith("/api/v1/users"):
        return "admin"
    if path.startswith("/api/v1/models"):
        return "admin"
    if path.startswith("/api/v1/strategies"):
        return "admin"
    if path.startswith("/api/v1/policies"):
        return "admin"
    if path.startswith("/api/v1/extensions"):
        return "admin"
    if path.startswith("/api/v1/evaluations"):
        return "admin"
    if path.startswith("/api/v1/benchmarks"):
        return "admin"
    if path.startswith("/api/v1/tasks"):
        return "tasks:write"
    if path.startswith("/api/v1/runs"):
        return "runs:write"
    if path.startswith("/api/v1/jobs"):
        return "runs:write"
    if path.startswith("/api/v1/approvals"):
        return "approvals:decide"
    if path.startswith("/api/v1/datasets"):
        return "datasets:review"
    if path.startswith("/api/v1/research"):
        return "research:write"
    if path.startswith("/api/v1/integrations"):
        return "integrations:write"
    if path.startswith("/api/v1/memory"):
        return "datasets:review"
    if path.startswith("/api/v1/knowledge"):
        return "datasets:review"
    if path.startswith("/api/v1/tools"):
        return "tasks:write"
    return None


def _rbac_response(request: Request) -> JSONResponse | None:
    if not request.url.path.startswith("/api/v1"):
        return None
    if request.url.path.startswith("/api/v1/scim/"):
        return None
    if request.method in {"OPTIONS"} or request.url.path in _PUBLIC_AUTH_PATHS:
        return None
    if (
        request.method == "GET"
        and request.url.path == "/api/v1/telemetry/metrics"
        and metrics_token_valid(request)
    ):
        return None
    try:
        user_id = authenticate(request, store)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content=_error_payload(request, exc.status_code, exc.detail))
    user = store.get_user(user_id)
    if user is None:
        return JSONResponse(
            status_code=401,
            content=_error_payload(
                request,
                401,
                "用户不存在。",
                code="USER_NOT_FOUND",
            ),
        )
    if user.status != "active":
        return JSONResponse(
            status_code=403,
            content=_error_payload(
                request,
                403,
                "用户已停用。",
                code="USER_DISABLED",
            ),
        )
    try:
        selected = selected_workspace(request, store, user)
    except ValueError as exc:
        # A revoked selection must not prevent switching to a remaining membership.
        if request.url.path in {"/api/v1/auth/workspace", "/api/v1/auth/invitations/accept"}:
            selected = user.workspace_id
        else:
            return JSONResponse(status_code=403, content=_error_payload(request, 403, str(exc)))
    request.state.user = user.model_copy(update={"workspace_id": selected})
    request.state.user_id = user.id
    request.state.workspace_id = selected
    request.state.is_admin = user.role == "admin"
    workspace = store.get_workspace(selected)
    if (workspace is None or workspace.status != "active") and not request.url.path.startswith("/api/v1/auth/"):
        return JSONResponse(status_code=403, content=_error_payload(request, 403, "WORKSPACE_DISABLED"))
    required = _required_permission(request.url.path, request.method)
    if required is None or user.role == "admin":
        return None
    session = store.auth_session(user.id, selected)
    if required not in session.permissions:
        return JSONResponse(
            status_code=403,
            content=_error_payload(
                request,
                403,
                f"当前角色缺少 {required} 权限。",
                code="FORBIDDEN",
            ),
        )
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await job_queue.start(start_worker=get_settings().job_worker_enabled)
    try:
        await event_publisher.start()
        await sandbox_pool_controller.start()
        await training_controller.start()
        yield
    finally:
        await sandbox_pool_controller.stop()
        await training_controller.stop()
        await event_publisher.stop()
        await job_queue.stop()
        if telemetry_provider:
            telemetry_provider.force_flush(timeout_millis=5000)


app = FastAPI(
    title="ResearchForge Agent Harness",
    version="0.1.0",
    description="P0 Coding Agent Harness API",
    lifespan=lifespan,
)
telemetry_provider = configure_observability(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    settings = get_settings()
    is_scim = request.url.path.startswith("/api/v1/scim/")
    if not is_scim and request.url.path.startswith("/api/v1") and request.method not in {"OPTIONS", "GET", "HEAD"}:
        if request.url.path != "/api/v1/auth/enterprise/saml/callback" and not development_auth() and not is_bearer_session(request) and not request.headers.get("x-api-key") and request.cookies.get("researchforge_user_id") and not csrf_valid(request):
            return JSONResponse(status_code=403, content=_error_payload(request, 403, "CSRF_TOKEN_REQUIRED"))
    # External workers persist through PostgreSQL/Redis. Refresh the in-process
    # snapshot before mutations, but never block ordinary GET page loads on a
    # full-store reload; list endpoints query the durable index directly.
    if (
        request.method not in {"GET", "HEAD", "OPTIONS"}
        and (settings.store_backend == "postgres" or (settings.job_queue_backend == "redis" and not settings.job_worker_enabled))
        and store.describe_storage().get("persistence_enabled")
        and not _uses_targeted_postgres_mutation(request)
    ):
        try:
            store.refresh()
        except Exception:
            if request.url.path.startswith("/api/v1"):
                return JSONResponse(status_code=503, content=_error_payload(request, 503, "STORAGE_UNAVAILABLE"))
    protected = request.url.path.startswith("/api/v1")
    exempt = request.method == "OPTIONS" or request.url.path in _PUBLIC_AUTH_PATHS
    if not is_scim and protected and request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if request.url.path != "/api/v1/auth/enterprise/saml/callback" and not development_auth() and origin and origin not in settings.cors_origins:
            return JSONResponse(status_code=403, content=_error_payload(request, 403, "ORIGIN_NOT_ALLOWED"))
    rbac_response = _rbac_response(request)
    if rbac_response is not None:
        return rbac_response
    response = await call_next(request)
    return response


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    started = time.perf_counter()
    incoming = request.headers.get("x-request-id", "").strip()
    request_id = incoming if re.fullmatch(r"[A-Za-z0-9._:-]{1,96}", incoming) else secrets.token_hex(12)
    request.state.request_id = request_id
    response = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        request_metrics.record(request.method, request.url.path, status_code, duration_ms)
        if response is not None:
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time-Ms"] = str(duration_ms)
            # API and probe responses may contain identities, traces or job
            # metadata. Never let a browser cache them or embed them in a page.
            if request.url.path.startswith(("/api/", "/health")):
                response.headers.setdefault("Cache-Control", "no-store")
                response.headers.setdefault("X-Frame-Options", "DENY")
                response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
                response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "no-referrer")
            if get_settings().environment in {"production", "prod"} and request.url.scheme == "https":
                response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


app.add_middleware(RequestLimitsMiddleware)

if os.getenv("RESEARCHFORGE_OIDC_ISSUER") or os.getenv("RESEARCHFORGE_SAML_SETTINGS_FILE"):
    from starlette.middleware.sessions import SessionMiddleware
    from app.services.secrets import resolve_secret
    sso_secret = resolve_secret("RESEARCHFORGE_SSO_SESSION_SECRET")
    if not sso_secret or len(sso_secret) < 32:
        raise RuntimeError("SSO_SESSION_SECRET_REQUIRES_32_CHARACTERS")
    app.add_middleware(SessionMiddleware, secret_key=sso_secret, session_cookie="researchforge_sso", max_age=600,
                       same_site="none", https_only=True)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(request, exc.status_code, exc.detail),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(StoreConflictError)
async def store_conflict_handler(request: Request, exc: StoreConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content=_error_payload(request, 409, "STORE_WRITE_CONFLICT"))


@app.exception_handler(ModelInvocationError)
async def model_error_handler(request: Request, exc: ModelInvocationError) -> JSONResponse:
    return JSONResponse(status_code=502, content=_error_payload(request, 502, str(exc)))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_payload(
            request,
            422,
            exc.errors(),
            code="VALIDATION_ERROR",
        ),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled API exception", extra={"method": request.method, "path": request.url.path, "request_id": getattr(request.state, "request_id", "")})
    return JSONResponse(
        status_code=500,
        content=_error_payload(request, 500, "INTERNAL_ERROR", code="INTERNAL_ERROR"),
    )

app.include_router(tasks.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(enterprise_auth.router, prefix="/api/v1")
app.include_router(training.router, prefix="/api/v1")
app.include_router(memberships.router, prefix="/api/v1")
app.include_router(runs.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(approvals.router, prefix="/api/v1")
app.include_router(a2a.router, prefix="/api/v1")
app.include_router(adapters.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(benchmarks.router, prefix="/api/v1")
app.include_router(evaluations.router, prefix="/api/v1")
app.include_router(events.router, prefix="/api/v1")
app.include_router(datasets.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(memory.router, prefix="/api/v1")
app.include_router(knowledge.router, prefix="/api/v1")
app.include_router(models.router, prefix="/api/v1")
app.include_router(extensions.router, prefix="/api/v1")
app.include_router(health_demo.router, prefix="/api/v1")
app.include_router(integrations.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(notebooks.router, prefix="/api/v1")
app.include_router(strategies.router, prefix="/api/v1")
app.include_router(policies.router, prefix="/api/v1")
app.include_router(sandbox.router, prefix="/api/v1")
app.include_router(system.router, prefix="/api/v1")
app.include_router(scim.router, prefix="/api/v1")
app.include_router(telemetry.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
async def liveness() -> dict[str, object]:
    from app.services.ha import snapshot
    state = await snapshot()
    return {"status": "ok", "instance_id": state["instance_id"]}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    from app.services.ha import snapshot
    state = await snapshot()
    if not state["ready"]:
        return JSONResponse(status_code=503, content={"status": "not_ready", **state})
    return JSONResponse(status_code=200, content={"status": "ready", **state})
