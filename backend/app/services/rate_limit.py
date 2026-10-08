from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
from hashlib import sha256
from math import ceil
from threading import Lock
from time import monotonic
from uuid import uuid4

import redis


class SlidingWindowLimiter:
    """Small process-local limiter; deployment-wide limits belong at the gateway."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= max(1, limit):
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                return False, retry_after
            events.append(now)
            if len(self._events) > 10_000:
                self._events = defaultdict(deque, {name: values for name, values in self._events.items() if values and values[-1] > cutoff})
            return True, 0


request_limiter = SlidingWindowLimiter()

# Use Redis time and one atomic script so limits hold across API replicas.
_WINDOW_SCRIPT = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, math.max(1, math.ceil((tonumber(first[2]) + window - now) / 1000))}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0}
"""


class RedisWindowLimiter:
    def __init__(self, url: str):
        self.client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        self.script = self.client.register_script(_WINDOW_SCRIPT)

    def allow(self, key: str, limit: int, window_seconds: float = 60) -> tuple[bool, int]:
        result = self.script(keys=["researchforge:rate:" + sha256(key.encode()).hexdigest()],
                             args=[max(1, limit), ceil(window_seconds * 1000), uuid4().hex])
        return bool(result[0]), int(result[1])


@lru_cache(maxsize=4)
def redis_limiter(url: str) -> RedisWindowLimiter:
    return RedisWindowLimiter(url)
