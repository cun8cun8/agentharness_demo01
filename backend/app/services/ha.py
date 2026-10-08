"""Small, dependency-aware availability snapshot used by probes and operators."""
from __future__ import annotations

import os
import socket
import time
from datetime import datetime, timezone

from app.infra.store import store
from app.services.job_queue import job_queue
from app.services.sandbox_pool import sandbox_pool

_started_at = datetime.now(timezone.utc).isoformat()
_started_monotonic = time.monotonic()
_instance_id = os.getenv("RESEARCHFORGE_INSTANCE_ID") or f"{socket.gethostname()}-{os.getpid()}"


async def snapshot() -> dict[str, object]:
    queue = await job_queue.snapshot()
    return {
        "instance_id": _instance_id,
        "started_at": _started_at,
        "uptime_seconds": max(0, int(time.monotonic() - _started_monotonic)),
        "storage": store.describe_storage(),
        "queue": queue,
        "sandbox_pool": sandbox_pool.status(),
        "ready": queue["backend"] != "redis" or bool(queue["connected"]),
    }
