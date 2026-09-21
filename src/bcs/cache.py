import hashlib
import json
import threading
import time
from typing import Any, Callable, Optional

from bcs.logging_config import get_logger

log = get_logger(__name__)


class TTLCache:
    def __init__(self, maxsize: int = 256, ttl_seconds: int = 300):
        self._data: dict = {}
        self._maxsize = maxsize
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is not None:
                value, expiry = entry
                if time.time() < expiry:
                    self._hits += 1
                    return value
                del self._data[key]
            self._misses += 1
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            if len(self._data) >= self._maxsize:
                self._evict_lru()
            expiry = time.time() + (ttl if ttl is not None else self._ttl)
            self._data[key] = (value, expiry)

    def _evict_lru(self) -> None:
        now = time.time()
        expired = [k for k, (v, e) in self._data.items() if now >= e]
        for k in expired:
            del self._data[k]
        while len(self._data) >= self._maxsize:
            self._data.pop(next(iter(self._data)))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._data),
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
                "maxsize": self._maxsize,
                "ttl_seconds": self._ttl,
            }

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data)


llm_cache = TTLCache(maxsize=128, ttl_seconds=600)

kg_cache = TTLCache(maxsize=64, ttl_seconds=300)

web_cache = TTLCache(maxsize=32, ttl_seconds=3600)


def make_cache_key(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cached_llm_call(
    cache_key: str,
    fallback: Callable[[], str],
) -> str:
    cached = llm_cache.get(cache_key)
    if cached is not None:
        log.debug("LLM cache HIT: %s...", cache_key[:12])
        return cached
    result = fallback()
    if result:
        llm_cache.set(cache_key, result)
    return result
