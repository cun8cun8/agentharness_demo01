import asyncio
import os
from uuid import uuid4

import pytest

from app.services.redis_delivery import RedisDelivery


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_REDIS_URL"), reason="requires isolated Redis")
def test_stream_reclaims_unfinished_delivery_and_moves_exhausted_job_to_dead_letter():
    from redis.asyncio import from_url

    async def exercise():
        redis = from_url(os.environ["RESEARCHFORGE_TEST_REDIS_URL"], decode_responses=True)
        name = "rf-acceptance:" + uuid4().hex
        first = RedisDelivery(redis, name, 1, 1)
        second = RedisDelivery(redis, name, 1, 1)
        try:
            await first.start()
            await second.start()
            message_id = await first.enqueue('{"id":"recover"}')
            message = await first.take()
            assert message[0] == message_id
            await redis.xclaim(first.stream, first.group, first.consumer, 0, [message_id], idle=2000)
            recovered = await second.take()
            assert recovered[0] == message_id
            calls = []

            async def success(payload):
                calls.append(payload)

            assert await second.handle(recovered, success) is True
            assert calls == ['{"id":"recover"}']
            assert await second.counts() == {"queued": 0, "running": 0, "dead_letters": 0}
            failed_id = await first.enqueue("failure")

            async def failure(payload):
                raise ValueError("expected test failure")

            assert await first.handle(await first.take(), failure) is None
            await redis.xclaim(first.stream, first.group, first.consumer, 0, [failed_id], idle=2000)
            assert await second.handle(await second.take(), failure) is False
            assert await second.counts() == {"queued": 0, "running": 0, "dead_letters": 1}
            assert await redis.hlen(first.attempts_key) == 0
        finally:
            await redis.delete(first.stream, first.attempts_key, first.stream + ":dead")
            await redis.aclose()

    asyncio.run(exercise())


def test_delivery_logs_failure_type_without_payload_or_exception_message(caplog):
    class RedisStub:
        class connection_pool:
            connection_kwargs = {"host": "127.0.0.1", "port": 1}
            class connection_class:
                pass

        async def set(self, *args, **kwargs):
            return True

        async def eval(self, *args):
            return 1

        async def hincrby(self, *args):
            return 1

    async def exercise():
        delivery = RedisDelivery(RedisStub(), "unit-test", 60, 1)

        async def failure(payload):
            raise ValueError("private credential in exception")

        assert await delivery.handle(("1-0", {"payload": "private payload"}), failure) is None

    asyncio.run(exercise())
    assert "message_id=1-0 attempt=1 error_type=ValueError" in caplog.text
    assert "private credential" not in caplog.text
    assert "private payload" not in caplog.text

