"""Tests for research/htf/confluence_20261103.py. The no-lookahead tests are the critical ones (prereg PREREG_20261103.md)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forex_lab.backtest import _simulate_trades, true_range_atr

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("confluence_20261103", ROOT / "research" / "htf" / "confluence_20261103.py")
C = importlib.util.module_from_spec(_spec)
sys.modules["confluence_20261103"] = C
_spec.loader.exec_module(C)
H = pd.Timedelta(hours=1)
CFG = {"horizon": 8, "one_position": True, "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"}, "spread_pips": 1.4, "commission_pips": 0.0, "atr_period": 14}


def _h1(n_days=700, seed=5, start="2019-01-01"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=24 * n_days, freq="h")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    c = 1.1 + np.cumsum(rng.normal(0, 0.0012, n)) + 0.0003 * np.sin(np.arange(n) / 700) * np.arange(n) ** 0.5
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) + rng.uniform(0, 0.0008, n)
    lo = np.minimum(o, c) - rng.uniform(0, 0.0008, n)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": 1.0}, index=idx)


def _same(a: pd.Series, b: pd.Series) -> bool:
    return bool(((a == b) | (a.isna() & b.isna())).all())


def test_constants_and_trial_count():
    assert C.N_TRIALS == 167 + 7 and len(C.VARIANTS) == 7
    assert C.ALPHA == pytest.approx(0.05 / 174)


def test_h4_bin_alignment():
    idx = pd.date_range("2024-03-04 20:00", periods=10, freq="h")
    df = pd.DataFrame({"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": np.arange(10) + 1.0}, index=idx)
    b = C.h4_bars(df)
    assert list(b.index.hour[:2]) == [21, 1]  # the lone 20:00 bar (bin 17) and lone 05:00 bar (bin 05) have < 2 hourly bars and are dropped
    assert (b["end"] - b.index == pd.Timedelta(hours=4)).all()
    row21 = b.loc[pd.Timestamp("2024-03-04 21:00")]
    assert row21["Close"] == 5.0 and row21["Open"] == 1.0 and row21["n"] == 4  # hours 21,22,23,00; the 20:00 hour is in the 17:00 bin


def test_no_lookahead_truncation_synthetic():
    h = _h1()
    idx = h.index
    for k in range(3000, len(idx) - 5, 997):
        t = idx[k]
        full = C.trend_states(h, pd.DatetimeIndex([t]))
        trunc = C.trend_states(h.iloc[: k + 1], pd.DatetimeIndex([t]))
        for col in ("t4", "td", "ts"):
            assert _same(full[col], trunc[col]), (col, t)


def test_forming_bars_never_used_even_with_garbage_future():
    """Corrupt every bar after the signal bar (including the rest of its forming 4h bar and day): the states must not change."""
    h = _h1()
    idx = h.index
    for k in range(3200, len(idx) - 40, 1213):
        t = idx[k]
        g = h.copy()
        g.iloc[k + 1:, g.columns.get_indexer(["Open", "High", "Low", "Close"])] = g.iloc[k + 1:][["Open", "High", "Low", "Close"]].to_numpy() * 7.0
        a = C.trend_states(h, pd.DatetimeIndex([t]))
        b = C.trend_states(g, pd.DatetimeIndex([t]))
        for col in ("t4", "td", "ts"):
            assert _same(a[col], b[col]), (col, t)


def test_used_bar_end_is_at_or_before_decision_time():
    h = _h1()
    sig = h.index[3000:9000]
    st = C.trend_states(h, sig)
    for col in ("end4", "endS", "endD"):
        used = st[col].dropna()
        assert len(used) > 1000
        assert (used <= st.loc[used.index, "decide"]).all(), col
    # equality is reachable: a signal on the last hour of a 4h bin sees that bin (bar t closes exactly when the bin closes)
    hit = st[(st["end4"] == st["decide"])]
    assert len(hit) > 100 and (hit.index.hour % 4 == 0).all()  # last hour of bins 01,05,..: 04,08,12,16,20,00


def test_mid_bin_signal_uses_previous_completed_4h_bar():
    h = _h1()
    b = C.h4_bars(h)
    k = 2000
    t = pd.Timestamp(b.index[k]) + H  # second hour of bin k: bin k is still forming at the decision time t + 1h
    st = C.trend_states(h, pd.DatetimeIndex([t]))
    assert st["end4"].iloc[0] == b["end"].iloc[k - 1]
    assert st["end4"].iloc[0] <= t + H


def test_swing_state_is_causal_and_pivot_needs_confirmation():
    b = C.h4_bars(_h1(900))
    full = C.swing_state(b)
    for j in (500, 1200, 2500, 3500):
        part = C.swing_state(b.iloc[: j + 1])
        assert _same(part, full.iloc[: j + 1])
    # crafted: rising swings -> +1 only after the confirming bars; a pivot at i cannot show before i+2
    hi = [1, 2, 1, 1.5, 1.2, 1.4, 1.1, 1.3, 1.0, 1.2, 1.5, 2.0]
    idx = pd.date_range("2024-01-01", periods=len(hi), freq="4h")
    crafted = pd.DataFrame({"High": hi, "Low": [x - 0.5 for x in hi]}, index=idx)
    s = C.swing_state(crafted)
    assert s.isna().iloc[:3].all()
    cum = C.swing_state(crafted.iloc[:5])
    assert _same(cum, s.iloc[:5])


def test_swing_up_and_down_patterns():
    # zig-zag with rising highs and rising lows
    pts = []
    for m in range(7):  # peaks 4,7,10.. and troughs 0,3,6.. both rising, each pivot wider than k=2
        L = 3 * m
        pts += [L + 2, L + 4, L + 2, L, L + 1]
    hi = np.array([p + 0.3 for p in pts]); lo = np.array([p - 0.3 for p in pts])
    idx = pd.date_range("2024-01-01", periods=len(pts), freq="4h")
    up = C.swing_state(pd.DataFrame({"High": hi, "Low": lo}, index=idx))
    assert up.iloc[-1] == 1.0
    dn = C.swing_state(pd.DataFrame({"High": 100 - lo, "Low": 100 - hi}, index=idx))
    assert dn.iloc[-1] == -1.0


def test_variant_logic_and_partition():
    h = _h1()
    n = len(h)
    rng = np.random.default_rng(1)
    sig_idx = h.index[3000:9000]
    pred = pd.Series(rng.choice([0, 1, 2], len(sig_idx), p=[0.5, 0.25, 0.25]), index=sig_idx)
    conf = pd.Series(rng.uniform(0.6, 0.9, len(sig_idx)), index=sig_idx)
    st = C.trend_states(h, sig_idx)
    s = C.variant_signals(pred, conf, st)
    keep = lambda v: (s[v] != C.HOLD)  # noqa: E731
    assert (keep("H4_AND_D") <= (keep("H4_EMA50") & keep("D_EMA50"))).all() and ((keep("H4_EMA50") & keep("D_EMA50")) <= keep("H4_AND_D")).all()
    assert not (keep("H4_AND_D") & keep("H4_D_DISAGREE")).any()
    assert (keep("H4_D_CONF65") <= keep("H4_AND_D")).all() and (keep("H4_D_CONF65") <= keep("CONF65")).all()
    assert (s["CONF65"][keep("CONF65")] == pred[keep("CONF65")]).all()
    assert (conf[keep("CONF65")] >= 0.65).all()
    for v in ("H4_EMA50", "D_EMA50", "H4_STRUCT", "H4_AND_D"):
        k = keep(v)
        trend = {"H4_EMA50": "t4", "D_EMA50": "td", "H4_STRUCT": "ts"}.get(v)
        if trend:
            ok = ((pred[k] == C.BUY) & (st.loc[k, trend] > 0)) | ((pred[k] == C.SELL) & (st.loc[k, trend] < 0))
            assert ok.all()
    # undefined trend never agrees
    assert not keep("D_EMA50")[st["td"].isna()].any()
    b = C.bucket(pred, st)
    assert set(b.unique()) <= {"both_agree", "mixed", "both_oppose"}
    assert ((b == "both_agree") & (pred != C.HOLD)).sum() == keep("H4_AND_D").sum()


def test_simulate_matches_backtest_and_swap_sign():
    h = _h1(200)
    rng = np.random.default_rng(2)
    sig = pd.Series(rng.choice([0, 0, 0, 1, 2], len(h) - 100), index=h.index[50:-50])
    atr = true_range_atr(h, 14)
    a = _simulate_trades(h, sig, CFG, "EURUSD", atr=atr)
    b = C.simulate(h, sig, CFG, "EURUSD", atr, swap=(-3.51, 1.49))
    assert len(a) == len(b) > 100
    assert (a["entry_time"].values == b["entry_time"].values).all()
    assert np.allclose(a["net_return"].values, b["net_return"].values)
    assert (b["exit_time"] > b["entry_time"]).all()
    crossed = b[b["nights"] > 0]
    assert len(crossed) > 5
    longs, shorts = crossed[crossed["side"] == 1], crossed[crossed["side"] == -1]
    assert (longs["swap_ret"] < 0).all() and (shorts["swap_ret"] > 0).all()
    none = C.simulate(h, sig, CFG, "EURUSD", atr)
    assert np.allclose(none["r"].values, none["r_swap"].values)
    assert np.allclose(b["r"].values, none["r"].values)


def test_pass_rules():
    base = {"pf": 0.95}
    m = {"n": 400, "pf": 1.12, "net_r": 30.0, "max_dd_r": -12.0, "p": 1e-5}
    assert C.is_pass(m, base, 4)["PASS"]
    assert not C.is_pass(dict(m, n=99), base, 4)["PASS"]
    assert not C.is_pass(dict(m, pf=1.0), base, 4)["PASS"]
    assert not C.is_pass(m, {"pf": 1.10}, 4)["PASS"]  # must beat baseline by 0.05
    assert not C.is_pass(dict(m, max_dd_r=-40.0), base, 4)["PASS"]
    assert not C.is_pass(dict(m, p=1e-3), base, 4)["PASS"]
    assert not C.is_pass(m, base, 2)["PASS"]
    assert not C.is_pass(m, base, 5, ctrl={"pf": 1.11})["PASS"]  # (d) must also beat the confidence-only control
    assert C.ho_pass({"n": 40, "pf": 1.1, "net_r": 3.0, "p": 0.001}, {"pf": 0.8}, 2)["PASS"]
    assert not C.ho_pass({"n": 40, "pf": 1.1, "net_r": 3.0, "p": 0.05}, {"pf": 0.8}, 2)["PASS"]


def test_real_history_no_lookahead_eurusd():
    p = ROOT / "data" / "history" / "EURUSD_1h.csv"
    if not p.exists():
        pytest.skip("history not present")
    h = pd.read_csv(p, index_col=0, parse_dates=True)[["Open", "High", "Low", "Close"]].astype(float)
    h = h[~h.index.duplicated(keep="last")].sort_index()
    h = h[(h.index >= "2019-01-01") & (h.index < "2020-12-31")]
    idx = h.index
    for k in (4000, 6000, 8000, 10000):
        t = idx[k]
        full = C.trend_states(h, pd.DatetimeIndex([t]))
        trunc = C.trend_states(h.iloc[: k + 1], pd.DatetimeIndex([t]))
        for col in ("t4", "td", "ts"):
            assert _same(full[col], trunc[col]), (col, t)
