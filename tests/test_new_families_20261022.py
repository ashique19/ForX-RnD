"""No-lookahead and mechanics tests for research/new_families/new_families_20261022.py.

The OHLC fixture is a seeded random walk used only to exercise code paths; no result is computed from it.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("nf", ROOT / "research" / "new_families" / "new_families_20261022.py")
nf = importlib.util.module_from_spec(spec); sys.modules["nf"] = nf; spec.loader.exec_module(nf)


def _df(n=3000, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-06 00:00", periods=n, freq="h")
    c = 1.1 + np.cumsum(rng.normal(0, 0.0008, n))
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) + rng.uniform(0, 0.0006, n)
    lo = np.minimum(o, c) - rng.uniform(0, 0.0006, n)
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": 1.0}, index=idx)


def _check_causal(fn, df, picks=60):
    atr = nf.true_range_atr(df, 14)
    side_full, a_full = fn(df, atr)
    rng = np.random.default_rng(1)
    ts_list = list(rng.choice(df.index[600:], size=picks, replace=False)) + list(side_full.index[side_full.index > df.index[600]][:40])
    for ts in ts_list:
        cut = df.loc[:ts]
        s_cut, a_cut = fn(cut, nf.true_range_atr(cut, 14))
        assert (ts in s_cut.index) == (ts in side_full.index), ts
        if ts in side_full.index:
            assert s_cut[ts] == side_full[ts] and a_cut[ts] == pytest.approx(a_full[ts])
    return side_full


@pytest.mark.parametrize("sess,k", [("LDN", 2.0), ("NY", 3.0)])
def test_a_no_lookahead_and_hour(sess, k):
    df = _df()
    s = _check_causal(lambda d, a: nf.sig_a(d, a, sess, k), df)
    assert len(s) and set(s.index.hour) == {nf.SESS_HOUR[sess]} and set(s.index.dayofweek) <= {0, 1, 2, 3, 4}


@pytest.mark.parametrize("N,q", [(12, 0.2), (24, 0.1)])
def test_b_no_lookahead_and_session(N, q):
    df = _df()
    s = _check_causal(lambda d, a: nf.sig_b(d, a, N, q), df)
    assert len(s) and s.index.hour.min() >= 7 and s.index.hour.max() <= 15


def test_c_no_lookahead_uses_given_events_only():
    df = _df()
    ev = pd.DataFrame({"title": "X", "currency": "USD",
                       "when": pd.to_datetime(["2020-02-07 13:30", "2020-03-06 13:30", "2020-03-18 18:00", "2020-04-03 13:30"])})
    s = _check_causal(lambda d, a: nf.sig_c(d, a, "EURUSD", ev, "cont", 0.5), df)
    assert set(s.index) <= set(ev["when"].dt.floor("h"))
    assert len(nf.sig_c(df, nf.true_range_atr(df, 14), "EURGBP", ev, "cont", 0.5)[0]) == 0  # no USD in pair


def test_entry_next_open_and_cost():
    df = _df(800)
    atr = nf.true_range_atr(df, 14)
    ts = df.index[100]
    t = nf.simulate(df, pd.Series({ts: 1}), pd.Series({ts: float(atr[ts])}), "EURUSD", 1.4, (1.0, 2.0, 12))
    assert t["entry_time"].iloc[0] == df.index[101] and t["entry"].iloc[0] == df["Open"].iloc[101]
    assert t["net_return"].iloc[0] == pytest.approx(t["raw_return"].iloc[0] - 1.4e-4 / t["entry"].iloc[0])


def test_cooldown_blocks_k_bars_after_stop():
    df = _df(800)
    atr = nf.true_range_atr(df, 14)
    sig = pd.Series(1, index=df.index[50:400]); au = atr.reindex(sig.index)
    base = nf.simulate(df, sig, au, "EURUSD", 1.4, (2.0, 2.0, 8))
    cd = nf.simulate(df, sig, au, "EURUSD", 1.4, (2.0, 2.0, 8), cooldown=24)
    pos = pd.Series(np.arange(len(df)), index=df.index)
    for prev, nxt in zip(cd.iloc[:-1].itertuples(), cd.iloc[1:].itertuples()):
        if prev.exit_reason in ("sl", "sl_conflict"):
            assert pos[nxt.entry_time] >= pos[prev.exit_bar_time] + 1 + 24
    assert len(cd) <= len(base)


def test_max_entry_cut():
    df = _df(800)
    atr = nf.true_range_atr(df, 14)
    sig = pd.Series(1, index=df.index[50:700]); au = atr.reindex(sig.index)
    t = nf.simulate(df, sig, au, "EURUSD", 1.4, (1.0, 1.0, 6), max_entry=df.index[300])
    assert (t["entry_time"] < df.index[300]).all()