@pytest.mark.skipif(not os.getenv('RESEARCHFORGE_TEST_REDIS_URL'), reason='requires isolated Redis')
def test_graceful_shutdown_releases_lease_for_immediate_recovery():
    from redis.asyncio import from_url
    async def exercise():
        redis=from_url(os.environ['RESEARCHFORGE_TEST_REDIS_URL'],decode_responses=True)
        first=RedisDelivery(redis,'rf-acceptance:'+uuid4().hex,3600,3)
        second=RedisDelivery(redis,first.stream.removesuffix(':stream'),3600,3)
        started=asyncio.Event()
        async def waiting(payload):
            started.set()
            await asyncio.Event().wait()
        try:
            await first.start()
            mid=await first.enqueue('recovery')
            task=asyncio.create_task(first.handle(await first.take(),waiting))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            recovered=await asyncio.wait_for(second.take(),timeout=2)
            assert recovered[0]==mid
            async def success(payload):
                assert payload=='recovery'
            assert await second.handle(recovered,success) is True
            assert (await second.counts())['running']==0
        finally:
            await redis.delete(first.stream,first.attempts_key,first.stream+':dead')
            await redis.aclose()
    asyncio.run(exercise())


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_REDIS_URL"), reason="requires isolated Redis")
def test_independent_heartbeat_survives_blocked_event_loop_with_second_consumer():
    import subprocess
    import sys
    from redis.asyncio import from_url

    async def exercise():
        redis = from_url(os.environ["RESEARCHFORGE_TEST_REDIS_URL"], decode_responses=True)
        name = "rf-acceptance:" + uuid4().hex
        delivery = RedisDelivery(redis, name, 1, 2)
        probe = None
        try:
            await delivery.start()
            await delivery.enqueue('blocking')
            # A separate process attempts recovery while this event loop is
            # deliberately blocked for three times the one-second lease.
            code = """import os,time,redis,sys
r=redis.Redis.from_url(os.environ['RESEARCHFORGE_TEST_REDIS_URL'],decode_responses=True)
time.sleep(1.5)
result=r.xautoclaim(sys.argv[1],'researchforge-workers','competitor',1000,'0-0',count=1)
sys.exit(2 if result[1] else 0)
"""
            async def blocking(payload):
                import time
                nonlocal probe
                probe = subprocess.Popen([sys.executable, '-c', code, delivery.stream], stdout=subprocess.DEVNULL)
                time.sleep(3)
            assert await delivery.handle(await delivery.take(), blocking) is True
            assert probe.wait(timeout=5) == 0
            assert (await delivery.counts())['running'] == 0
        finally:
            if probe and probe.poll() is None:
                probe.kill()
                probe.wait()
            await redis.delete(delivery.stream, delivery.attempts_key, delivery.stream + ':dead')
            await redis.aclose()
    asyncio.run(exercise())


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_REDIS_URL"), reason="requires isolated Redis")
def test_duplicate_messages_for_same_job_cannot_execute_concurrently():
    from redis.asyncio import from_url
    async def exercise():
        redis = from_url(os.environ["RESEARCHFORGE_TEST_REDIS_URL"], decode_responses=True)
        name = 'rf-acceptance:' + uuid4().hex
        first, second = RedisDelivery(redis, name, 1, 2), RedisDelivery(redis, name, 1, 2)
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def active(payload):
            calls.append('first')
            started.set()
            await finish.wait()
        async def duplicate(payload):
            calls.append('duplicate')
        task = None
        try:
            await first.start()
            await first.enqueue('{"args":["resource","job_same",{"goal":"a"}]}')
            await first.enqueue('{"args":["resource","job_same",{"goal":"b"}]}')
            task = asyncio.create_task(first.handle(await first.take(), active))
            await started.wait()
            contender = await second.take()
            assert await second.handle(contender, duplicate) is None
            await asyncio.sleep(1.5)
            assert calls == ['first']
            assert await redis.hget(first.attempts_key, contender[0]) is None
            finish.set()
            assert await task is True
            assert await second.handle(contender, duplicate) is True
            assert calls == ['first', 'duplicate']
        finally:
            finish.set()
            if task:
                await task
            await redis.delete(first.stream, first.attempts_key, first.stream + ':dead')
            await redis.aclose()
    asyncio.run(exercise())


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_REDIS_URL"), reason="requires isolated Redis")
def test_stale_consumer_cannot_ack_a_message_owned_by_another_worker():
    from redis.asyncio import from_url
    async def exercise():
        redis = from_url(os.environ["RESEARCHFORGE_TEST_REDIS_URL"], decode_responses=True)
        name = 'rf-acceptance:' + uuid4().hex
        first, second = RedisDelivery(redis, name, 1, 2), RedisDelivery(redis, name, 1, 2)
        started, finish = asyncio.Event(), asyncio.Event()
        async def stale(payload):
            started.set()
            await finish.wait()
        try:
            await first.start()
            mid = await first.enqueue('ownership')
            task = asyncio.create_task(first.handle(await first.take(), stale))
            await started.wait()
            recovered = await redis.xclaim(first.stream, first.group, second.consumer, 0, [mid])
            finish.set()
            assert await task is None
            assert await redis.xlen(first.stream) == 1
            async def success(payload):
                assert payload == 'ownership'
            assert await second.handle(recovered[0], success) is True
            assert (await second.counts())['running'] == 0
        finally:
            finish.set()
            await redis.delete(first.stream, first.attempts_key, first.stream + ':dead')
            await redis.aclose()
    asyncio.run(exercise())


@pytest.mark.skipif(not os.getenv("RESEARCHFORGE_TEST_REDIS_URL"), reason="requires isolated Redis")
def test_workspace_capacity_defers_busy_workspace_without_blocking_another():
    import json
    from redis.asyncio import from_url
    async def exercise():
        redis = from_url(os.environ["RESEARCHFORGE_TEST_REDIS_URL"], decode_responses=True)
        name = "rf-acceptance:" + uuid4().hex
        first, second = RedisDelivery(redis,name,2,2,1), RedisDelivery(redis,name,2,2,1)
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        task = None
        async def active(payload):
            calls.append("a1")
            started.set()
            await finish.wait()
        async def other(payload):
            calls.append(json.loads(payload)["args"][0])
        try:
            await first.start()
            for workspace, job in [("a","job_a1"),("a","job_a2"),("b","job_b1")]:
                await first.enqueue(json.dumps({"workspace_id":workspace,"args":[job]}))
            task = asyncio.create_task(first.handle(await first.take(),active))
            await started.wait()
            blocked = await second.take()
            assert await second.handle(blocked,other) is None
            assert await redis.hget(first.attempts_key,blocked[0]) is None
            assert await second.handle(await second.take(),other) is True
            assert calls == ["a1","job_b1"]
            finish.set()
            assert await task is True
            assert await second.handle(blocked,other) is True
            assert calls == ["a1","job_b1","job_a2"]
            assert (await second.counts())["running"] == 0
        finally:
            finish.set()
            if task: await task
            keys = [key async for key in redis.scan_iter(match=name+"*")]
            if keys: await redis.delete(*keys)
            await redis.aclose()
    asyncio.run(exercise())
