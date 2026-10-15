"""MT5 /refresh write guards (2026-10-16): future bars, closed-bar overwrite, server->UTC, HALT."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forex_lab import data as D

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-15 19:20:00")


def _bars(end, n, px=1.10, vol=1000.0):
    idx = pd.date_range(end=end, periods=n, freq="1h")
    c = px + np.cumsum(np.full(n, 1e-4))
    return pd.DataFrame({"Open": c, "High": c + 2e-4, "Low": c - 2e-4, "Close": c, "Volume": vol}, index=idx)


def test_expected_offset_follows_us_dst():
    assert D.expected_mt5_server_offset_hours(pd.Timestamp("2026-10-15 19:00", tz="UTC")) == 3
    assert D.expected_mt5_server_offset_hours(pd.Timestamp("2026-12-15 19:00", tz="UTC")) == 2


def test_future_bar_rejected():
    with pytest.raises(D.MT5GuardError, match="future"):
        D.validate_mt5_bars(None, _bars("2026-10-15 21:00", 50), now_utc=NOW)


def test_current_forming_bar_allowed():
    D.validate_mt5_bars(_bars("2026-10-15 18:00", 50), _bars("2026-10-15 19:00", 51), now_utc=NOW)


def test_closed_bar_overwrite_with_tip_prices_rejected():
    ex = _bars("2026-10-15 18:00", 50)
    new = ex.copy()
    new.loc[pd.Timestamp("2026-10-15 17:00"), ["Open", "High", "Low", "Close"]] = 1.20
    with pytest.raises(D.MT5GuardError, match="closed bar"):
        D.validate_mt5_bars(ex, new, now_utc=NOW)


def test_small_closed_bar_correction_allowed():
    ex = _bars("2026-10-15 18:00", 50)
    new = ex.copy()
    new.loc[pd.Timestamp("2026-10-15 17:00"), "Close"] += 0.0001
    D.validate_mt5_bars(ex, new, now_utc=NOW)


def _cfg(tmp_path):
    return {"interval": "1h", "data_dir": str(tmp_path), "board": {"mt5_tip": True}}


def test_refresh_refuses_write_on_future_bars(tmp_path, monkeypatch):
    path = tmp_path / "EURUSD_1h.csv"
    ex = _bars("2026-10-15 18:00", 400)
    monkeypatch.setattr(D, "data_path", lambda pair, cfg, iv: path)
    monkeypatch.setattr(D, "load_cached_ohlcv", lambda pair, cfg, iv: ex)
    ex.to_csv(path)
    before = path.read_bytes()
    shifted = _bars("2026-10-15 22:00", 72)  # server-time labels (UTC+3) leaking into the cache
    df, tag = D.try_mt5_refresh("EURUSD", _cfg(tmp_path), incremental=True, fetch=lambda p, n: shifted, now_utc=NOW)
    assert df is None and tag.startswith("mt5 guard:") and "future" in tag
    assert path.read_bytes() == before


def test_refresh_refuses_on_closed_bar_divergence(tmp_path, monkeypatch):
    path = tmp_path / "EURUSD_1h.csv"
    ex = _bars("2026-10-15 18:00", 400)
    monkeypatch.setattr(D, "data_path", lambda pair, cfg, iv: path)
    monkeypatch.setattr(D, "load_cached_ohlcv", lambda pair, cfg, iv: ex)
    ex.to_csv(path); before = path.read_bytes()
    bad = ex.tail(72).copy(); bad[["Open", "High", "Low", "Close"]] += 0.01
    df, tag = D.try_mt5_refresh("EURUSD", _cfg(tmp_path), incremental=True, fetch=lambda p, n: bad, now_utc=NOW)
    assert df is None and "closed bar" in tag
    assert path.read_bytes() == before


def test_live_refresh_does_not_fall_back_after_guard(monkeypatch):
    monkeypatch.setattr(D, "try_mt5_refresh", lambda *a, **k: (None, "mt5 guard: future"))
    called = []
    monkeypatch.setattr(D, "try_dukascopy_refresh", lambda *a, **k: called.append(1) or (None, "x"))
    df, tag = D.try_live_refresh("EURUSD", {"interval": "1h"}, interval="1h", incremental=True)
    assert df is None and tag.startswith("mt5 guard:") and not called


def test_mt5_unavailable_returns_reason_without_write(tmp_path, monkeypatch):
    path = tmp_path / "EURUSD_1h.csv"
    monkeypatch.setattr(D, "data_path", lambda pair, cfg, iv: path)
    def boom(p, n):
        raise RuntimeError("Terminal: Call failed")
    df, tag = D.try_mt5_refresh("EURUSD", _cfg(tmp_path), incremental=True, fetch=boom, now_utc=NOW)
    assert df is None and "Call failed" in tag and not path.exists()


def test_explore_burst_honors_halt(tmp_path):
    halt = ROOT / "data" / "paper_explore" / "HALT"
    created = not halt.exists()
    halt.parent.mkdir(parents=True, exist_ok=True)
    if created:
        halt.write_text("test", encoding="utf-8")
    try:
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "explore_paper_burst.py"), "--base-url", "http://127.0.0.1:9"],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0 and '"halted": true' in out.stdout
    finally:
        if created:
            halt.unlink()
