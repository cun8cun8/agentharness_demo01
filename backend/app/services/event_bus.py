from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from contextlib import suppress
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.config import Settings, get_settings

try:  # pragma: no cover - optional dependency
    from redis import asyncio as redis_asyncio
except Exception:  # pragma: no cover - optional dependency
    redis_asyncio = None


logger = logging.getLogger(__name__)


class EventPublisher:
    """Best-effort cross-process event publisher.

    Trace events are always persisted and delivered to local SSE subscribers by
    the store. This adapter adds Redis Pub/Sub for workers and external
    consumers without making the request path depend on the broker.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        redis_client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.backend = self.settings.event_bus_backend.strip().lower()
        self.url = self.settings.event_bus_url or self.settings.redis_url
        self.channel = self.settings.event_bus_channel
        self._redis_client_factory = redis_client_factory or self._default_redis_client_factory
        self._redis: Any | None = None
        self._kafka: Any | None = None
        self._pending: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._connected = self.backend == "none"
        self._last_error: str | None = None
        self._failure_count = 0
        self._dropped_count = 0
        self._circuit_open_until = 0.0

    def _default_redis_client_factory(self, url: str) -> Any:
        if redis_asyncio is None:
            raise RuntimeError("Redis event bus requires the 'redis' package")
        return redis_asyncio.from_url(url, decode_responses=True)

    async def start(self) -> None:
        if self.backend in {"kafka", "redpanda"}:
            async with self._lock:
                if self._kafka is None:
                    from aiokafka import AIOKafkaProducer
                    producer = AIOKafkaProducer(
                        bootstrap_servers=self.settings.kafka_bootstrap_servers,
                        enable_idempotence=True,
                        request_timeout_ms=10000,
                    )
                    try:
                        await producer.start()
                    except BaseException:
                        with suppress(Exception):
                            await asyncio.wait_for(producer.stop(), timeout=5)
                        self._connected = False
                        raise
                    self._kafka = producer
                self._connected = True
                self._last_error = None
            return
        if self.backend not in {"none", "redis"}:
            raise RuntimeError("EVENT_BUS_BACKEND_UNSUPPORTED")
        if self.backend != "redis":
            self._connected = True
            return
        async with self._lock:
            if self._redis is None:
                self._redis = self._redis_client_factory(self.url)
            try:
                await self._redis.ping()
            except Exception as exc:
                self._connected = False
                self._last_error = str(exc)
                raise RuntimeError("Unable to connect event bus") from exc
            self._connected = True
            self._last_error = None

    async def stop(self) -> None:
        if self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
        async with self._lock:
            if self._kafka is not None:
                try:
                    await asyncio.wait_for(self._kafka.stop(), timeout=5)
                except TimeoutError:
                    self._last_error = "TimeoutError"
                finally:
                    self._kafka = None
            if self._redis is not None:
                closer = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
                if closer is not None:
                    result = closer()
                    if inspect.isawaitable(result):
                        await result
            self._redis = None
            self._connected = self.backend == "none"

    async def publish(self, event_type: str, payload: dict[str, Any]) -> bool:
        if self.backend == "none":
            return False
        if time.monotonic() < self._circuit_open_until:
            self._dropped_count += 1
            return False
        attempts = self.settings.event_bus_max_retries + 1
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(self.settings.event_bus_publish_timeout_seconds):
                    await self._publish_event(event_type, payload)
                self._failure_count = 0
                self._circuit_open_until = 0.0
                self._last_error = None
                return True
            except Exception as exc:
                self._connected = False
                self._last_error = type(exc).__name__
                if attempt < attempts - 1:
                    await asyncio.sleep(self.settings.event_bus_retry_backoff_seconds * (2 ** attempt))
        self._failure_count += 1
        self._dropped_count += 1
        self._circuit_open_until = time.monotonic() + self.settings.event_bus_circuit_breaker_seconds
        logger.warning("Event bus publish failed after %s attempts: %s", attempts, self._last_error)
        return False

    async def _publish_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        await self.start()
        message = {
            "schema_version": "researchforge.event.v1",
            "event_type": event_type,
            "payload": payload,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        encoded = json.dumps(message, ensure_ascii=False, default=str)
        if self.backend in {"kafka", "redpanda"}:
            await self._kafka.send_and_wait(
                self.settings.kafka_topic, encoded.encode("utf-8"),
                key=str(payload.get("workspace_id") or "global").encode("utf-8"),
            )
        else:
            await self._redis.publish(self.channel, encoded)
        return True

    def publish_background(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.backend == "none":
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                async def publish_once():
                    publisher = EventPublisher(self.settings, self._redis_client_factory)
                    try:
                        await publisher.publish(event_type, payload)
                    finally:
                        await publisher.stop()
                asyncio.run(publish_once())
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._last_error = str(exc)
            return
        task = loop.create_task(self.publish(event_type, payload))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def stream(self):
        if self.backend in {"kafka", "redpanda"}:
            from aiokafka import AIOKafkaConsumer
            consumer = AIOKafkaConsumer(
                self.settings.kafka_topic,
                bootstrap_servers=self.settings.kafka_bootstrap_servers,
                group_id=None, auto_offset_reset="latest", enable_auto_commit=False,
            )
            try:
                await consumer.start()
                while True:
                    batches = await consumer.getmany(timeout_ms=1000)
                    if not batches:
                        yield ""
                    for batch in batches.values():
                        for record in batch:
                            yield record.value.decode("utf-8")
            finally:
                await consumer.stop()
            return
        if self.backend != "redis":
            raise RuntimeError("Event stream requires the Redis event bus")
        await self.start()
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.channel)
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message and message.get("data") is not None:
                    yield str(message["data"])
                else:
                    yield ""
                    await asyncio.sleep(0.05)
        finally:
            with suppress(Exception):
                await pubsub.unsubscribe(self.channel)
            closer = getattr(pubsub, "aclose", None) or getattr(pubsub, "close", None)
            if closer is not None:
                result = closer()
                if inspect.isawaitable(result):
                    await result

    def snapshot(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "channel": self.channel,
            "topic": self.settings.kafka_topic if self.backend in {"kafka", "redpanda"} else None,
            "connected": self._connected,
            "last_error": self._last_error,
            "failure_count": self._failure_count,
            "dropped_count": self._dropped_count,
            "circuit_open": time.monotonic() < self._circuit_open_until,
        }


event_publisher = EventPublisher()
