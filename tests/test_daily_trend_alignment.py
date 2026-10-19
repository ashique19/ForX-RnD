"""Read-only daily-trend alignment what-if helpers (scripts/daily_trend_alignment.py)."""
import importlib.util
import pathlib

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("dta", ROOT / "scripts" / "daily_trend_alignment.py")
DTA = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(DTA)


def _h1(start, n, step=1e-4):
    idx = pd.date_range(start, periods=n, freq="1h")
    c = 1.1 + np.cumsum(np.full(n, step))
    return pd.DataFrame({"Open": c - step, "High": c + 1e-4, "Low": c - step - 1e-4, "Close": c}, index=idx)


def test_daily_bar_ends_at_17_ny_and_groups_from_previous_17_ny():
    h = _h1("2026-10-13 21:00", 24)  # EDT: 17:00 NY = 21:00 UTC; one full FX day
    d = DTA.daily_from_hourly(h)
    assert len(d) == 1 and d["hours"].iloc[0] == 24
    assert d["end_utc"].iloc[0] == pd.Timestamp("2026-10-14 21:00")
    assert d["Open"].iloc[0] == pytest.approx(h["Open"].iloc[0]) and d["Close"].iloc[0] == pytest.approx(h["Close"].iloc[-1])


def test_no_lookahead_only_completed_days_used():
    h = _h1("2026-07-01 21:00", 24 * 80)
    d = DTA.daily_from_hourly(h)
    entry = pd.Timestamp("2026-09-10 15:30")  # mid-day: today's bar is not finished
    tr = DTA.trend_at(d, entry)
    assert pd.Timestamp(tr["last_day_end_utc"]) <= entry
    assert pd.Timestamp(tr["last_day_end_utc"]) == pd.Timestamp("2026-09-09 21:00")
    assert tr["A_close_vs_sma20"] == 1 and tr["B_sma20_vs_sma50"] == 1 and tr["C_prior_day_body"] == 1 and tr["ALL_agree"] == 1


def test_short_history_and_partial_days_untagged():
    d = DTA.daily_from_hourly(_h1("2026-10-01 21:00", 24 * 10))
    assert DTA.trend_at(d, pd.Timestamp("2026-10-12"))["A_close_vs_sma20"] is None
    assert DTA.daily_from_hourly(_h1("2026-10-01 21:00", 5)).empty  # < 12 hours: dropped


def test_alignment_and_stats():
    assert DTA.aligned("BUY", 1) == "aligned" and DTA.aligned("SELL", 1) == "counter"
    assert DTA.aligned("SELL", -1) == "aligned" and DTA.aligned("BUY", 0) == "counter" and DTA.aligned("BUY", None) is None
    s = DTA.stats([1.0, -0.5, 0.5])
    assert s["win_rate"] == pytest.approx(0.667, abs=1e-3) and s["pf"] == 3.0 and s["total_r"] == 1.0


def test_script_is_read_only():
    src = (ROOT / "scripts" / "daily_trend_alignment.py").read_text(encoding="utf-8")
    for bad in ("eb.append(", "append_event(", "order_send(", ".to_csv(", "write_text(", "/pipeline/", "/model/train"):
        assert bad not in src