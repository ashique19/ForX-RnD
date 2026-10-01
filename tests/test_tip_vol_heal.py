"""Tip Volume self-heal: zeros -> filled from donor, OHLC preserved."""
from __future__ import annotations

import time

import pandas as pd

from forex_lab.tip_vol_heal import (
    TipVolHealResult,
    format_heal_log_line,
    heal_pair_tip_volume,
    heal_watchlist_tip_volumes,
    _coalesce_volume_only,
    _tip_zero_months,
)


def _frame(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    # rows: (ts, close, volume)
    idx = pd.to_datetime([r[0] for r in rows])
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[1] + 0.0001 for r in rows],
            "Low": [r[1] - 0.0001 for r in rows],
            "Close": [r[1] for r in rows],
            "Volume": [r[2] for r in rows],
        },
        index=idx,
    )


def test_coalesce_fills_zero_keeps_ohlc_and_positive_vol():
    base = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 2000.0),
            ("2026-10-01 02:00:00", 1.11, 0.0),
            ("2026-10-01 03:00:00", 1.12, 0.0),
        ]
    )
    donor = _frame(
        [
            ("2026-10-01 01:00:00", 9.99, 9999.0),  # must NOT clobber OHLC or positive vol
            ("2026-10-01 02:00:00", 9.99, 1500.0),
            ("2026-10-01 03:00:00", 9.99, 1600.0),
        ]
    )
    out, n = _coalesce_volume_only(base, donor)
    assert n == 2
    assert float(out.loc[pd.Timestamp("2026-10-01 01:00:00"), "Close"]) == 1.10
    assert float(out.loc[pd.Timestamp("2026-10-01 01:00:00"), "Volume"]) == 2000.0
    assert float(out.loc[pd.Timestamp("2026-10-01 02:00:00"), "Close"]) == 1.11
    assert float(out.loc[pd.Timestamp("2026-10-01 02:00:00"), "Volume"]) == 1500.0
    assert float(out.loc[pd.Timestamp("2026-10-01 03:00:00"), "Volume"]) == 1600.0


def test_coalesce_noop_when_tip_already_positive():
    base = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 100.0),
            ("2026-10-01 02:00:00", 1.11, 200.0),
        ]
    )
    donor = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 999.0),
            ("2026-10-01 02:00:00", 1.11, 999.0),
        ]
    )
    out, n = _coalesce_volume_only(base, donor)
    assert n == 0
    assert float(out.loc[pd.Timestamp("2026-10-01 02:00:00"), "Volume"]) == 200.0


def test_format_heal_log_line():
    line = format_heal_log_line(
        {
            "healed_bars": 5,
            "pairs": [
                {"pair": "EURUSD", "healed_bars": 5, "tip_zeros_before": 8, "tip_zeros_after": 3},
                {"pair": "USDJPY", "skipped": "tip_already_positive"},
            ],
            "duka_used": 1,
            "budget_remaining_sec": 12.5,
        }
    )
    assert line.startswith("tip_vol_healed 5 bars")
    assert "EURUSD:5" in line
    assert "z8->3" in line
    assert "duka_used=1" in line


def test_heal_pair_skips_without_cache(tmp_path, monkeypatch):
    import forex_lab.tip_vol_heal as mod

    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: None)
    r = heal_pair_tip_volume("EURUSD", cfg={"watchlist": {"active": "EURUSD"}}, write=False)
    assert isinstance(r, TipVolHealResult)
    assert r.skipped == "no_live_cache"
    assert r.healed_bars == 0


def test_skip_duka_when_hist_enough(monkeypatch):
    """History clears tip zeros → Dukascopy must not be called."""
    import forex_lab.tip_vol_heal as mod

    live = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 0.0),
            ("2026-10-01 02:00:00", 1.11, 0.0),
        ]
    )
    hist = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 500.0),
            ("2026-10-01 02:00:00", 1.11, 600.0),
        ]
    )
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("Dukascopy should be skipped when hist fills tip zeros")

    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: live)
    monkeypatch.setattr(mod, "load_history_csv", lambda *a, **k: hist)
    monkeypatch.setattr(mod, "history_path", lambda *a, **k: "dummy")
    monkeypatch.setattr(mod, "fetch_dukascopy_recent_bars", _boom)

    def _jetta_boom(*a, **k):
        raise AssertionError("Jetta should be skipped when hist fills tip zeros")

    monkeypatch.setattr(mod, "fetch_jetta_month", _jetta_boom)
    r = heal_pair_tip_volume(
        "EURUSD",
        cfg={"watchlist": {"active": "EURUSD"}},
        use_dukascopy=True,
        use_jetta=True,
        write=False,
    )
    assert calls["n"] == 0
    assert r.healed_bars == 2
    assert "history:2" in r.sources
    assert "duka_skipped:hist_enough" in r.sources
    assert r.tip_zeros_after == 0


