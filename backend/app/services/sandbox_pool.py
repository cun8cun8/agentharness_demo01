"""Capacity, image warming, and stale-resource cleanup for isolated sandboxes."""
from __future__ import annotations

import subprocess
import threading
import time
import asyncio
from contextlib import contextmanager

from app.config import get_settings


class SandboxCapacityExceeded(RuntimeError):
    pass


class SandboxPool:
    def __init__(self):
        self._condition = threading.Condition()
        self._active = 0
        self._last_reconcile = None
        self._last_error = None

    def _limit(self):
        return get_settings().sandbox_max_concurrent

    @contextmanager
    def lease(self, backend):
        # Local developer commands deliberately do not consume production capacity.
        if backend == "local":
            yield
            return
        timeout = get_settings().sandbox_queue_timeout_seconds
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._active >= self._limit():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxCapacityExceeded("SANDBOX_CAPACITY_EXHAUSTED")
                self._condition.wait(remaining)
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active = max(0, self._active - 1)
                self._condition.notify()

    def reconcile(self):
        settings = get_settings()
        backend = settings.sandbox_backend.strip().lower()
        status = {"backend": backend, "warmed": False, "cleaned": False}
        if backend != "docker":
            self._last_reconcile = status
            return status
        try:
            inspect = subprocess.run(["docker", "image", "inspect", settings.sandbox_image], capture_output=True, text=True, timeout=10, check=False)
            if inspect.returncode != 0 and settings.sandbox_prewarm_pull:
                inspect = subprocess.run(["docker", "pull", settings.sandbox_image], capture_output=True, text=True, timeout=600, check=False)
            status["warmed"] = inspect.returncode == 0
            # Only remove stopped containers created by this service and labelled as ephemeral.
            cleanup = subprocess.run(["docker", "container", "prune", "--force", "--filter", "label=researchforge.sandbox.ephemeral=true"], capture_output=True, text=True, timeout=30, check=False)
            status["cleaned"] = cleanup.returncode == 0
            status["message"] = "镜像已就绪" if status["warmed"] else (inspect.stderr.strip() or "沙箱镜像未就绪")
            self._last_error = None if status["warmed"] else status["message"]
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._last_error = type(exc).__name__
            status["message"] = self._last_error
        self._last_reconcile = status
        return status

    def status(self):
        with self._condition:
            return {"capacity": self._limit(), "active": self._active, "available": max(0, self._limit() - self._active),
                    "last_reconcile": self._last_reconcile, "last_error": self._last_error,
                    "mode": "ephemeral-workspace-per-lease"}


sandbox_pool = SandboxPool()


class SandboxPoolController:
    def __init__(self, pool=sandbox_pool):
        self.pool = pool
        self.task = None
        self.stop_event = None

    async def _run(self):
        while not self.stop_event.is_set():
            await asyncio.to_thread(self.pool.reconcile)
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=get_settings().sandbox_pool_reconcile_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self):
        if self.task is None:
            self.stop_event = asyncio.Event()
            self.task = asyncio.create_task(self._run(), name="sandbox-pool-controller")

    async def stop(self):
        if self.task is not None:
            self.stop_event.set()
            await self.task
            self.task = None


sandbox_pool_controller = SandboxPoolController()
