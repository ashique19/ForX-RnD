"""MT5 tip source, volume rescale, tip-volume guard, and burst staleness (2026-10-14 fix)."""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forex_lab import data as D

ROOT = Path(__file__).resolve().parents[1]


def _bars(start, n, vol, px=1.10):
    idx = pd.date_range(start, periods=n, freq="1h")
    c = px + np.cumsum(np.full(n, 1e-4))
    return pd.DataFrame({"Open": c, "High": c + 2e-4, "Low": c - 2e-4, "Close": c, "Volume": vol}, index=idx)


def test_volume_ratio_median_over_overlap():
    cache = _bars("2026-10-01", 200, 400.0)
    mt5 = _bars("2026-10-01", 200, 1000.0)
    r, n = D.mt5_volume_ratio(cache, mt5)
    assert n == 200 and r == pytest.approx(0.4)


def test_volume_ratio_falls_back_to_one_on_short_overlap():
    cache = _bars("2026-10-01", 10, 400.0)
    mt5 = _bars("2026-10-01", 10, 1000.0)
    assert D.mt5_volume_ratio(cache, mt5) == (1.0, 10)


def test_tip_guard_refills_from_mt5_and_never_drops():
    df = _bars("2026-10-01", 30, 500.0)
    df.iloc[-1, df.columns.get_loc("Volume")] = 0.0
    df.iloc[-2, df.columns.get_loc("Volume")] = np.nan
    refill = df.copy(); refill["Volume"] = 777.0
    out, flags = D.guard_tip_volume(df, refill=refill)
    assert len(out) == len(df)
    assert out["Volume"].iloc[-1] == 777.0 and out["Volume"].iloc[-2] == 777.0
    assert {f["action"] for f in flags} == {"refilled_mt5"}


def test_tip_guard_median_fallback_without_refill():
    df = _bars("2026-10-01", 30, 500.0)
    df.iloc[-1, df.columns.get_loc("Volume")] = 0.0
    out, flags = D.guard_tip_volume(df, refill=None)
    assert out["Volume"].iloc[-1] == 500.0 and flags[0]["action"] == "refilled_median20"


def test_try_mt5_refresh_merges_rescales_and_writes(tmp_path, monkeypatch):
    cfg = {"interval": "1h", "paths": {"data_dir": str(tmp_path)}}
    path = D.data_path("EURUSD", cfg, "1h")
    full = _bars("2026-09-01", 396, 400.0)  # one consistent price path (closed-bar guard)
    cache = full.iloc[:300].copy()
    cache.iloc[-3:, cache.columns.get_loc("Volume")] = 0.0  # yfinance_1m tip with Volume=0
    cache.index.name = "Datetime"; cache.to_csv(path)
    mt5 = full.iloc[96:].copy(); mt5["Volume"] = 1000.0  # overlaps and extends past the cache
    df, src = D.try_mt5_refresh("EURUSD", cfg, interval="1h", fetch=lambda p, n: mt5)
    assert src == D.SOURCE_MT5
    assert df.index.max() == mt5.index.max()
    tip = df["Volume"].iloc[-5:]
    assert (tip > 0).all() and tip.iloc[-1] == pytest.approx(400.0)  # rescaled to cache level
    assert D.read_cache_source("EURUSD", cfg, "1h") == D.SOURCE_MT5


def test_try_mt5_refresh_unavailable_falls_back(tmp_path):
    cfg = {"interval": "1h", "paths": {"data_dir": str(tmp_path)}}
    def boom(p, n):
        raise RuntimeError("terminal not running")
    df, tag = D.try_mt5_refresh("EURUSD", cfg, interval="1h", fetch=boom)
    assert df is None and tag.startswith("mt5 unavailable")


def test_try_mt5_refresh_skips_non_1h(tmp_path):
    cfg = {"interval": "1h", "paths": {"data_dir": str(tmp_path)}}
    df, tag = D.try_mt5_refresh("EURUSD", cfg, interval="1d", fetch=lambda p, n: None)
    assert df is None and "unsupported" in tag


def _burst():
    spec = importlib.util.spec_from_file_location("spb", ROOT / "scripts" / "shadow_paper_burst.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_burst_skips_stale_signal_as_not_current():
    m = _burst(); now = datetime(2026, 10, 14, 15, 0, tzinfo=m.DHAKA)
    row = {"pair": "EURJPY", "signal": "BUY", "confidence": 0.9, "validity": "OK", "status": "ready",
           "last_signal_dhaka": "2026-10-05 20:00:00 Asia/Dhaka"}
    ok, why = m.eligible(row, 0.6, now=now)
    assert not ok and why.startswith("not_current")


def test_burst_accepts_current_signal():
    m = _burst(); now = datetime(2026, 10, 14, 15, 0, tzinfo=m.DHAKA)
    row = {"pair": "EURJPY", "signal": "BUY", "confidence": 0.9, "validity": "OK", "status": "ready",
           "last_signal_dhaka": "2026-10-14 14:00:00 Asia/Dhaka"}
    assert m.eligible(row, 0.6, now=now)[0]
