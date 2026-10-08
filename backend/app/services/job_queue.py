from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

from fastapi import BackgroundTasks
from fastapi.encoders import jsonable_encoder

from app.config import Settings, get_settings
from app.services.redis_delivery import RedisDelivery

try:  # pragma: no cover - optional dependency
    from redis import asyncio as redis_asyncio
except Exception:  # pragma: no cover - optional dependency
    redis_asyncio = None


logger = logging.getLogger(__name__)
JobHandler = Callable[..., Awaitable[Any]]


def _redact_queue_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.scheme:
        return "***configured***"
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return f"{parts.scheme}://{netloc}{parts.path}" if parts.path else f"{parts.scheme}://{netloc}"


class JobQueueManager:
    """Job queue adapter with local and Redis-backed execution modes."""

    def __init__(
        self,
        settings: Settings | None = None,
        redis_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.backend = self.settings.job_queue_backend
        self.queue_name = self.settings.job_queue_name
        self.poll_seconds = max(0.1, float(self.settings.job_worker_poll_seconds))
        self.worker_enabled = bool(self.settings.job_worker_enabled)
        self._states: Counter[str] = Counter()
        self._handlers: dict[str, JobHandler] = {}
        self._redis_client_factory = redis_client_factory or self._default_redis_client_factory
        self._redis: Any | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._worker_lock = asyncio.Lock()
        self._before_handler: Callable[[], Any] | None = None
        self._connected = self.backend != "redis"
        self._last_error: str | None = None
        self._state_key = f"{self.queue_name}:state"
        self._delivery: RedisDelivery | None = None

    def _default_redis_client_factory(self, url: str) -> Any:
        if redis_asyncio is None:
            raise RuntimeError("Redis job queue backend requires the 'redis' package")
        return redis_asyncio.from_url(url, decode_responses=True)

    def register_handler(self, handler: JobHandler, key: str | None = None) -> str:
        handler_key = key or self._handler_key(handler)
        self._handlers[handler_key] = handler
        return handler_key

    def set_before_handler(self, callback: Callable[[], Any] | None) -> None:
        """Set a callback used by standalone workers before each job."""
        self._before_handler = callback

    async def start(self, *, start_worker: bool | None = None) -> None:
        if self.backend != "redis":
            self._connected = True
            return
        should_start_worker = self.worker_enabled if start_worker is None else start_worker
        async with self._worker_lock:
            if should_start_worker and self._worker_task is not None and not self._worker_task.done():
                return
            if self._redis is None:
                self._redis = self._redis_client_factory(self.settings.redis_url)
            try:
                await self._redis.ping()
            except Exception as exc:  # pragma: no cover - exercised via unit test double
                self._connected = False
                self._last_error = str(exc)
                raise RuntimeError(
                    f"Unable to connect Redis job queue at {_redact_queue_url(self.settings.redis_url)}"
                ) from exc
            await self._ensure_state_record()
            if self.settings.job_delivery_mode == "stream" and self._delivery is None and hasattr(self._redis, "xgroup_create"):
                self._delivery = RedisDelivery(self._redis, self.queue_name, self.settings.job_visibility_timeout_seconds, self.settings.job_max_retries, self.settings.job_workspace_concurrency)
                await self._delivery.start()
            self._connected = True
            self._last_error = None
            if not should_start_worker:
                return
            self._worker_task = asyncio.create_task(
                self._worker_loop(),
                name=f"job-queue:{self.queue_name}",
            )

    async def stop(self) -> None:
        if self.backend != "redis":
            return
        async with self._worker_lock:
            task = self._worker_task
            self._worker_task = None
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if self._delivery is not None and self._redis is not None:
                with suppress(Exception):
                    await self._redis.delete(self._delivery.stream + ":workers:" + self._delivery.consumer)
            if self._redis is not None:
                closer = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
                if closer is not None:
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
            self._redis = None
            self._delivery = None
            self._connected = False

    async def enqueue(
        self,
        background_tasks: BackgroundTasks | None,
        handler: JobHandler,
        *args: Any,
    ) -> None:
        handler_key = self.register_handler(handler)
        if self.backend != "redis":
            self._states["queued"] += 1
            if background_tasks is not None:
                background_tasks.add_task(self._run_local, handler, *args)
            else:
                asyncio.create_task(self._run_local(handler, *args))
            return

        await self.start(start_worker=False)
        from app.infra.store import store
        job_id = next((arg for arg in args if isinstance(arg, str) and arg.startswith("job_")), None)
        job = store.read_job(job_id) if job_id else None
        workspace_id = job.metadata.get("workspace_id") if job else None
        if not workspace_id and job and job.task_id:
            task = store.get_task(job.task_id)
            workspace_id = task.workspace_id if task else None
        if not workspace_id and job and job.kind.startswith("repository"):
            repository = store.get_repository_connection(job.resource_id)
            workspace_id = repository.workspace_id if repository else None
        if not workspace_id and job:
            request_data = job.metadata.get("request")
            if isinstance(request_data, dict):
                workspace_id = request_data.get("workspace_id")
        workspace_id = workspace_id or "workspace_default"
        payload = json.dumps(
            {
                "handler_key": handler_key,
                "args": jsonable_encoder(args),
                "workspace_id": workspace_id,
                "enqueued_at": time.time(),
            },
            ensure_ascii=False,
        )
        try:
            assert self._redis is not None
            await self._change_state(queued=1)
            if self._delivery is not None:
                await self._delivery.enqueue(payload)
            else:
                await self._redis.rpush(self.queue_name, payload)
        except Exception as exc:
            with suppress(Exception):
                await self._change_state(queued=-1)
            self._last_error = str(exc)
            raise

    async def _run_local(self, handler: JobHandler, *args: Any) -> None:
        self._states["queued"] = max(0, self._states["queued"] - 1)
        self._states["running"] += 1
        try:
            await self._invoke_handler(handler, *args)
            self._states["completed"] += 1
        except Exception as exc:
            self._states["failed"] += 1
            self._last_error = str(exc)
            raise
        finally:
            self._states["running"] = max(0, self._states["running"] - 1)

    async def _worker_loop(self) -> None:
        assert self._redis is not None
        if self._delivery is not None:
            await self._reliable_worker_loop()
            return
        while True:
            try:
                item = await self._redis.blpop(self.queue_name, timeout=self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._last_error = str(exc)
                logger.warning("Redis job worker error: %s", exc)
                await asyncio.sleep(self.poll_seconds)
                continue
            if item is None:
                continue
            _, raw_payload = item
            await self._change_state(queued=-1, running=1)
            try:
                if self._before_handler is not None:
                    await self._invoke_handler(self._before_handler)
                payload = json.loads(raw_payload)
                handler_key = str(payload["handler_key"])
                handler = self._handlers.get(handler_key)
                if handler is None:
                    raise RuntimeError(f"Unknown job handler: {handler_key}")
                args = payload.get("args", [])
                if not isinstance(args, list):
                    args = [args]
                await self._invoke_handler(handler, *args)
                await self._change_state(running=-1, completed=1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._change_state(running=-1, failed=1)
                self._last_error = str(exc)
                logger.exception("Redis job handler failed")

    async def _invoke_handler(self, handler: JobHandler, *args: Any) -> Any:
        result = handler(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _reliable_worker_loop(self):
        async def dispatch(raw):
            if self._before_handler is not None:
                await self._invoke_handler(self._before_handler)
            payload = json.loads(raw)
            handler = self._handlers.get(payload["handler_key"])
            if handler is None:
                raise RuntimeError("JOB_HANDLER_UNKNOWN")
            from app.infra.records import StoreConflictError
            from app.infra.store import store
            enqueued_at = payload.get("enqueued_at")
            if isinstance(enqueued_at, (int, float)) and time.time() - enqueued_at > self.settings.job_queue_wait_timeout_seconds:
                job_id = next((arg for arg in payload.get("args", []) if isinstance(arg, str) and arg.startswith("job_")), None)
                job = store.read_job(job_id) if job_id else None
                if job and job.status == "queued":
                    if job.kind in {"agent_run", "agent_run_resume"}:
                        from app.api.runs import runtime
                        from app.domain.schemas import RunPhase
                        await runtime._fail_run(job.resource_id, RunPhase.CREATED, "QUEUE_WAIT_TIMEOUT", "任务等待时间超过队列上限。")
                    store.update_job(job.id, "failed", "QUEUE_WAIT_TIMEOUT")
                    return
            for attempt in range(3):
                try:
                    await self._invoke_handler(handler, *payload.get("args", []))
                    break
                except StoreConflictError:
                    if attempt == 2:
                        raise
                    # Retry only repository jobs that have not created a run.
                    # A partially executed handler must use its durable recovery path.
                    if payload["handler_key"] != "app.api.integrations:_execute_repository_repair_job":
                        raise
                    from app.infra.store import store
                    job = store.read_job(payload["args"][1])
                    if job is None or job.status != "queued" or job.metadata.get("agent_run_id"):
                        raise
                    if self._before_handler is not None:
                        await self._invoke_handler(self._before_handler)
                    await asyncio.sleep(self.poll_seconds)
        while True:
            try:
                await self._redis.set(self._delivery.stream + ":workers:" + self._delivery.consumer, str(time.time()), ex=self.settings.job_visibility_timeout_seconds)
                message = await self._delivery.take()
                if message is not None:
                    success = await self._delivery.handle(message, dispatch)
                    if success is not None:
                        await self._change_state(completed=int(success), failed=int(not success))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = type(exc).__name__
                await asyncio.sleep(self.poll_seconds)

    async def _ensure_state_record(self) -> None:
        if self.backend != "redis" or self._redis is None:
            return
        current = await self._redis.hgetall(self._state_key)
        if current:
            return
        await self._redis.hset(
            self._state_key,
            mapping={
                "queued": 0,
                "running": 0,
                "completed": 0,
                "failed": 0,
            },
        )

    async def _change_state(
        self,
        *,
        queued: int = 0,
        running: int = 0,
        completed: int = 0,
        failed: int = 0,
    ) -> None:
        self._states["queued"] = max(0, self._states["queued"] + queued)
        self._states["running"] = max(0, self._states["running"] + running)
        self._states["completed"] = max(0, self._states["completed"] + completed)
        self._states["failed"] = max(0, self._states["failed"] + failed)
        if self.backend != "redis" or self._redis is None:
            return
        updates = {
            "queued": queued,
            "running": running,
            "completed": completed,
            "failed": failed,
        }
        for field, delta in updates.items():
            if delta:
                await self._redis.hincrby(self._state_key, field, delta)

    def _handler_key(self, handler: JobHandler) -> str:
        return f"{handler.__module__}:{handler.__qualname__}"

    async def snapshot(self) -> dict[str, Any]:
        worker_running = (
            self._worker_task is not None and not self._worker_task.done()
            if self.backend == "redis"
            else bool(self._states["queued"] or self._states["running"])
        )
        queued = max(0, self._states["queued"])
        running = max(0, self._states["running"])
        completed = self._states["completed"]
        failed = self._states["failed"]
        if self.backend == "redis" and self._redis is not None:
            try:
                state = await self._redis.hgetall(self._state_key)
            except Exception as exc:
                self._last_error = str(exc)
                state = {}
            else:
                if state:
                    queued = max(0, int(state.get("queued", queued)))
                    running = max(0, int(state.get("running", running)))
                    completed = int(state.get("completed", completed))
                    failed = int(state.get("failed", failed))
        delivery_counts = await self._delivery.counts() if self._delivery is not None else {}
        active_workers = None
        if self._delivery is not None and hasattr(self._redis, "scan_iter"):
            active_workers = sum([1 async for _ in self._redis.scan_iter(match=self._delivery.stream + ":workers:*")])
        return {
            "backend": self.backend,
            "active_workers": active_workers,
            "delivery_mode": self.settings.job_delivery_mode,
            "dead_letters": delivery_counts.get("dead_letters", 0),
            "queue_name": self.queue_name,
            "workspace_concurrency": self.settings.job_workspace_concurrency,
            "queue_wait_timeout_seconds": self.settings.job_queue_wait_timeout_seconds,
            "connected": self._connected,
            "worker_enabled": self.worker_enabled,
            "worker_running": worker_running,
            "queued": delivery_counts.get("queued", queued),
            "running": delivery_counts.get("running", running),
            "completed": completed,
            "failed": failed,
            "last_error": self._last_error,
        }


job_queue = JobQueueManager()
