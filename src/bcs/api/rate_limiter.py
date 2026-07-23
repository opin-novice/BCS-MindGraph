import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

LIMITS = {
    "generate": (5, 60),
    "feedback": (10, 60),
    "default":  (30, 60),
}


class IPRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._windows: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))

    def _prune(self, ip: str, zone: str, window: int):
        now = time.time()
        self._windows[ip][zone] = [t for t in self._windows[ip][zone] if now - t < window]

    def check(self, ip: str, zone: str) -> Tuple[bool, int]:
        limit, window = LIMITS.get(zone, LIMITS["default"])
        with self._lock:
            self._prune(ip, zone, window)
            calls = self._windows[ip][zone]
            if len(calls) >= limit:
                retry_after = int(calls[0] + window - time.time()) + 1
                return False, max(retry_after, 1)
            calls.append(time.time())
            return True, 0


_rate_limiter = IPRateLimiter()


def check_rate_limit(ip: str, zone: str) -> Tuple[bool, int]:
    return _rate_limiter.check(ip, zone)


def reset_rate_limiter():
    _rate_limiter._windows.clear()
