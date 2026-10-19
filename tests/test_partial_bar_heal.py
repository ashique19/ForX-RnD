"""Partial-bar heal + forming-bar exclusion (2026-10-19 stuckness-monitor bug).

Hourly desk CSVs kept bars saved before their hour closed (one tick at hh:01, or a
still-forming hour) and nothing overwrote them. Every writer now keeps closed bars only
and each MT5 refresh re-fetches/overwrites the last 3 closed bars.
"""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forex_lab import data as D

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-19 14:35:00")  # 20:35 Asia/Dhaka


def _bars(end, n, px=1.33, vol=1000.0):
    idx = pd.date_range(end=end, periods=n, freq="1h")
    c = px + np.cumsum(np.full(n, 1e-4))
    df = pd.DataFrame({"Open": c, "High": c + 3e-4, "Low": c - 3e-4, "Close": c, "Volume": vol}, index=idx)
    df.index.name = "Datetime"
    return df


def _cfg(tmp_path):
    return {"interval": "1h", "paths": {"data_dir": str(tmp_path)}, "board": {"mt5_tip": True}}


def test_drop_forming_bars_keeps_only_closed_hours():
    df = _bars("2026-10-19 14:00", 5)
    out, n = D.drop_forming_bars(df, "1h", NOW)
    assert n == 1 and out.index.max() == pd.Timestamp("2026-10-19 13:00")
    # exactly at the close the bar counts as closed
    out2, n2 = D.drop_forming_bars(df, "1h", pd.Timestamp("2026-10-19 15:00"))
    assert n2 == 0 and len(out2) == 5


def test_drop_forming_bars_tz_aware_index_and_now():
    df = _bars("2026-10-19 14:00", 3)
    df.index = df.index.tz_localize("UTC")
    out, n = D.drop_forming_bars(df, "1h", pd.Timestamp("2026-10-19 20:35", tz="Asia/Dhaka"))
    assert n == 1 and len(out) == 2


def test_heal_window_last_three_closed_hours():
    assert D.heal_window(NOW) == [pd.Timestamp("2026-10-19 11:00"), pd.Timestamp("2026-10-19 12:00"),
                                  pd.Timestamp("2026-10-19 13:00")]


def _seed_cache(tmp_path, full):
    cfg = _cfg(tmp_path)
    cache = full.loc[: "2026-10-19 13:00"].copy()
    t13 = pd.Timestamp("2026-10-19 13:00")
    tick = float(full.loc[t13, "Open"])
    cache.loc[t13, ["Open", "High", "Low", "Close", "Volume"]] = [tick, tick, tick, tick, 1.0]  # one tick at 13:01
    cache.to_csv(D.data_path("EURGBP", cfg, "1h"))
    return cfg, cache


def test_mt5_refresh_heals_partial_bar_and_drops_forming(tmp_path):
    full = _bars("2026-10-19 14:00", 400)  # 14:00 = MT5 forming bar at 14:35
    cfg, cache = _seed_cache(tmp_path, full)
    # cache also holds a forming 14:00 bar (USDCAD case)
    cache.loc[pd.Timestamp("2026-10-19 14:00")] = full.loc[pd.Timestamp("2026-10-19 14:00")] * [1, 1, 1, 1, 0.3]
    cache.to_csv(D.data_path("EURGBP", cfg, "1h"))
    mt5 = full.tail(72).copy()
    df, src = D.try_mt5_refresh("EURGBP", cfg, incremental=True, fetch=lambda p, n: mt5, now_utc=NOW)
    assert src == D.SOURCE_MT5
    assert df.index.max() == pd.Timestamp("2026-10-19 13:00")  # forming 14:00 dropped
    t13 = pd.Timestamp("2026-10-19 13:00")
    for c in ("Open", "High", "Low", "Close"):
        assert df.loc[t13, c] == pytest.approx(full.loc[t13, c])
    assert df.loc[t13, "Volume"] > 1.0
    disk = pd.read_csv(D.data_path("EURGBP", cfg, "1h"), index_col=0, parse_dates=True)
    assert disk.index.max() == t13 and disk.loc[t13, "High"] == pytest.approx(full.loc[t13, "High"])
    import json
    meta = json.loads(D.data_path("EURGBP", cfg, "1h").with_suffix(".mt5.json").read_text(encoding="utf-8"))
    assert meta["forming_dropped"] == {"cache": 1, "mt5": 1}
    assert [h["ts"] for h in meta["healed_bars"] if h["action"] == "healed"] == ["2026-10-19 13:00:00"]


