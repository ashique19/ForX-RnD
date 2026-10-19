"""Tests for research/htf/daily_trend_flip_wf_20261020.py (synthetic data only)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from forex_lab.backtest import _simulate_trades, true_range_atr

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("htf20261020", ROOT / "research" / "htf" / "daily_trend_flip_wf_20261020.py")
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
HOLD, BUY, SELL = M.HOLD, M.BUY, M.SELL
CFG = {"horizon": 8, "one_position": True, "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"},
       "spread_pips": 1.4, "commission_pips": 0.0, "atr_period": 14}


def _bars(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-06", periods=n, freq="h")
    c = 1.1 + np.cumsum(rng.normal(0, 0.0012, n))
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) + rng.uniform(0, 0.0008, n)
    lo = np.minimum(o, c) - rng.uniform(0, 0.0008, n)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": 1.0}, index=idx)


def test_baseline_matches_harness():
    df = _bars()
    rng = np.random.default_rng(2)
    sig = pd.Series(rng.choice([HOLD, HOLD, BUY, SELL], len(df) - 100), index=df.index[50:-50])
    atr = true_range_atr(df, 14)
    a = _simulate_trades(df, sig, CFG, "EURUSD", atr=atr)
    b = M.simulate(df, sig, CFG, "EURUSD", atr)
    assert len(a) == len(b) > 50
    assert (a["entry_time"].values == b["entry_time"].values).all()
    assert np.allclose(a["net_return"].values, b["net_return"].values)
    assert (a["exit_reason"].values == b["exit_reason"].values).all()


def test_flip_exit_next_open_and_reverse():
    df = _bars(400, seed=3)
    df["High"] = df[["Open", "Close"]].max(axis=1) + 1e-6  # tiny ranges: barriers never hit inside 8 bars
    df["Low"] = df[["Open", "Close"]].min(axis=1) - 1e-6
    atr = pd.Series(0.05, index=df.index)
    sig = pd.Series(HOLD, index=df.index[20:300]); sig.iloc[0] = BUY
    raw = sig.copy(); conf = pd.Series(0.7, index=sig.index)
    t0 = sig.index[0]
    s_loc = df.index.get_loc(t0) + 3
    raw[df.index[s_loc]] = SELL
    base = M.simulate(df, sig, CFG, "EURUSD", atr)
    assert base.iloc[0]["exit_reason"] == "timeout"
    x1 = M.simulate(df, sig, CFG, "EURUSD", atr, raw=raw, conf=conf, mode="x1")
    assert x1.iloc[0]["exit_reason"] == "flip" and x1.iloc[0]["exit"] == df["Open"].iloc[s_loc + 1]
    conf_low = conf.copy(); conf_low[df.index[s_loc]] = 0.5
    assert M.simulate(df, sig, CFG, "EURUSD", atr, raw=raw, conf=conf_low, mode="x1").iloc[0]["exit_reason"] == "timeout"
    assert M.simulate(df, sig, CFG, "EURUSD", atr, raw=raw, conf=conf_low, mode="x2").iloc[0]["exit_reason"] == "flip"
    sig2 = sig.copy(); sig2[df.index[s_loc]] = SELL
    x3 = M.simulate(df, sig2, CFG, "EURUSD", atr, raw=raw, conf=conf, mode="x3")
    assert len(x3) == 2 and x3.iloc[1]["side"] == "SELL" and x3.iloc[1]["entry_time"] == df.index[s_loc + 1]
    assert len(M.simulate(df, sig2, CFG, "EURUSD", atr, raw=raw, conf=conf, mode="x1")) == 1


def test_daily_trend_no_lookahead_and_filter():
    df = _bars(24 * 80, seed=4)
    d = M.daily_bars(df)
    assert (d["hours"] >= 12).all()
    tr = M.trend_for_signals(d, pd.DatetimeIndex(df.index))
    ok = tr.dropna(subset=["end_utc"])
    assert (ok["end_utc"] <= ok.index + pd.Timedelta(hours=1)).all()
    # day end is 17:00 New York (21:00 or 22:00 UTC)
    assert set(pd.DatetimeIndex(d["end_utc"]).hour) <= {21, 22}
    pred = pd.Series([BUY, SELL, BUY, SELL], index=df.index[:4])
    trend = pd.Series([1.0, 1.0, np.nan, -1.0], index=df.index[:4])
    assert list(M.apply_trend_filter(pred, trend)) == [BUY, HOLD, HOLD, SELL]


def test_metrics_r_units():
    t = pd.DataFrame({"entry_time": pd.date_range("2024-01-01", periods=4, freq="h"), "r": [1.0, -0.5, -1.0, 2.0],
                      "net_return": [0.01, -0.005, -0.01, 0.02]})
    m = M.metrics(t)
    assert m["n"] == 4 and abs(m["pf"] - 2.0) < 1e-9 and abs(m["total_r"] - 1.5) < 1e-9 and abs(m["max_dd_r"] + 1.5) < 1e-9