import threading
import time
from typing import List

_lock = threading.Lock()
_calls: List[float] = []
_RPM_LIMIT = 28
_WINDOW_SEC = 60


def wait_for_rate_limit():
    now = time.time()
    with _lock:
        _calls[:] = [t for t in _calls if now - t < _WINDOW_SEC]
        if len(_calls) >= _RPM_LIMIT:
            sleep_for = _calls[0] + _WINDOW_SEC - now + 0.5
            time.sleep(sleep_for)
        _calls.append(time.time())
