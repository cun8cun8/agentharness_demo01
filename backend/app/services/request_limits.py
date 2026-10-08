from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse

from app.config import get_settings
from app.services.rate_limit import redis_limiter, request_limiter


class RequestLimitsMiddleware:
    """Bound actual body bytes before application parsing, including chunked bodies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1") or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        settings = get_settings()
        headers = {key.lower(): value for key, value in scope["headers"]}

        async def reject(status, code, retry=None):
            response = JSONResponse({"detail": code, "error": {"code": code, "message": code}},
                                    status_code=status, headers={"Retry-After": str(retry)} if retry else None)
            await response(scope, receive, send)

        # Never use attacker-controlled credentials or forwarded headers as the key.
        peer = (scope.get("client") or ("unknown",))[0]
        bucket = "auth" if scope["path"].startswith("/api/v1/auth/") else "api"
        limit = settings.auth_rate_limit_per_minute if bucket == "auth" else settings.rate_limit_per_minute
        try:
            limiter = redis_limiter(settings.redis_url) if settings.rate_limit_backend == "redis" else request_limiter
            allowed, retry = await asyncio.to_thread(limiter.allow, f"{bucket}:{peer}", limit)
        except Exception:
            return await reject(503, "RATE_LIMIT_BACKEND_UNAVAILABLE")
        if not allowed:
            return await reject(429, "RATE_LIMIT_EXCEEDED", retry)
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await reject(400, "INVALID_CONTENT_LENGTH")
        if length < 0:
            return await reject(400, "INVALID_CONTENT_LENGTH")
        if length > settings.max_request_body_bytes:
            return await reject(413, "REQUEST_BODY_TOO_LARGE")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > settings.max_request_body_bytes:
                return await reject(413, "REQUEST_BODY_TOO_LARGE")
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        consumed = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
