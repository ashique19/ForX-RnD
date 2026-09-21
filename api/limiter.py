"""In-process rate limit for POST /refresh. Not a broker throttle."""
from __future__ import annotations

import os
import threading
import time

DEFAULT_MIN_S = 18.0

_lock = threading.Lock()
_last: dict[str, float] = {}


def min_interval_s() -> float:
    raw = os.environ.get("FORX_REFRESH_MIN_S")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MIN_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_MIN_S


def reset() -> None:
    with _lock:
        _last.clear()


def allow(key: str, *, now: float | None = None) -> tuple[bool, float]:
    """Return (allowed, retry_after_s). ``retry_after_s`` is 0 when allowed."""
    interval = min_interval_s()
    clock = time.monotonic() if now is None else float(now)
    token = str(key or "").upper()
    with _lock:
        prev = _last.get(token)
        if interval > 0 and prev is not None:
            wait = interval - (clock - prev)
            if wait > 0:
                return False, wait
        _last[token] = clock
    return True, 0.0
