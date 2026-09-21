import time
from bcs.cache import TTLCache, make_cache_key, cached_llm_call


class TestTTLCache:
    def test_set_and_get(self):
        c = TTLCache(maxsize=10, ttl_seconds=60)
        c.set("key1", "value1")
        assert c.get("key1") == "value1"

    def test_miss(self):
        c = TTLCache(maxsize=10, ttl_seconds=60)
        assert c.get("nonexistent") is None

    def test_expiry(self):
        c = TTLCache(maxsize=10, ttl_seconds=1)
        c.set("key1", "value1")
        assert c.get("key1") == "value1"
        time.sleep(1.1)
        assert c.get("key1") is None

    def test_custom_ttl(self):
        c = TTLCache(maxsize=10, ttl_seconds=60)
        c.set("key1", "value1", ttl=2)
        time.sleep(1)
        assert c.get("key1") == "value1"
        time.sleep(1.5)
        assert c.get("key1") is None

    def test_eviction(self):
        c = TTLCache(maxsize=3, ttl_seconds=60)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.set("d", 4)
        assert c.size <= 3

    def test_clear(self):
        c = TTLCache(maxsize=10, ttl_seconds=60)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert c.size == 0
        assert c.get("a") is None

    def test_stats(self):
        c = TTLCache(maxsize=10, ttl_seconds=60)
        c.set("a", 1)
        c.get("a")
        c.get("b")
        stats = c.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_cached_call(self):
        c = TTLCache(maxsize=10, ttl_seconds=60)
        calls = []
        def fallback():
            calls.append(1)
            return "result"
        key = make_cache_key("test")
        result1 = cached_llm_call(key, fallback)
        assert result1 == "result"
        assert len(calls) == 1
        result2 = cached_llm_call(key, fallback)
        assert result2 == "result"
        assert len(calls) == 1


class TestMakeCacheKey:
    def test_consistent(self):
        k1 = make_cache_key("hello", "world")
        k2 = make_cache_key("hello", "world")
        assert k1 == k2

    def test_different(self):
        k1 = make_cache_key("hello", "world")
        k2 = make_cache_key("hello", "there")
        assert k1 != k2

    def test_not_empty(self):
        k = make_cache_key("a", "b")
        assert len(k) == 64
        assert all(c in "0123456789abcdef" for c in k)