def test_duka_deadline_skips_fetch(monkeypatch):
    import forex_lab.tip_vol_heal as mod

    live = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 0.0),
            ("2026-10-01 02:00:00", 1.11, 0.0),
        ]
    )
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1
        return _frame([])

    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: live)
    monkeypatch.setattr(mod, "load_history_csv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "history_path", lambda *a, **k: "dummy")
    monkeypatch.setattr(mod, "fetch_dukascopy_recent_bars", _count)
    monkeypatch.setattr(mod, "fetch_jetta_month", lambda *a, **k: (_ for _ in ()).throw(AssertionError("jetta should not run when disabled")))
    r = heal_pair_tip_volume(
        "EURUSD",
        cfg={"watchlist": {"active": "EURUSD"}},
        use_dukascopy=True,
        use_jetta=False,
        write=False,
        duka_deadline=time.monotonic() - 1.0,  # already expired
    )
    assert calls["n"] == 0
    assert "duka_skipped:budget" in r.sources
    assert r.healed_bars == 0


def test_duka_max_pairs_only_active(monkeypatch):
    """Watchlist heal: only first needy pair may call Dukascopy."""
    import forex_lab.tip_vol_heal as mod

    live = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 0.0),
            ("2026-10-01 02:00:00", 1.11, 0.0),
        ]
    )
    duka = _frame(
        [
            ("2026-10-01 01:00:00", 1.10, 111.0),
            ("2026-10-01 02:00:00", 1.11, 222.0),
        ]
    )
    calls: list[str] = []

    def _duka(pair, **k):
        calls.append(str(pair).upper())
        return duka

    monkeypatch.setattr(mod, "_watchlist_pairs", lambda cfg: ["EURUSD", "USDJPY", "GBPUSD"])
    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: live.copy())
    monkeypatch.setattr(mod, "load_history_csv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "history_path", lambda *a, **k: "dummy")
    monkeypatch.setattr(mod, "fetch_dukascopy_recent_bars", _duka)
    monkeypatch.setattr(mod, "fetch_jetta_month", lambda *a, **k: (_ for _ in ()).throw(AssertionError("jetta disabled")))
    s = heal_watchlist_tip_volumes(
        cfg={"watchlist": {"active": "EURUSD"}},
        tip_bars=8,
        use_dukascopy=True,
        use_jetta=False,
        write=False,
        max_pairs=3,
        duka_max_pairs=1,
        budget_sec=60.0,
        duka_lookback_hours=8,
    )
    assert calls == ["EURUSD"]
    assert s["duka_used"] == 1
    assert s["pairs"][0]["healed_bars"] == 2
    assert s["pairs"][1]["healed_bars"] == 0
    assert s["pairs"][2]["healed_bars"] == 0



def test_tip_zero_months_collects_calendar_months():
    frame = _frame(
        [
            ("2026-09-30 21:00:00", 1.10, 0.0),
            ("2026-10-01 07:00:00", 1.11, 0.0),
            ("2026-10-01 08:00:00", 1.12, 100.0),
        ]
    )
    assert _tip_zero_months(frame, 8) == [(2026, 9), (2026, 10)]


def test_jetta_fills_when_hist_empty(monkeypatch):
    """History empty + Jetta Sep donor fills tip zeros; Duka not required."""
    import forex_lab.tip_vol_heal as mod

    live = _frame(
        [
            ("2026-09-30 21:00:00", 1.10, 0.0),
            ("2026-09-30 22:00:00", 1.11, 0.0),
        ]
    )
    jetta = _frame(
        [
            ("2026-09-30 21:00:00", 9.99, 1500.0),
            ("2026-09-30 22:00:00", 9.99, 1600.0),
        ]
    )
    calls = {"jetta": 0, "duka": 0}

    def _jetta(pair, year, month, **k):
        calls["jetta"] += 1
        assert (year, month) == (2026, 9)
        return jetta

    def _duka(*a, **k):
        calls["duka"] += 1
        raise AssertionError("Duka should be skipped after Jetta clears tip")

    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: live)
    monkeypatch.setattr(mod, "load_history_csv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "history_path", lambda *a, **k: "dummy")
    monkeypatch.setattr(mod, "fetch_jetta_month", _jetta)
    monkeypatch.setattr(mod, "fetch_dukascopy_recent_bars", _duka)
    monkeypatch.setattr(mod, "_persist_history_volume", lambda *a, **k: 0)
    r = heal_pair_tip_volume(
        "EURUSD",
        cfg={"watchlist": {"active": "EURUSD"}},
        use_dukascopy=True,
        use_jetta=True,
        write=False,
    )
    assert calls["jetta"] == 1
    assert calls["duka"] == 0
    assert r.healed_bars == 2
    assert "jetta:2" in r.sources
    assert "duka_skipped:hist_enough" in r.sources
    assert r.tip_zeros_after == 0


def test_jetta_skip_not_fx(monkeypatch):
    import forex_lab.tip_vol_heal as mod

    live = _frame(
        [
            ("2026-10-01 01:00:00", 100.0, 0.0),
            ("2026-10-01 02:00:00", 101.0, 0.0),
        ]
    )
    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: live)
    monkeypatch.setattr(mod, "load_history_csv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "history_path", lambda *a, **k: "dummy")
    monkeypatch.setattr(
        mod,
        "fetch_jetta_month",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no jetta for BTC")),
    )
    monkeypatch.setattr(mod, "fetch_dukascopy_recent_bars", lambda *a, **k: None)
    r = heal_pair_tip_volume(
        "BTCUSD",
        cfg={"watchlist": {"active": "BTCUSD"}},
        use_dukascopy=False,
        use_jetta=True,
        write=False,
    )
    assert r.healed_bars == 0
    assert "jetta_skip:not_fx" in r.sources


def test_jetta_max_pairs_caps_network(monkeypatch):
    import forex_lab.tip_vol_heal as mod

    live = _frame(
        [
            ("2026-09-30 21:00:00", 1.10, 0.0),
            ("2026-09-30 22:00:00", 1.11, 0.0),
        ]
    )
    jetta = _frame(
        [
            ("2026-09-30 21:00:00", 1.10, 111.0),
            ("2026-09-30 22:00:00", 1.11, 222.0),
        ]
    )
    calls: list[str] = []

    def _jetta(pair, year, month, **k):
        calls.append(str(pair).upper())
        return jetta

    monkeypatch.setattr(mod, "_watchlist_pairs", lambda cfg: ["EURUSD", "USDJPY", "GBPUSD"])
    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: live.copy())
    monkeypatch.setattr(mod, "load_history_csv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "history_path", lambda *a, **k: "dummy")
    monkeypatch.setattr(mod, "fetch_jetta_month", _jetta)
    monkeypatch.setattr(mod, "_persist_history_volume", lambda *a, **k: 0)
    s = heal_watchlist_tip_volumes(
        cfg={"watchlist": {"active": "EURUSD"}},
        tip_bars=8,
        use_dukascopy=False,
        use_jetta=True,
        write=False,
        max_pairs=3,
        jetta_max_pairs=2,
        budget_sec=60.0,
    )
    assert calls == ["EURUSD", "USDJPY"]
    assert s["jetta_used"] == 2
    assert s["pairs"][0]["healed_bars"] == 2
    assert s["pairs"][1]["healed_bars"] == 2
    assert s["pairs"][2]["healed_bars"] == 0


def test_format_heal_log_line_includes_jetta_used():
    line = format_heal_log_line(
        {
            "healed_bars": 3,
            "pairs": [
                {"pair": "EURUSD", "healed_bars": 3, "tip_zeros_before": 10, "tip_zeros_after": 7},
            ],
            "jetta_used": 2,
            "duka_used": 0,
            "budget_remaining_sec": 20.0,
        }
    )
    assert "jetta_used=2" in line
    assert "duka_used=0" in line

