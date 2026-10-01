"""Tip Volume self-heal: zeros -> filled from donor, OHLC preserved."""
from __future__ import annotations

import pandas as pd

from forex_lab.tip_vol_heal import (
    TipVolHealResult,
    format_heal_log_line,
    heal_pair_tip_volume,
    _coalesce_volume_only,
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
                {"pair": "EURUSD", "healed_bars": 5},
                {"pair": "USDJPY", "skipped": "tip_already_positive"},
            ],
        }
    )
    assert line.startswith("tip_vol_healed 5 bars")
    assert "EURUSD:5" in line


def test_heal_pair_skips_without_cache(tmp_path, monkeypatch):
    import forex_lab.tip_vol_heal as mod

    monkeypatch.setattr(mod, "load_cached_ohlcv", lambda *a, **k: None)
    r = heal_pair_tip_volume("EURUSD", cfg={"watchlist": {"active": "EURUSD"}}, write=False)
    assert isinstance(r, TipVolHealResult)
    assert r.skipped == "no_live_cache"
    assert r.healed_bars == 0