def test_partial_bar_heal_allowed_even_when_range_diverges_but_bad_tip_still_refused(tmp_path):
    full = _bars("2026-10-19 14:00", 400)
    t13 = pd.Timestamp("2026-10-19 13:00")
    full.loc[t13, "High"] = full.loc[t13, "Open"] * 1.006  # volatile hour: range > 0.3% guard
    cfg, cache = _seed_cache(tmp_path, full)
    df, src = D.try_mt5_refresh("EURGBP", cfg, incremental=True, fetch=lambda p, n: full.tail(72), now_utc=NOW)
    assert src == D.SOURCE_MT5 and df.loc[t13, "High"] == pytest.approx(full.loc[t13, "High"])
    # a cached bar NOT inside the MT5 range (wrong clock / tip stamped on an old bar) is still refused
    bad = full.tail(72).copy()
    bad.loc[t13, ["Open", "High", "Low", "Close"]] = bad.loc[t13, "Close"] * 0.99
    df2, tag = D.try_mt5_refresh("EURGBP", cfg, incremental=True, fetch=lambda p, n: bad, now_utc=NOW)
    assert df2 is None and "closed bar" in tag


def test_old_closed_bar_outside_heal_window_still_guarded():
    ex = _bars("2026-10-19 13:00", 50)
    new = ex.copy()
    t = pd.Timestamp("2026-10-19 09:00")  # outside the 3-bar heal window
    new.loc[t, "High"] = ex.loc[t, "High"] * 1.01
    with pytest.raises(D.MT5GuardError, match="closed bar"):
        D.validate_mt5_bars(ex, new, now_utc=NOW)


def test_signals_ignore_forming_last_bar(monkeypatch):
    from forex_lab import signals as S
    seen = {}

    def fake_build(df, cfg, pair=None):
        seen["max"] = df.index.max()
        raise RuntimeError("stop")

    monkeypatch.setattr(S, "load_model", lambda *a, **k: (None, [], "x"))
    monkeypatch.setattr(S, "build_features", fake_build)
    now = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("h")
    df = _bars(now, 10)  # last bar = current (forming) hour
    with pytest.raises(RuntimeError, match="stop"):
        S.generate_signals(df, {"interval": "1h"}, "EURUSD", interval="1h")
    assert seen["max"] == now - pd.Timedelta(hours=1)


def _rig():
    spec = importlib.util.spec_from_file_location("rig_heal", ROOT / "scripts" / "refresh_if_gap.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_refresh_if_gap_flags_partial_and_forming_tips(tmp_path):
    rig = _rig()
    now = datetime(2026, 10, 19, 14, 35, tzinfo=timezone.utc)
    def write(pair, last, mtime):
        p = tmp_path / f"{pair}_1h.csv"
        p.write_text(f"Datetime,Open,High,Low,Close,Volume\n{last},1,1,1,1,1.0\n", encoding="utf-8")
        ts = datetime.fromisoformat(mtime).replace(tzinfo=timezone.utc).timestamp(); os.utime(p, (ts, ts))
        return p
    states = {
        "GBPUSD": rig.csv_tip_state(write("GBPUSD", "2026-10-19 13:00:00", "2026-10-19 13:01:49")),
        "USDCAD": rig.csv_tip_state(write("USDCAD", "2026-10-19 14:00:00", "2026-10-19 14:16:30")),
        "EURUSD": rig.csv_tip_state(write("EURUSD", "2026-10-19 13:00:00", "2026-10-19 14:16:03")),
    }
    got = {s["pair"]: s["reason"] for s in rig.partial_suspects(states, now)}
    assert got == {"GBPUSD": "written_before_close", "USDCAD": "forming_bar"}


class _Tick:
    def __init__(self, ts):
        self.time = int(pd.Timestamp(ts).timestamp())


def test_mt5_bars_1h_synced_waits_for_stale_history(monkeypatch):
    from forex_lab import mt5_feed as F
    stale = _bars("2026-10-19 16:00", 5)   # server time: newest bar 16:00 (half-filled)
    fresh = _bars("2026-10-19 17:00", 6)   # after sync: 17:00 forming bar present
    calls = {"n": 0}

    def fake_bars(sym, count):
        calls["n"] += 1
        return stale if calls["n"] < 3 else fresh

    class FakeMT5:
        def symbol_info_tick(self, sym):
            return _Tick("2026-10-19 17:42")
    monkeypatch.setattr(F, "bars_1h", fake_bars)
    monkeypatch.setattr(F, "_mt5", lambda: FakeMT5())
    df, n, is_stale = F.bars_1h_synced("GBPUSD.sml", 6, retries=5, wait_s=0)
    assert not is_stale and n == 2 and df.index.max() == pd.Timestamp("2026-10-19 17:00")
    monkeypatch.setattr(F, "bars_1h", lambda s, c: stale)
    df, n, is_stale = F.bars_1h_synced("GBPUSD.sml", 6, retries=2, wait_s=0)
    assert is_stale and n == 2
