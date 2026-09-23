"""In-process rate limit for POST /refresh. Not a broker throttle.

The Decision desk auto-refreshes watchlist OHLCV on this same floor
(``data_refresh_seconds``, default 18). That cadence is market data only —
it does not run the research pipeline.
"""
from __future__ import annotations

import math
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


def data_refresh_seconds() -> int:
    """Desk auto-refresh cadence in whole seconds.

    Matches ``FORX_REFRESH_MIN_S`` (default 18), rounded up so the client
    does not poll faster than the limiter. A disabled floor (0) still
    reports 18 so the desk does not spin.
    """
    raw = min_interval_s()
    if raw <= 0:
        return int(DEFAULT_MIN_S)
    return max(1, math.ceil(raw - 1e-9))


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
