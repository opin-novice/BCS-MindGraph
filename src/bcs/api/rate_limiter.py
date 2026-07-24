import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

LIMITS = {
    "generate": (5, 60),
    "feedback": (10, 60),
    "default":  (30, 60),
}

# Authenticated users get higher limits
AUTH_LIMITS = {
    "generate": (20, 60),
    "feedback": (50, 60),
    "default":  (100, 60),
}


class IPRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._windows: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))

    def _prune(self, ip: str, zone: str, window: int):
        now = time.time()
        self._windows[ip][zone] = [t for t in self._windows[ip][zone] if now - t < window]

    def check(self, ip: str, zone: str, user_id: Optional[str] = None) -> Tuple[bool, int]:
        limits = AUTH_LIMITS if user_id else LIMITS
        limit, window = limits.get(zone, limits["default"])
        key = f"{user_id}|{ip}" if user_id else ip
        with self._lock:
            self._prune(key, zone, window)
            calls = self._windows[key][zone]
            if len(calls) >= limit:
                retry_after = int(calls[0] + window - time.time()) + 1
                return False, max(retry_after, 1)
            calls.append(time.time())
            return True, 0


_rate_limiter = IPRateLimiter()


def check_rate_limit(ip: str, zone: str, user_id: Optional[str] = None) -> Tuple[bool, int]:
    return _rate_limiter.check(ip, zone, user_id=user_id)


def reset_rate_limiter():
    _rate_limiter._windows.clear()
