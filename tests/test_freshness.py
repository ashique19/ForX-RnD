"""FX cache freshness: session hours, STALE vs CLOSED, rate-limit gate."""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.freshness import (
    VALIDITY_CLOSED,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
    FetchGate,
    assess_ohlcv,
    fx_session_open,
    is_rate_limited_reason,
    should_fetch_ohlcv,
)
from forex_lab.ui.board import apply_freshness, row_from_signal


def _bars_ending(end: datetime, n: int = 40) -> pd.DataFrame:
    df = generate_synthetic_ohlcv(bars=n, seed=3)
    idx = pd.date_range(end=end, periods=n, freq="h")
    df = df.iloc[:n].copy()
    df.index = idx
    df.index.name = "Datetime"
    return df


def test_fx_session_weekend_is_closed():
    sat = datetime(2026, 9, 19, 12, 0, 0)  # Saturday
    sun_morning = datetime(2026, 9, 20, 10, 0, 0)
    sun_open = datetime(2026, 9, 20, 21, 30, 0)
    fri_open = datetime(2026, 9, 18, 15, 0, 0)
    fri_close = datetime(2026, 9, 18, 21, 30, 0)
    assert fx_session_open(sat) is False
    assert fx_session_open(sun_morning) is False
    assert fx_session_open(sun_open) is True
    assert fx_session_open(fri_open) is True
    assert fx_session_open(fri_close) is False


def test_in_session_recent_bar_is_ok():
    now = datetime(2026, 9, 21, 14, 20, 0)  # Monday
    df = _bars_ending(datetime(2026, 9, 21, 14, 0, 0))
    fresh = assess_ohlcv(df, "1h", now=now)
    assert fresh.validity == VALIDITY_OK
    assert fresh.suppress_live_signal() is False


def test_in_session_old_bar_is_stale():
    now = datetime(2026, 9, 21, 16, 0, 0)
    df = _bars_ending(datetime(2026, 9, 21, 10, 0, 0))  # 6h old on 1h
    fresh = assess_ohlcv(df, "1h", {"board": {"stale_bars": 2}}, now=now)
    assert fresh.validity == VALIDITY_STALE
    assert "refresh required" in fresh.reason
    assert fresh.suppress_live_signal() is True


def test_weekend_is_closed_not_stale_panic():
    now = datetime(2026, 9, 19, 12, 0, 0)  # Saturday
    df = _bars_ending(datetime(2026, 9, 18, 20, 0, 0))  # Friday 20:00
    fresh = assess_ohlcv(df, "1h", now=now)
    assert fresh.validity == VALIDITY_CLOSED
    assert "market likely closed" in fresh.reason.lower()
    assert fresh.suppress_live_signal() is False


def test_week_old_cache_on_weekend_is_still_stale():
    now = datetime(2026, 9, 19, 12, 0, 0)
    df = _bars_ending(datetime(2026, 9, 10, 12, 0, 0))
    fresh = assess_ohlcv(df, "1h", now=now)
    assert fresh.validity == VALIDITY_STALE


def test_missing_ohlcv():
    fresh = assess_ohlcv(None, "1h", now=datetime(2026, 9, 21, 12, 0, 0))
    assert fresh.validity == VALIDITY_MISSING
    assert fresh.suppress_live_signal() is True


def test_stale_row_does_not_flash_buy_sell():
    now = datetime(2026, 9, 21, 16, 0, 0)
    df = _bars_ending(datetime(2026, 9, 21, 10, 0, 0))
    last = {
        "datetime": "2026-09-21 10:00:00",
        "signal": "BUY",
        "raw_signal": "BUY",
        "confidence": 0.6,
        "dir_edge": 0.2,
        "p_buy": 0.6,
        "p_sell": 0.2,
        "p_hold": 0.2,
        "model": "xgboost",
        "close": 1.1,
    }
    row = row_from_signal("EURUSD", "1h", last, df, {"label_scheme": "triple_barrier", "horizon": 8, "atr_period": 14, "barrier": {"tp_atr": 2, "sl_atr": 2}})
    assert row.buy_sell == "BUY"
    fresh = assess_ohlcv(df, "1h", now=now)
    row = apply_freshness(row, fresh)
    assert row.validity == VALIDITY_STALE
    assert row.buy_sell == "—"
    assert "data stale" in row.signal_details
    assert row.raw_signal == "BUY"
    assert row.sparkline == []
    assert row.risk is not None and row.risk.available is False


def test_closed_row_keeps_last_model_class():
    now = datetime(2026, 9, 19, 12, 0, 0)
    df = _bars_ending(datetime(2026, 9, 18, 20, 0, 0))
    last = {
        "datetime": "2026-09-18 20:00:00",
        "signal": "SELL",
        "raw_signal": "SELL",
        "confidence": 0.55,
        "dir_edge": 0.1,
        "p_buy": 0.2,
        "p_sell": 0.55,
        "p_hold": 0.25,
        "model": "xgboost",
        "close": 1.1,
    }
    row = row_from_signal("EURUSD", "1h", last, df, {"label_scheme": "triple_barrier", "horizon": 8, "atr_period": 14, "barrier": {"tp_atr": 2, "sl_atr": 2}})
    row = apply_freshness(row, assess_ohlcv(df, "1h", now=now))
    assert row.validity == VALIDITY_CLOSED
    assert row.buy_sell == "SELL"
    assert row.sparkline  # CLOSED still plots cached closes
    assert row.risk is not None and row.risk.available


def test_should_fetch_skips_closed_realtime_and_respects_min_interval():
    now = datetime(2026, 9, 19, 12, 0, 0)
    df = _bars_ending(datetime(2026, 9, 18, 20, 0, 0))
    fresh = assess_ohlcv(df, "1h", now=now)
    ts = now.timestamp()
    assert (
        should_fetch_ohlcv(
            fresh, force=False, last_yf_ok_ts=ts, now_ts=ts + 10, interval="1h", realtime=True
        )
        is False
    )
    assert (
        should_fetch_ohlcv(
            fresh, force=True, last_yf_ok_ts=None, now_ts=ts, interval="1h", realtime=False
        )
        is True
    )


def test_fetch_gate_backoff_and_rate_limit_reason():
    assert is_rate_limited_reason("yfinance rate limited (HTTP 429)")
    assert not is_rate_limited_reason("yfinance empty")
    g = FetchGate()
    g.mark_fail("yfinance rate limited (429)", 1_000_000)
    assert g.allowed(1_000_000) is False
    assert g.until >= 1_000_000 + 120
    g.mark_ok("EURUSD", 1_000_500)
    assert g.allowed(1_000_500) is True
    assert g.last_yf_ok["EURUSD"] == 1_000_500
    first = g.next_stagger(3)
    second = g.next_stagger(3)
    assert {first, second} <= {0, 1, 2}
    assert first != second
