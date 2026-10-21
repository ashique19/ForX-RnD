"""No-lookahead / mechanics tests for research/lowfreq/lowfreq_20261022.py.

The hourly OHLC fixture is a seeded random walk (test-only, never used for results) on an FX-week calendar (Sun 22:00 - Fri 21:00 UTC).
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lf", ROOT / "research" / "lowfreq" / "lowfreq_20261022.py")
lf = importlib.util.module_from_spec(spec); sys.modules["lf"] = lf; spec.loader.exec_module(lf)


def _hourly(weeks=120, seed=3):
    idx = pd.date_range("2019-01-06 22:00", periods=weeks * 7 * 24, freq="h")
    dow, hr = idx.dayofweek, idx.hour
    keep = ~((dow == 5) | ((dow == 6) & (hr < 22)) | ((dow == 4) & (hr > 21)))
    idx = idx[keep]
    rng = np.random.default_rng(seed)
    c = 1.1 * np.exp(np.cumsum(rng.normal(0, 0.0012, len(idx))))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * (1 + rng.uniform(0, 4e-4, len(idx))),
                         "Low": np.minimum(o, c) * (1 - rng.uniform(0, 4e-4, len(idx))), "Close": c}, index=idx)


def test_daily_boundary_and_no_weekend_bars():
    d = lf.resample(_hourly(10), "D")
    assert set(d.index.dayofweek) <= {0, 1, 2, 3, 4}
    # Mon-Thu bars close at 21:00 UTC; Friday bar includes the 21:00 stub hour (closes 22:00)
    mt = d[d.index.dayofweek < 4]
    assert set(mt.index.hour) == {21}
    assert set(d[d.index.dayofweek == 4].index.hour) == {22}
    assert all(o < c for o, c in zip(d["open_time"], d.index))


def test_4h_bins():
    b = lf.resample(_hourly(10), "4h")
    assert set(b.index.hour) <= {1, 5, 9, 13, 17, 21, 22, 23}
    assert ((b.index - b["open_time"]) <= pd.Timedelta(hours=6)).all()


@pytest.mark.parametrize("tf", ["4h", "D"])
def test_resample_causal(tf):
    h = _hourly(60)
    full = lf.resample(h, tf)
    rng = np.random.default_rng(0)
    for cut in rng.choice(np.arange(500, len(h)), 25, replace=False):
        hc = h.iloc[:cut]
        part = lf.resample(hc, tf)
        assert part.index.max() <= hc.index[-1] + pd.Timedelta(hours=1)  # only bars already closed
        common = part.index
        pd.testing.assert_frame_equal(part, full.loc[common])


@pytest.mark.parametrize("tf,q", [("4h", 0.2), ("D", 0.2)])
def test_f1_signal_causal(tf, q):
    h = _hourly(150)
    b = lf.resample(h, tf); a = lf.true_range_atr(b, 14)
    s = lf.sig_f1(b, a, q)
    rng = np.random.default_rng(1)
    picks = list(s.index[-15:]) + list(rng.choice(b.index[300:], 20, replace=False))
    for t in picks:
        bc = b.loc[:t]
        sc = lf.sig_f1(bc, lf.true_range_atr(bc, 14), q)
        assert (t in sc.index) == (t in s.index) and (t not in s.index or sc[t] == s[t])


def test_f3_signal_causal():
    b = lf.resample(_hourly(80), "D")
    s = lf.sig_f3(b, 20)
    for t in b.index[30::17]:
        assert lf.sig_f3(b.loc[:t], 20)[t] == s[t]


def test_carry_targets_causal_and_lagged(tmp_path):
    h = _hourly(40)
    daily = {"EURUSD": lf.resample(h, "D"), "USDJPY": lf.resample(h * 100, "D")}
    idx = pd.date_range("2018-12-01", "2020-06-01", freq="D")
    rates = pd.DataFrame({"EUR": 0.0, "USD": 2.0, "JPY": -0.1}, index=idx)
    jump = pd.Timestamp("2019-06-07")  # a Friday: USD jumps on the decision day itself
    rates.loc[jump:, "USD"] = -5.0
    tg = lf.carry_targets(daily, rates, k=1)
    t = [x for x in tg["EURUSD"].index if x.normalize() == jump]
    # decision on the jump day must still use the previous day's rate (USD 2.0: EURUSD short wins |d|=2 vs USDJPY 2.1 -> USDJPY long)
    if t:
        assert tg["USDJPY"][t[0]] == 1 and tg["EURUSD"][t[0]] == 0
    cut = {p: d.loc[: pd.Timestamp("2019-05-01")] for p, d in daily.items()}
    tg_cut = lf.carry_targets(cut, rates, k=1)
    for p in daily:
        common = tg_cut[p].index
        assert (tg_cut[p] == tg[p].loc[common]).all()


def test_rollovers_and_financing_sign():
    t0, t1 = pd.Timestamp("2020-01-06 21:00"), pd.Timestamp("2020-01-10 21:00")  # Mon open -> Fri close
    ro = lf.rollovers(t0, t1)
    assert list(ro.dayofweek) == [1, 2, 3]  # Tue, Wed, Thu 21:00 strictly inside
    idx = pd.date_range("2019-12-01", "2020-02-01", freq="D")
    rates = pd.DataFrame({"AUD": 1.0, "JPY": 0.0}, index=idx)
    acc, mk, n = lf.financing("AUDJPY", 1, 80.0, t0, t1, rates)
    assert n == 3 and acc == pytest.approx(3 * 0.01 / 365) and mk == pytest.approx(3 * 0.01 / 80.0)
    acc_s, _, _ = lf.financing("AUDJPY", -1, 80.0, t0, t1, rates)
    assert acc_s == pytest.approx(-acc)


def test_position_sim_next_open_and_cost():
    b = lf.resample(_hourly(30), "D"); a = lf.true_range_atr(b, 14)
    idx = pd.date_range("2018-12-01", "2020-01-01", freq="D")
    rates = pd.DataFrame({"EUR": 0.0, "USD": 0.0}, index=idx)
    tgt = pd.Series([1, 1, -1], index=b.index[20:23])
    t = lf.sim_position(b, a, tgt, "EURUSD", 1.4, rates)
    assert t["entry_time"].iloc[0] == b["open_time"].iloc[21] and t["entry"].iloc[0] == b["Open"].iloc[21]
    assert t["exit"].iloc[0] == b["Open"].iloc[23] and t["side"].tolist()[:2] == [1, -1]
