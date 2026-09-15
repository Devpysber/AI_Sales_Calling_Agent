"""
Shared state store.

Redis when REDIS_URL is set (required for multiple API replicas: call
sessions, generated audio, rate limits and the scheduler lock must be
visible to every instance). Falls back to an in-process store for
single-instance development.
"""

import json
import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

PREFIX = "va:"


class MemoryStore:
    def __init__(self):
        self._data: dict[str, tuple[Any, float | None]] = {}
        self._lock = threading.RLock()

    def _alive(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.time():
            self._data.pop(key, None)
            return None
        return value

    def get(self, key: str):
        with self._lock:
            return self._alive(key)

    def set(self, key: str, value, ttl: int | None = None, nx: bool = False) -> bool:
        with self._lock:
            if nx and self._alive(key) is not None:
                return False
            self._data[key] = (value, time.time() + ttl if ttl else None)
            return True

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            value = (self._alive(key) or 0) + 1
            expires = self._data.get(key, (None, time.time() + ttl))[1] or time.time() + ttl
            self._data[key] = (value, expires)
            return value

    def keys(self, pattern_prefix: str) -> list[str]:
        with self._lock:
            return [k for k in list(self._data) if k.startswith(pattern_prefix) and self._alive(k) is not None]

    def ping(self) -> bool:
        return True


class RedisStore:
    def __init__(self, url: str):
        import redis

        self.client = redis.Redis.from_url(url, socket_timeout=5, health_check_interval=30)

    def get(self, key: str):
        return self.client.get(key)

    def set(self, key: str, value, ttl: int | None = None, nx: bool = False) -> bool:
        return bool(self.client.set(key, value, ex=ttl, nx=nx))

    def delete(self, key: str):
        self.client.delete(key)

    def incr(self, key: str, ttl: int) -> int:
        pipe = self.client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        return pipe.execute()[0]

    def keys(self, pattern_prefix: str) -> list[str]:
        return [k.decode() for k in self.client.scan_iter(match=pattern_prefix + "*", count=500)]

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False


def _make_store():
    if settings.redis_url:
        log.info("State store: Redis")
        return RedisStore(settings.redis_url)
    if settings.is_production:
        log.warning("REDIS_URL not set: state is per-process; do not run multiple API instances")
    return MemoryStore()


store = _make_store()


# ---------------- typed helpers ----------------

def get_json(key: str, default=None):
    raw = store.get(PREFIX + key)
    if raw is None:
        return default
    return json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw


def set_json(key: str, value, ttl: int | None = None):
    store.set(PREFIX + key, json.dumps(value, ensure_ascii=False, default=str), ttl=ttl)


def get_bytes(key: str) -> bytes | None:
    return store.get(PREFIX + key)


def set_bytes(key: str, value: bytes, ttl: int | None = None):
    store.set(PREFIX + key, value, ttl=ttl)


def delete(key: str):
    store.delete(PREFIX + key)


def acquire_lock(name: str, owner: str, ttl: int) -> bool:
    """
    Simple leader lock: true if we hold (or just took) the lock.
    """
    key = PREFIX + "lock:" + name
    if store.set(key, owner, ttl=ttl, nx=True):
        return True
    current = store.get(key)
    if isinstance(current, bytes):
        current = current.decode()
    if current == owner:
        store.set(key, owner, ttl=ttl)
        return True
    return False


def rate_limited(bucket: str, limit: int, window: int) -> bool:
    return store.incr(PREFIX + "rl:" + bucket, window) > limit
