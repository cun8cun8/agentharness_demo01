import asyncio
import json
import hashlib
import threading
import time
import logging
from contextlib import suppress
from uuid import uuid4


logger = logging.getLogger(__name__)


class RedisDelivery:
    """At-least-once delivery using a consumer group with renewable leases."""

    group = "researchforge-workers"

    def __init__(self, redis, queue_name: str, visibility_seconds: int, max_retries: int, workspace_concurrency: int = 0):
        self.redis = redis
        self.stream = queue_name + ":stream"
        self.consumer = uuid4().hex
        self.visibility_ms = max(1000, visibility_seconds * 1000)
        self.max_retries = max_retries
        self.workspace_concurrency = workspace_concurrency
        self.attempts_key = self.stream + ":attempts"

    async def start(self):
        from redis.exceptions import ResponseError
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, payload: str):
        return await self.redis.xadd(self.stream, {"payload": payload})

    async def take(self):
        claimed = await self.redis.xautoclaim(self.stream, self.group, self.consumer, self.visibility_ms, "0-0", count=1)
        if claimed[1]:
            return claimed[1][0]
        entries = await self.redis.xreadgroup(self.group, self.consumer, {self.stream: ">"}, count=1, block=1000)
        return entries[0][1][0] if entries else None

    # Every mutation checks the execution token. A stale consumer cannot renew
    # another worker's delivery, acknowledge it, or release its execution lock.
    _RENEW = """
        if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
        if #KEYS >= 3 then
            if redis.call('GET', KEYS[3]) ~= ARGV[3] then return 0 end
            redis.call('PEXPIRE', KEYS[3], ARGV[4])
        end
        redis.call('PEXPIRE', KEYS[2], ARGV[4])
        local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
        if #pending == 0 or pending[1][2] ~= ARGV[5] then return 2 end
        redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[5], 0, ARGV[2], 'JUSTID')
        return 1
    """
    _RELEASE = """
        if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
        local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
        if #pending > 0 and pending[1][2] == ARGV[5] then
            redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[5], 0, ARGV[2], 'IDLE', ARGV[4], 'JUSTID')
        end
        redis.call('DEL', KEYS[2])
        return 1
    """
    _DEFER = """
        if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
        local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
        if #pending > 0 and pending[1][2] == ARGV[5] then
            redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[5], 0, ARGV[2], 'IDLE', math.max(0, tonumber(ARGV[4]) - 1000), 'JUSTID')
        end
        redis.call('DEL', KEYS[2])
        return 1
    """
    _RELEASE_SLOT = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"

    async def _reserve_workspace(self, payload, token):
        try:
            workspace = json.loads(payload).get("workspace_id") or "workspace_default"
        except (ValueError, AttributeError):
            workspace = "workspace_default"
        namespace = self.stream + ":workspace:" + hashlib.sha256(str(workspace).encode()).hexdigest()
        for slot in range(self.workspace_concurrency):
            key = namespace + ":" + str(slot)
            if await self.redis.set(key, token, nx=True, px=self.visibility_ms):
                return key
        return None

    _ACK = """
        if redis.call('GET', KEYS[2]) ~= ARGV[3] then return 0 end
        local pending = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[2], ARGV[2], 1)
        if #pending == 0 or pending[1][2] ~= ARGV[5] then return 0 end
        if ARGV[6] == 'dead' then
            redis.call('XADD', KEYS[4], '*', 'payload', ARGV[7], 'error', ARGV[8], 'message_id', ARGV[2])
        end
        redis.call('XACK', KEYS[1], ARGV[1], ARGV[2])
        redis.call('XDEL', KEYS[1], ARGV[2])
        redis.call('HDEL', KEYS[3], ARGV[2])
        redis.call('DEL', KEYS[2])
        return 1
    """

    def _execution_key(self, payload):
        try:
            parsed = json.loads(payload)
            # Job IDs are globally unique, regardless of request serialization.
            job = next((arg for arg in parsed.get('args', [])
                        if isinstance(arg, str) and arg.startswith('job_')), None)
            identity = job or json.dumps(parsed, sort_keys=True, separators=(',', ':'))
        except (ValueError, AttributeError):
            identity = payload
        return self.stream + ':execution:' + hashlib.sha256(identity.encode()).hexdigest()

    def _sync_client(self):
        from redis import Redis, ConnectionPool, Connection, SSLConnection, UnixDomainSocketConnection
        original = self.redis.connection_pool
        kwargs = dict(original.connection_kwargs)
        timeout = min(5, max(.1, self.visibility_ms / 6000))
        kwargs.update(socket_timeout=timeout, socket_connect_timeout=timeout)
        name = original.connection_class.__name__
        connection = SSLConnection if 'SSL' in name else UnixDomainSocketConnection if 'Unix' in name else Connection
        return Redis(connection_pool=ConnectionPool(connection_class=connection, **kwargs))

    def _heartbeat_thread(self, message_id, lock_key, token, stopped, lost, loop, callback_task, slot_key=None):
        try:
            client = self._sync_client()
        except Exception as exc:
            logger.error('Redis heartbeat unavailable: error_type=%s', type(exc).__name__)
            lost.set()
            loop.call_soon_threadsafe(callback_task.cancel)
            return
        last_success = time.monotonic()
        try:
            while not stopped.wait(max(.1, self.visibility_ms / 3000)):
                try:
                    keys = [self.stream, lock_key] + ([slot_key] if slot_key else [])
                    owned = client.eval(self._RENEW, len(keys), *keys,
                        self.group, message_id, token, self.visibility_ms, self.consumer)
                    if owned == 1:
                        client.set(self.stream + ":workers:" + self.consumer, str(time.time()), px=self.visibility_ms)
                        last_success = time.monotonic()
                    if owned != 1 and not lost.is_set():
                        lost.set()
                        loop.call_soon_threadsafe(callback_task.cancel)
                    if owned == 0:
                        break
                    # If ownership moved, keep our execution lock alive until
                    # the cancelled callback actually exits. No parallel writer.
                except Exception:
                    if time.monotonic() - last_success >= self.visibility_ms / 1000:
                        lost.set()
                        loop.call_soon_threadsafe(callback_task.cancel)
                        break
        finally:
            client.close()
            client.connection_pool.disconnect()

    async def handle(self, message, callback):
        message_id, fields = message
        payload = fields['payload']
        lock_key = self._execution_key(payload)
        token = uuid4().hex
        if not await self.redis.set(lock_key, token, nx=True, px=self.visibility_ms):
            # Do not consume the retry budget for duplicate delivery contention.
            return None
        # Confirm message ownership before invoking any handler side effect.
        owned = await self.redis.eval(self._RENEW, 2, self.stream, lock_key,
            self.group, message_id, token, self.visibility_ms, self.consumer)
        if owned != 1:
            await self.redis.eval(self._RELEASE, 2, self.stream, lock_key,
                self.group, message_id, token, self.visibility_ms, self.consumer)
            return None
        slot_key = None
        if self.workspace_concurrency:
            try:
                slot_key = await self._reserve_workspace(payload, token)
            except BaseException:
                await self.redis.eval(self._RELEASE, 2, self.stream, lock_key, self.group, message_id, token, self.visibility_ms, self.consumer)
                raise
            if slot_key is None:
                # Leave this delivery pending without consuming attempts; a
                # worker can immediately serve a different workspace instead.
                await self.redis.eval(self._DEFER, 2, self.stream, lock_key, self.group, message_id, token, self.visibility_ms, self.consumer)
                return None
        stopped, lost = threading.Event(), threading.Event()
        callback_task = None
        heartbeat = None
        try:
            attempts = await self.redis.hincrby(self.attempts_key, message_id, 1)
            callback_task = asyncio.create_task(callback(payload))
            heartbeat = threading.Thread(target=self._heartbeat_thread,
                args=(message_id, lock_key, token, stopped, lost, asyncio.get_running_loop(), callback_task, slot_key),
                name='redis-delivery-heartbeat', daemon=True)
            heartbeat.start()
            try:
                await callback_task
            except asyncio.CancelledError:
                if lost.is_set() and not asyncio.current_task().cancelling():
                    return None
                raise
            except Exception as exc:
                logger.error('Redis delivery failed: message_id=%s attempt=%s error_type=%s',
                             message_id, attempts, type(exc).__name__)
                if attempts <= self.max_retries:
                    return None
                return False if await self._ack(message_id, lock_key, token, payload, type(exc).__name__) else None
            else:
                return True if await self._ack(message_id, lock_key, token) else None
        finally:
            stopped.set()
            if heartbeat is not None:
                await asyncio.to_thread(heartbeat.join, 10)
            try:
                await self.redis.eval(self._RELEASE, 2, self.stream, lock_key,
                    self.group, message_id, token, self.visibility_ms, self.consumer)
            finally:
                if slot_key:
                    await self.redis.eval(self._RELEASE_SLOT, 1, slot_key, token)

    async def _ack(self, message_id, lock_key, token, payload='', error=''):
        return await self.redis.eval(self._ACK, 4, self.stream, lock_key,
            self.attempts_key, self.stream + ':dead', self.group, message_id, token,
            self.visibility_ms, self.consumer, 'dead' if error else '', payload, error)

    async def counts(self):
        groups = await self.redis.xinfo_groups(self.stream)
        group = next((item for item in groups if item["name"] == self.group), {})
        return {"queued": group.get("lag") or 0, "running": group.get("pending") or 0, "dead_letters": await self.redis.xlen(self.stream + ":dead")}
