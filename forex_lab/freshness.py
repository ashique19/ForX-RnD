"""OHLCV freshness for the trader board (research only).

Spot FX via yfinance is **not** a broker clock. These rules are practical
heuristics so the UI does not flash BUY/SELL as if it were live analysis.

Validity
--------
OK       last bar is within ~N × timeframe during an expected liquid session
CLOSED   weekend / Friday after ~21:00 UTC: show last bar + "market likely closed"
         (not a STALE panic)
STALE    last bar too old during a session (or cache missed a whole week)
MISSING  no usable bars
ERROR    unreadable / exception path (set by callers)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from forex_lab.clock import fmt_display

VALIDITY_OK = "OK"
VALIDITY_STALE = "STALE"
VALIDITY_MISSING = "MISSING"
VALIDITY_ERROR = "ERROR"
VALIDITY_CLOSED = "CLOSED"

INTERVAL_SECONDS = {
    "15m": 15 * 60,
    "15min": 15 * 60,
    "1h": 3600,
    "60m": 3600,
    "4h": 4 * 3600,
    "1d": 86400,
    "1D": 86400,
    "d": 86400,
}

# Spot FX: Sun 21:00 UTC → Fri 21:00 UTC (NY 17:00 close is the usual cut).
FX_CLOSE_WEEKDAY = 4  # Friday
FX_CLOSE_HOUR_UTC = 21
FX_OPEN_WEEKDAY = 6  # Sunday
FX_OPEN_HOUR_UTC = 21

DEFAULT_STALE_BARS = 2.0
YF_MIN_INTERVAL_S = 60
YF_BACKOFF_CAP_S = 600


def board_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("board") or {})


def interval_seconds(interval: str | None) -> int:
    raw = str(interval or "1h").strip()
    if raw in INTERVAL_SECONDS:
        return INTERVAL_SECONDS[raw]
    key = raw.lower().replace(" ", "")
    if key in INTERVAL_SECONDS:
        return INTERVAL_SECONDS[key]
    return 3600


def naive_utc(ts: object) -> datetime | None:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return None
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(t):
        return None
    return t.tz_convert("UTC").tz_localize(None).to_pydatetime()


def now_utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    t = now
    if t.tzinfo is not None:
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def fmt_ts(ts: object, *, seconds: bool = False) -> str:
    t = naive_utc(ts)
    if t is None:
        return "n/a"
    if seconds:
        return t.strftime("%Y-%m-%d %H:%M:%S UTC")
    return t.strftime("%Y-%m-%d %H:%M UTC")


def fx_session_open(now: datetime | None = None) -> bool:
    """True when spot FX is typically trading (24/5), in naive UTC."""
    t = now_utc(now)
    wd = t.weekday()
    hour = t.hour + t.minute / 60.0
    if wd == 5:  # Saturday
        return False
    if wd == FX_CLOSE_WEEKDAY and hour >= FX_CLOSE_HOUR_UTC:
        return False
    if wd == FX_OPEN_WEEKDAY and hour < FX_OPEN_HOUR_UTC:
        return False
    return True


def last_bar_time(ohlcv: pd.DataFrame | None) -> datetime | None:
    if ohlcv is None or ohlcv.empty:
        return None
    return naive_utc(ohlcv.index[-1])


def _ohlcv_incomplete(ohlcv: pd.DataFrame) -> str | None:
    need = ["Open", "High", "Low", "Close"]
    missing = [c for c in need if c not in ohlcv.columns]
    if missing:
        return f"incomplete OHLCV (missing {missing})"
    tail = ohlcv[need].iloc[-min(3, len(ohlcv)) :]
    if tail.isna().any().any():
        return "incomplete OHLCV (NaNs on recent bars)"
    if len(ohlcv) < 20:
        return f"incomplete OHLCV ({len(ohlcv)} bars)"
    return None


@dataclass
class Freshness:
    validity: str
    reason: str
    last_bar: datetime | None = None
    age_s: float | None = None
    stale_after_s: float = 7200
    session_open: bool = True

    @property
    def last_bar_label(self) -> str:
        return fmt_ts(self.last_bar)

    def suppress_live_signal(self) -> bool:
        """True → do not flash BUY/SELL as a fresh call."""
        return self.validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}


def assess_ohlcv(
    ohlcv: pd.DataFrame | None,
    interval: str | None,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> Freshness:
    """Classify cache vs now. Never invents bars."""
    tnow = now_utc(now)
    iv_s = interval_seconds(interval)
    stale_bars = float(board_cfg(cfg).get("stale_bars") or DEFAULT_STALE_BARS)
    stale_after = max(iv_s * stale_bars, iv_s + 60)
    slack = min(900, max(60, iv_s * 0.15))
    open_now = fx_session_open(tnow)

    if ohlcv is None or getattr(ohlcv, "empty", True):
        return Freshness(
            VALIDITY_MISSING,
            "no OHLCV cache — Fetch required",
            stale_after_s=stale_after,
            session_open=open_now,
        )
    bad = _ohlcv_incomplete(ohlcv)
    last = last_bar_time(ohlcv)
    if last is None:
        return Freshness(
            VALIDITY_MISSING,
            "OHLCV index has no timestamp",
            stale_after_s=stale_after,
            session_open=open_now,
        )
    age = (tnow - last).total_seconds()
    if bad:
        return Freshness(
            VALIDITY_STALE,
            f"{bad} — refresh required",
            last_bar=last,
            age_s=age,
            stale_after_s=stale_after,
            session_open=open_now,
        )
    if age < 0:
        # Future-dated bars (bad synthetic/cache or severe clock skew) must not look OK.
        return Freshness(
            VALIDITY_ERROR,
            f"last bar {fmt_ts(last)} is in the future — discard/refetch cache",
            last_bar=last,
            age_s=age,
            stale_after_s=stale_after,
            session_open=open_now,
        )

    if open_now:
        if age > stale_after + slack:
            return Freshness(
                VALIDITY_STALE,
                f"last bar {fmt_ts(last)} is older than {stale_bars:g}× {interval or '1h'} "
                "during a liquid session — refresh required",
                last_bar=last,
                age_s=age,
                stale_after_s=stale_after,
                session_open=True,
            )
        return Freshness(
            VALIDITY_OK,
            f"last bar {fmt_ts(last)} within {stale_bars:g}× timeframe",
            last_bar=last,
            age_s=age,
            stale_after_s=stale_after,
            session_open=True,
        )

    # Market likely closed: don't STALE-panic unless the cache missed the last week.
    week_s = 5 * 86400
    if age > week_s:
        return Freshness(
            VALIDITY_STALE,
            f"last bar {fmt_ts(last)} is older than the last weekly session — refresh required",
            last_bar=last,
            age_s=age,
            stale_after_s=stale_after,
            session_open=False,
        )
    return Freshness(
        VALIDITY_CLOSED,
        f"market likely closed · last bar {fmt_ts(last)}",
        last_bar=last,
        age_s=age,
        stale_after_s=stale_after,
        session_open=False,
    )


def approaching_stale(fresh: Freshness, *, frac: float = 0.8) -> bool:
    if fresh.validity != VALIDITY_OK or fresh.age_s is None:
        return fresh.validity == VALIDITY_STALE
    return fresh.age_s >= frac * fresh.stale_after_s


def should_fetch_ohlcv(
    fresh: Freshness,
    *,
    force: bool,
    last_yf_ok_ts: float | None,
    now_ts: float,
    interval: str | None,
    min_interval_s: int = YF_MIN_INTERVAL_S,
    realtime: bool = False,
) -> bool:
    """Whether to hit yfinance. Realtime prefers local cache until the bar is due."""
    if force:
        return True
    if fresh.validity == VALIDITY_MISSING:
        return True
    if last_yf_ok_ts is not None and (now_ts - last_yf_ok_ts) < max(min_interval_s, 1):
        return False
    if realtime and fresh.validity == VALIDITY_CLOSED:
        return False
    if fresh.validity == VALIDITY_STALE:
        return True
    if approaching_stale(fresh):
        return True
    iv = interval_seconds(interval)
    if last_yf_ok_ts is not None and (now_ts - last_yf_ok_ts) < iv:
        return False
    return False


def is_rate_limited_reason(reason: str | None) -> bool:
    r = (reason or "").lower()
    return any(
        tok in r
        for tok in (
            "429",
            "too many",
            "rate limit",
            "rate-limited",
            "ratelimit",
            "quota",
            "throttl",
        )
    )


@dataclass
class FetchGate:
    """In-process yfinance cooldown (Streamlit session). Not a broker limiter."""

    until: float = 0.0
    failures: int = 0
    last_error: str | None = None
    last_yf_ok: dict[str, float] = field(default_factory=dict)
    stagger_i: int = 0

    def allowed(self, now_ts: float) -> bool:
        return now_ts >= self.until

    def backoff_until_label(self) -> str:
        if self.until <= 0:
            return ""
        return fmt_display(datetime.fromtimestamp(self.until, tz=timezone.utc), seconds=True)

    def mark_fail(self, reason: str, now_ts: float) -> None:
        self.failures += 1
        delay = min(YF_BACKOFF_CAP_S, 30 * (2 ** max(0, self.failures - 1)))
        if is_rate_limited_reason(reason):
            delay = max(delay, 120)
        self.until = now_ts + delay
        self.last_error = reason

    def mark_ok(self, pair: str, now_ts: float) -> None:
        self.failures = 0
        self.last_error = None
        self.until = 0.0
        self.last_yf_ok[str(pair).upper()] = now_ts

    def next_stagger(self, n: int) -> int:
        if n <= 0:
            return 0
        i = self.stagger_i % n
        self.stagger_i = (self.stagger_i + 1) % n
        return i
