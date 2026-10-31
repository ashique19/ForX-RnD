"""No-lookahead and rule tests for research/new_families/round_20261031.py (prereg PREREG_20261031.md)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("round_20261031", ROOT / "research" / "new_families" / "round_20261031.py")
rd = importlib.util.module_from_spec(_spec)
sys.modules["round_20261031"] = rd
_spec.loader.exec_module(rd)


def _daily(n=900, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n) + pd.Timedelta(hours=21)
    c = 1.1 + np.cumsum(rng.normal(0, 0.004, n)) + 0.0004 * np.arange(n) * np.sin(np.arange(n) / 150)
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 0.003, n)
    l = np.minimum(o, c) - rng.uniform(0, 0.003, n)
    d = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)
    d["open_time"] = idx - pd.Timedelta(hours=24)
    return d


def test_trial_accounting():
    assert rd.N_TRIALS == 156 + 11 == 167 and len(rd.VARIANTS) == 11
    assert rd.ALPHA == pytest.approx(0.05 / 167)


def test_weekly_filter_is_causal():
    d = _daily()
    for k in range(300, 880, 37):
        t = d.index[k]
        full = rd.weekly_trend_at(d, pd.DatetimeIndex([t])).iloc[0]
        trunc = rd.weekly_trend_at(d.iloc[: k + 1], pd.DatetimeIndex([t])).iloc[0]
        assert (np.isnan(full) and np.isnan(trunc)) or full == trunc


def test_weekly_filter_ignores_the_week_in_progress_and_friday_uses_previous_week():
    d = _daily(300)
    times = d.index
    wt = rd.weekly_trend_at(d, times)
    tab = rd.weekly_trend_table(d)
    for t in times[100:140]:
        prior = tab[tab.index < t]
        expect = prior.iloc[-1] if len(prior) else np.nan
        got = wt.loc[t]
        assert (np.isnan(expect) and np.isnan(got)) or expect == got
    fri = [t for t in times[100:] if t.dayofweek == 4][0]
    this_week_done = tab[tab.index == fri]
    assert len(this_week_done) == 1  # the Friday bar completes its week at the same instant ...
    assert tab[tab.index < fri].index.max() < fri  # ... and is excluded (strict), so Friday uses the previous week


def test_weekly_needs_ten_completed_weeks():
    d = _daily(60)
    wt = rd.weekly_trend_at(d, d.index)
    assert wt.iloc[:60].isna().sum() >= 50 - 5 and wt.iloc[:40].isna().all()


@pytest.mark.parametrize("fast,slow", [(None, 200), (50, 200)])
def test_sma_targets_are_causal(fast, slow):
    d = _daily()
    full = rd.target_sma_cross(d, fast, slow)
    for k in (300, 450, 600, 800):
        part = rd.target_sma_cross(d.iloc[: k + 1], fast, slow)
        assert part.equals(full.loc[: d.index[k]])


def test_donchian_is_causal_and_rules():
    d = _daily()
    full = rd.target_donchian(d)
    for k in (300, 500, 800):
        part = rd.target_donchian(d.iloc[: k + 1])
        assert part.equals(full.loc[: d.index[k]])
    idx = pd.bdate_range("2020-01-01", periods=140) + pd.Timedelta(hours=21)
    c = np.r_[np.full(70, 1.0), np.linspace(1.0, 1.3, 30), np.linspace(1.3, 0.7, 40)]
    x = pd.DataFrame({"Open": c, "High": c + 0.001, "Low": c - 0.001, "Close": c}, index=idx)
    tg = rd.target_donchian(x)
    assert tg.iloc[0] == 1  # breakout above the 55-day high enters long
    assert tg.iloc[1] in (0, -1)  # then exits (or reverses) on the way down
    seq = list(tg.values)
    assert all(a != b for a, b in zip(seq, seq[1:]))  # changes only


def test_donchian_exit_bar_can_reverse():
    idx = pd.bdate_range("2020-01-01", periods=120) + pd.Timedelta(hours=21)
    c = np.r_[np.full(60, 1.0), [1.05], np.full(30, 1.05), [0.5], np.full(28, 0.5)]
    x = pd.DataFrame({"Open": c, "High": c + 0.001, "Low": c - 0.001, "Close": c}, index=idx)
    tg = rd.target_donchian(x)
    assert list(tg.values) == [1, -1]  # long on the break, then the crash exits and reverses on the same bar


def test_folds_and_pass_rules():
    t = pd.DataFrame({"entry_time": pd.to_datetime(["2016-06-01", "2018-06-01", "2020-06-01", "2022-06-01", "2024-06-01"]), "r": [1.0, 1.0, 1.0, -1.0, -1.0]})
    assert rd.folds_positive(t) == 3
    m = {"n": 20, "pf": 1.5, "net_r": 10.0, "max_dd_r": -3.0, "p_trade": 1e-5}
    assert rd.is_pass(m, "LT1", 3) and not rd.is_pass(m, "LT1", 2)
    assert not rd.is_pass(dict(m, n=14), "LT1", 5)
    assert not rd.is_pass(dict(m, max_dd_r=-25.0, net_r=30.0), "LT1", 5)  # 20R cap
    assert not rd.is_pass(dict(m, p_trade=1e-3), "LT1", 5)


def test_conservative_cost_has_no_missing_pair_and_equals_duka():
    from forex_lab import cost_model as cm

    rt, miss = cm.cost_series(rd.PAIRS, "mt5_measured")
    assert miss == [] and rt["EURUSD"] == 1.4 and rt["USDJPY"] == 2.4
    duka, _ = cm.cost_series(rd.PAIRS, "duka_p90")
    assert rt == duka