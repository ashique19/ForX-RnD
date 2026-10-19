"""Tests for research/htf/profit_lock_20261020.py (synthetic data only)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from forex_lab.backtest import _simulate_trades, true_range_atr

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plock", ROOT / "research" / "htf" / "profit_lock_20261020.py")
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)
CFG = {"horizon": 8, "one_position": True, "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"}, "spread_pips": 1.4, "commission_pips": 0.0}


def _bars(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-06", periods=n, freq="h")
    c = 1.1 + np.cumsum(rng.normal(0, 0.0012, n)); o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) + 3e-4, "Low": np.minimum(o, c) - 3e-4, "Close": c}, index=idx)


def test_baseline_matches_harness():
    df = _bars(); rng = np.random.default_rng(7)
    sig = pd.Series(rng.choice([P.HOLD, P.HOLD, P.BUY, P.SELL], len(df) - 100), index=df.index[50:-50])
    atr = true_range_atr(df, 14)
    a = _simulate_trades(df, sig, CFG, "EURUSD", atr=atr); b = P.simulate_lock(df, sig, CFG, "EURUSD", atr, None)
    assert len(a) == len(b) and np.allclose(a.net_return.values, b.net_return.values)


def _path(highs, lows, closes, opens=None):
    idx = pd.date_range("2024-01-02", periods=len(highs), freq="h")
    return pd.DataFrame({"Open": opens or closes, "High": highs, "Low": lows, "Close": closes}, index=idx)


def test_lock_moves_stop_next_bar_only():
    # 1R = 0.01. Bar1 reaches +0.9R; bar2 dips to +0.4R -> locked stop at +0.5R
    df = _path([1.0, 1.009, 1.006, 1.006], [1.0, 1.0, 1.004, 1.004], [1.0, 1.008, 1.005, 1.005])
    loc, raw, why = P.lock_exit(df, 1, 8, 1, 1.0, 0.01, 1.0, "A")
    assert why == "lock_sl" and loc == 2 and abs(raw - 0.005) < 1e-12
    loc, raw, why = P.lock_exit(df, 1, 3, 1, 1.0, 0.01, 1.0, None)
    assert why == "timeout"


def test_closeback_exits_next_open():
    df = _path([1.0, 1.009, 1.007, 1.007, 1.007], [1.0, 1.0, 1.004, 1.004, 1.004], [1.0, 1.008, 1.005, 1.006, 1.006],
               opens=[1.0, 1.0, 1.008, 1.0055, 1.006])
    loc, raw, why = P.lock_exit(df, 1, 8, 1, 1.0, 0.01, 1.0, "B")
    assert why == "closeback" and loc == 3 and abs(raw - 0.0055) < 1e-12


def test_partial_half_at_08():
    df = _path([1.0, 1.009, 1.0], [1.0, 1.0, 0.985], [1.0, 1.008, 0.99])
    loc, raw, why = P.lock_exit(df, 1, 8, 1, 1.0, 0.01, 1.0, "C")
    assert why == "sl" and abs(raw - (0.5 * 0.008 + 0.5 * -0.01)) < 1e-12

def test_dynamic_target_reset_and_outward_only():
    # entry 1.0, 1R = 0.01 (decision ATR 0.005, tp_atr 2). Bar1 ATR shrinks to 0.003 -> DT1 TP = +0.6R; bar2 high +0.7R hits it.
    df = _path([1.0, 1.004, 1.007, 1.0], [1.0, 0.999, 1.001, 1.0], [1.0, 1.003, 1.005, 1.0])
    atr = np.array([0.005, 0.003, 0.003, 0.003]); raw = np.array([2.0, 2.0, 2.0, 2.0])
    loc, r, why = P.lock_exit(df, 1, 8, 1, 1.0, 0.01, 1.0, "D1", atr_arr=atr, raw_arr=raw, sig=2, tp_atr=2.0)
    assert why == "tp" and loc == 2 and abs(r - 0.006) < 1e-12
    loc, r, why = P.lock_exit(df, 1, 3, 1, 1.0, 0.01, 1.0, "D2", atr_arr=atr, raw_arr=raw, sig=2, tp_atr=2.0)
    assert why == "timeout"  # outward-only: TP never comes in
    atr_up = np.array([0.005, 0.008, 0.008, 0.008])
    df2 = _path([1.0, 1.009, 1.012, 1.0], [1.0, 0.999, 1.001, 1.0], [1.0, 1.008, 1.009, 1.0])
    loc, r, why = P.lock_exit(df2, 1, 3, 1, 1.0, 0.01, 1.0, "D2", atr_arr=atr_up, raw_arr=raw, sig=2, tp_atr=2.0)
    assert why == "timeout"  # TP moved out to +1.6R, 1.2R high does not hit it
    loc, r, why = P.lock_exit(df2, 1, 3, 1, 1.0, 0.01, 1.0, "D2", atr_arr=atr_up, raw_arr=np.array([1.0] * 4), sig=2, tp_atr=2.0)
    assert why == "tp" and abs(r - 0.01) < 1e-12  # model no longer agrees -> TP stays at 1R
