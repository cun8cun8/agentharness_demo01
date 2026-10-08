from __future__ import annotations

import asyncio
from collections import defaultdict

from app.config import Settings
from app.services.job_queue import JobQueueManager


class FakeRedis:
    def __init__(self) -> None:
        self.queues: dict[str, list[str]] = defaultdict(list)
        self.hashes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def rpush(self, key: str, value: str) -> int:
        self.queues[key].append(value)
        return len(self.queues[key])

    async def blpop(self, key: str, timeout: float = 0.0):  # noqa: ARG002
        if self.queues[key]:
            return key, self.queues[key].pop(0)
        await asyncio.sleep(0)
        return None

    async def hgetall(self, key: str) -> dict[str, int]:
        return dict(self.hashes[key])

    async def hset(self, key: str, mapping: dict[str, int]) -> int:
        self.hashes[key].update({str(k): int(v) for k, v in mapping.items()})
        return len(mapping)

    async def hincrby(self, key: str, field: str, amount: int) -> int:
        self.hashes[key][field] += int(amount)
        return self.hashes[key][field]

    async def aclose(self) -> None:
        self.closed = True


def test_redis_job_queue_processes_enqueued_jobs() -> None:
    fake_redis = FakeRedis()
    settings = Settings(
        store_path="D:/code/ResearchForge Agent Harness/.data/test-store.json",
        job_queue_backend="redis",
        redis_url="redis://example.test/0",
        job_queue_name="researchforge:test-jobs",
        job_worker_poll_seconds=0.01,
    )
    queue = JobQueueManager(settings=settings, redis_client_factory=lambda _url: fake_redis)
    calls: list[tuple[str, str]] = []
    processed = asyncio.Event()

    async def handler(run_id: str, job_id: str) -> None:
        calls.append((run_id, job_id))
        processed.set()

    async def main() -> None:
        await queue.start()
        await queue.enqueue(None, handler, "run_1", "job_1")
        await asyncio.wait_for(processed.wait(), timeout=1)
        await asyncio.sleep(0.05)
        snapshot = await queue.snapshot()
        assert snapshot["backend"] == "redis"
        assert snapshot["connected"] is True
        assert snapshot["queued"] == 0
        assert snapshot["running"] == 0
        assert snapshot["completed"] == 1
        assert snapshot["failed"] == 0
        assert snapshot["worker_running"] is True
        assert calls == [("run_1", "job_1")]
        await queue.stop()
        stopped = await queue.snapshot()
        assert stopped["worker_running"] is False
        assert fake_redis.closed is True

    asyncio.run(main())


def test_redis_api_mode_can_connect_without_starting_an_embedded_worker() -> None:
    fake_redis = FakeRedis()
    settings = Settings(
        store_path="D:/code/ResearchForge Agent Harness/.data/test-store.json",
        job_queue_backend="redis",
        redis_url="redis://example.test/0",
        job_queue_name="researchforge:api-only",
        job_worker_poll_seconds=0.01,
        job_worker_enabled=False,
    )
    queue = JobQueueManager(settings=settings, redis_client_factory=lambda _url: fake_redis)

    async def main() -> None:
        await queue.start()
        snapshot = await queue.snapshot()
        assert snapshot["connected"] is True
        assert snapshot["worker_enabled"] is False
        assert snapshot["worker_running"] is False

        async def handler() -> None:
            return None

        await queue.enqueue(None, handler)
        queued = await queue.snapshot()
        assert queued["queued"] == 1
        assert queued["worker_running"] is False
        await queue.stop()

    asyncio.run(main())


def test_redis_worker_invokes_before_handler_before_each_job() -> None:
    fake_redis = FakeRedis()
    settings = Settings(
        store_path="D:/code/ResearchForge Agent Harness/.data/test-store.json",
        job_queue_backend="redis",
        redis_url="redis://example.test/0",
        job_queue_name="researchforge:before-handler",
        job_worker_poll_seconds=0.01,
    )
    queue = JobQueueManager(settings=settings, redis_client_factory=lambda _url: fake_redis)
    calls: list[str] = []
    processed = asyncio.Event()

    def refresh_store() -> None:
        calls.append("refresh")

    async def handler() -> None:
        calls.append("handler")
        processed.set()

    async def main() -> None:
        queue.set_before_handler(refresh_store)
        await queue.start()
        await queue.enqueue(None, handler)
        await asyncio.wait_for(processed.wait(), timeout=1)
        await asyncio.sleep(0.05)
        assert calls == ["refresh", "handler"]
        await queue.stop()

    asyncio.run(main())


def test_enqueue_uses_task_workspace_when_job_metadata_is_missing(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.infra.store import store
    fake = FakeRedis()
    settings = Settings(store_path="unused-workspace-routing.json", job_queue_backend="redis", redis_url="redis://example.test/0", job_queue_name="researchforge:workspace-routing", job_worker_enabled=False, job_delivery_mode="list")
    manager = JobQueueManager(settings=settings, redis_client_factory=lambda _url: fake)
    monkeypatch.setattr(store, "read_job", lambda key: SimpleNamespace(metadata={}, task_id="task_other", kind="agent_run"))
    monkeypatch.setattr(store, "get_task", lambda key: SimpleNamespace(workspace_id="workspace_other"))
    async def handler(job_id):
        pass
    async def exercise():
        await manager.enqueue(None, handler, "job_route")
        payload = json.loads(next(iter(fake.queues.values()))[0])
        assert payload["workspace_id"] == "workspace_other"
        await manager.stop()
    asyncio.run(exercise())
