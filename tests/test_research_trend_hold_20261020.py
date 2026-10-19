"""Tests for research/htf/trend_hold_20261020.py (synthetic data only)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from forex_lab.backtest import _simulate_trades, true_range_atr

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trend_hold", ROOT / "research" / "htf" / "trend_hold_20261020.py")
H = importlib.util.module_from_spec(spec); spec.loader.exec_module(H)
CFG = {"horizon": 8, "one_position": True, "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"}, "spread_pips": 1.4, "commission_pips": 0.0}


def _bars(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-06", periods=n, freq="h")
    c = 1.1 + np.cumsum(rng.normal(0, 0.0012, n)); o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) + 3e-4, "Low": np.minimum(o, c) - 3e-4, "Close": c, "Volume": 1.0}, index=idx)


def test_unaligned_matches_harness_without_swap():
    df = _bars(); rng = np.random.default_rng(5)
    sig = pd.Series(rng.choice([H.HOLD, H.HOLD, H.BUY, H.SELL], len(df) - 100), index=df.index[50:-50])
    atr = true_range_atr(df, 14)
    a = _simulate_trades(df, sig, CFG, "EURUSD", atr=atr)
    b = H.simulate_hold(df, sig, CFG, "EURUSD", atr, pd.Series(False, index=sig.index), H.HOLD_VARIANTS["H120"])
    assert len(a) == len(b) and np.allclose(a.net_return.values, b.net_return.values)


def test_aligned_trades_get_longer_horizon():
    df = _bars(); rng = np.random.default_rng(6)
    sig = pd.Series(rng.choice([H.HOLD, H.HOLD, H.BUY, H.SELL], len(df) - 300), index=df.index[50:-250])
    atr = pd.Series(0.05, index=df.index)  # barriers far away -> timeouts
    t = H.simulate_hold(df, sig, CFG, "EURUSD", atr, pd.Series(True, index=sig.index), H.HOLD_VARIANTS["H48"])
    assert (t.bars == 48).all()


def test_rollover_count_and_wednesday_triple():
    inst, cumw = H.rollover_weights("2024-01-01", "2024-01-31")
    # Tue 2024-01-09 20:00 UTC -> Thu 2024-01-11 23:00 UTC crosses Tue (1) and Wed (3) 17:00 NY = 22:00 UTC
    n = H.nights([np.datetime64("2024-01-09T20:00")], [np.datetime64("2024-01-11T23:00")], inst, cumw)
    assert n[0] == 5.0  # Tue 1 + Wed 3 + Thu 1 (Thu 22:00 UTC <= 23:00)
    n2 = H.nights([np.datetime64("2024-01-09T10:00")], [np.datetime64("2024-01-09T18:00")], inst, cumw)
    assert n2[0] == 0.0


def test_trailing_stop_only_tightens():
    idx = pd.date_range("2024-01-02", periods=6, freq="h")
    df = pd.DataFrame({"Open": [1.0, 1.0, 1.01, 1.02, 1.03, 1.0], "High": [1.0, 1.005, 1.015, 1.025, 1.031, 1.03],
                       "Low": [1.0, 0.999, 1.005, 1.015, 1.025, 0.99], "Close": [1.0, 1.0, 1.01, 1.02, 1.03, 1.0]}, index=idx)
    x_loc, px, why = H._trail_exit(df, 1, 10, 1, 1.0, 0.02, 0.01)
    assert why == "trail_sl" and x_loc == 5 and abs(px - (1.031 - 0.01)) < 1e-12