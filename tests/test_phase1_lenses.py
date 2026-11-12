"""Tests for research/phase1. The no-lookahead tests are the critical ones (prereg PREREG_20261112.md, commit 218e3e5)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "phase1"))
import lenses as L  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_phase1", ROOT / "research" / "phase1" / "run_phase1.py")
R = importlib.util.module_from_spec(_spec)
# Importing run_phase1 loads confluence; that is intentional (trial constants live there).
sys.modules["run_phase1"] = R
_spec.loader.exec_module(R)

H = pd.Timedelta(hours=1)


def _h1(n_days=800, seed=5, start="2018-01-01", shock=None):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=24 * n_days, freq="h")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    c = 1.1 + np.cumsum(rng.normal(0, 0.0012, n)) + 0.0003 * np.sin(np.arange(n) / 700.0)
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) + rng.uniform(0, 0.0008, n)
    lo = np.minimum(o, c) - rng.uniform(0, 0.0008, n)
    df = pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": 1.0}, index=idx)
    if shock is not None:
        ts, field, val = shock
        df.loc[ts, field] = val
    return df


def _same(a: pd.Series, b: pd.Series) -> bool:
    a = pd.Series(a)
    b = pd.Series(b)
    if a.dtype == object or b.dtype == object:
        return bool((a.fillna("NA").astype(str).to_numpy() == b.fillna("NA").astype(str).to_numpy()).all())
    return bool(((a == b) | (a.isna() & b.isna())).all())


def test_trial_count_and_alpha():
    assert R.N_TRIALS == 174 + 8 and R.N_TRIALS == 182
    assert len(R.VARIANTS) == 8
    assert R.VARIANTS[:7] == list(L.DIRECTIONAL)
    assert R.VARIANTS[-1] == "BEST_PER_REGIME"
    assert R.ALPHA == pytest.approx(0.05 / 182)
    assert R.N_PRIOR == 174
    assert R.JUDGED_COSTS == {"EURUSD": 1.4, "USDJPY": 2.4}


def test_daily_ohlc_matches_prior_close_and_end():
    M = R.C.M
    df = _h1(n_days=40, seed=1)
    ours = L.daily_ohlc(df)
    prior = M.daily_bars(df)
    # same index, Close, hours, end_utc
    common = ours.index.intersection(prior.index)
    assert len(common) == len(ours) == len(prior)
    pd.testing.assert_series_equal(ours["Close"], prior["Close"], check_names=False)
    pd.testing.assert_series_equal(ours["hours"], prior["hours"], check_names=False)
    pd.testing.assert_series_equal(ours["end_utc"], prior["end_utc"], check_names=False)
    assert (ours["High"] >= ours[["Open", "Close"]].max(axis=1) - 1e-15).all()
    assert (ours["Low"] <= ours[["Open", "Close"]].min(axis=1) + 1e-15).all()


def test_weekly_drops_short_weeks_and_ends_at_last_daily():
    df = _h1(n_days=40, seed=2)
    daily = L.daily_ohlc(df)
    weekly = L.weekly_ohlc(daily)
    assert (weekly["n"] >= 3).all()
    for mon, row in weekly.iterrows():
        days = daily[(daily.index >= mon) & (daily.index < mon + pd.Timedelta(days=7))]
        assert len(days) == row["n"]
        friday = mon + pd.Timedelta(days=4)
        end_ny = friday.tz_localize("America/New_York") + pd.Timedelta(hours=17)
        assert row["end_utc"] == end_ny.tz_convert("UTC").tz_localize(None)
        assert row["Close"] == days["Close"].iloc[-1]


def test_htf_at_used_end_le_decision():
    df = _h1(n_days=60, seed=3)
    daily = L.daily_ohlc(df)
    decide = pd.DatetimeIndex(df.index) + H
    val, used = L.htf_at(pd.DatetimeIndex(daily["end_utc"]), daily["Close"].to_numpy(), decide)
    ok = pd.notna(used)
    assert (used[ok] <= decide[ok]).all()


def test_no_lookahead_truncation_equality():
    """Lens values at a decision equal those computed from history truncated at that decision."""
    df = _h1(n_days=400, seed=4)
    # pick a mid-sample Friday 12:00 UTC decision (bar 11:00 close)
    mid = df.index[len(df) // 2]
    decide = mid + H
    # full
    full = L.lenses_at_decisions(df, pd.DatetimeIndex([mid]))
    # truncated: only bars whose close is at or before decide, i.e. index <= mid (bar mid closes at mid+1h = decide)
    trunc = df.loc[df.index <= mid]
    half = L.lenses_at_decisions(trunc, pd.DatetimeIndex([mid]))
    for name in L.DIRECTIONAL + ["ATR_VOL"]:
        assert _same(full[name], half[name]), name
    assert full["endD"].iloc[0] == half["endD"].iloc[0]
    assert full["endW"].iloc[0] == half["endW"].iloc[0]


def test_forming_daily_bar_shock_changes_nothing():
    """A violent move inside the still-forming daily bar (after D) must not change the lens at D."""
    df = _h1(n_days=400, seed=6)
    # Find a decision in the middle of a NY day, not at the daily close.
    mid = df.index[len(df) // 2]
    decide = mid + H
    full = L.lenses_at_decisions(df, pd.DatetimeIndex([mid]))
    used_end = full["endD"].iloc[0]
    assert pd.notna(used_end)
    # shock a bar that starts at or after decide (still forming / future)
    future = df.index[df.index >= decide]
    assert len(future)
    shock_ts = future[0]
    shocked = df.copy()
    shocked.loc[shock_ts, "High"] = shocked.loc[shock_ts, "High"] + 0.05
    shocked.loc[shock_ts, "Low"] = shocked.loc[shock_ts, "Low"] - 0.05
    shocked.loc[shock_ts, "Close"] = shocked.loc[shock_ts, "Close"] + 0.04
    after = L.lenses_at_decisions(shocked, pd.DatetimeIndex([mid]))
    for name in L.DIRECTIONAL + ["ATR_VOL", "RSI", "SMA200_DIST_ATR"]:
        assert _same(full[name], after[name]), name


def test_swing_unusable_until_confirming_bars_complete():
    """A pivot at i is not usable until bar i+k has completed (k=2)."""
    # Build a short daily frame with a clear swing high at position 5.
    idx = pd.date_range("2020-01-06", periods=16, freq="D")  # Mon start
    close = np.array([1.0, 1.1, 1.2, 1.15, 1.05, 1.30, 1.10, 1.00, 0.95, 0.90, 0.92, 1.00, 1.05, 1.08, 1.02, 0.98])
    high = close + 0.02
    high[5] = 1.40  # unique swing high
    low = close - 0.02
    low[2] = 0.80
    low[9] = 0.70  # two swing lows
    daily = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=idx)
    st = L.swing_state(daily, k=2)
    # pivot at i=5 is a swing high; confirmed when j = i+k = 7
    # until index 7, that high is not in the confirmed list
    # We only require: at bar 6 (before confirmation), the value equals what we'd get without bar 7+.
    early = L.swing_state(daily.iloc[:7], k=2)  # through index 6, i+k=5+2=7 not complete
    # last bar of early is index 6; confirmation for i=5 needs j>=7, so early's last value should ignore the i=5 high
    later = L.swing_state(daily.iloc[:8], k=2)  # through index 7, confirmed
    # The confirmation can change the state at index 7 vs 6; that is expected.
    # Truncation: state at index 6 computed from full data equals state from data ending at 6.
    full = L.swing_state(daily, k=2)
    assert _same(full.iloc[:7], early)
    # After confirmation the full series at index 7 equals the series ending at 7.
    assert _same(full.iloc[:8], later)


def test_sma_stack_logic_and_warmup():
    n = 220
    idx = pd.date_range("2015-01-01", periods=n, freq="D")
    # strictly rising closes -> SMAs stacked up, slope positive after warmup
    close = pd.Series(np.linspace(1.0, 2.0, n), index=idx)
    daily = pd.DataFrame({"Open": close, "High": close + 0.01, "Low": close - 0.01, "Close": close}, index=idx)
    s = L.lens_sma_stack(daily)
    # SMA200 first finite at index 199 (200 bars). slope at 199 uses SMA50[194], which exists.
    assert s.iloc[:199].isna().all()
    assert (s.iloc[199:] == 1.0).all()
    # strictly falling
    close_dn = pd.Series(np.linspace(2.0, 1.0, n), index=idx)
    daily_dn = pd.DataFrame({"Open": close_dn, "High": close_dn + 0.01, "Low": close_dn - 0.01, "Close": close_dn}, index=idx)
    s2 = L.lens_sma_stack(daily_dn)
    assert (s2.iloc[199:] == -1.0).all()


def test_donchian_and_sma200_and_rsi_causal():
    df = _h1(n_days=300, seed=8)
    daily = L.daily_ohlc(df)
    # Donchian, SMA200, RSI at t equal values from daily[:t] (inclusive)
    t = 250
    d_full = {
        "DONCHIAN": L.lens_donchian(daily),
        "SMA200": L.lens_sma200(daily),
        "RSI": L.lens_rsi_regime(daily),
        "ATR": L.lens_atr_vol(daily),
        "ADX": L.lens_adx(daily),
    }
    d_cut = {
        "DONCHIAN": L.lens_donchian(daily.iloc[: t + 1]),
        "SMA200": L.lens_sma200(daily.iloc[: t + 1]),
        "RSI": L.lens_rsi_regime(daily.iloc[: t + 1]),
        "ATR": L.lens_atr_vol(daily.iloc[: t + 1]),
        "ADX": L.lens_adx(daily.iloc[: t + 1]),
    }
    for name in d_full:
        assert _same(d_full[name].iloc[: t + 1], d_cut[name]), name


def test_weekly_lens_causal_and_alignment():
    df = _h1(n_days=400, seed=9)
    daily = L.daily_ohlc(df)
    weekly = L.weekly_ohlc(daily)
    trend = L.lens_weekly(weekly)
    # first 9 weeks undefined (need 10)
    assert trend.iloc[:9].isna().all()
    assert trend.iloc[9:].notna().all()
    # mid decision: used weekly end <= decide
    mid = df.index[len(df) // 2]
    st = L.lenses_at_decisions(df, pd.DatetimeIndex([mid]))
    assert pd.isna(st["endW"].iloc[0]) or st["endW"].iloc[0] <= mid + H


def test_atr_vol_unknown_until_100_and_inclusive_median():
    n = 130
    idx = pd.date_range("2016-01-01", periods=n, freq="D")
    # constant TR -> ATR constant once warmed, median defined at 100 ATR values
    close = pd.Series(1.0 + np.arange(n) * 0.0, index=idx)
    high = close + 0.01
    low = close - 0.01
    daily = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=idx)
    atr = L.wilder_atr(daily, 14)
    # TR[0] is High-Low (no previous close); first 14 TRs at indices 0..13, first ATR at 13.
    assert atr.iloc[:13].isna().all()
    assert atr.iloc[13:].notna().all()
    reg = L.lens_atr_vol(daily)
    # median needs 100 ATR values: first at index 13+99=112
    assert (reg.iloc[:112] == "unknown").all()
    assert set(reg.iloc[112:].unique()) <= {"low", "high"}
    # equal ATR vs median -> low (prereg: high only if ATR > median)
    assert (reg.iloc[112:] == "low").all()


def test_agree_zero_and_nan_never_agree():
    idx = pd.date_range("2020-01-01", periods=4, freq="h")
    pred = pd.Series([R.BUY, R.SELL, R.BUY, R.SELL], index=idx)
    trend = pd.Series([0.0, 0.0, np.nan, np.nan], index=idx)
    a = L.agrees(pred, trend, R.BUY, R.SELL)
    assert not a.any()
    trend2 = pd.Series([1.0, -1.0, 1.0, -1.0], index=idx)
    a2 = L.agrees(pred, trend2, R.BUY, R.SELL)
    assert a2.all()
    # opposite
    trend3 = pd.Series([-1.0, 1.0, -1.0, 1.0], index=idx)
    assert not L.agrees(pred, trend3, R.BUY, R.SELL).any()


def test_best_per_regime_selection_rules():
    """30-trade minimum, mean R must beat baseline, ties keep earlier lens, unknown is no-filter."""
    rng = np.random.default_rng(0)

    def trades(n, mean, start, regime="low"):
        et = pd.date_range(start, periods=n, freq="D")
        r = rng.normal(mean, 0.05, n)
        return pd.DataFrame({"entry_time": et, "r": r, "regime": regime})

    # Before 2017: baseline low-vol mean ~0.0; ADX ~0.2 with 40 trades; SMA_STACK ~0.1 with 40 trades
    baseline = pd.concat(
        [trades(40, 0.00, "2016-01-01", "low"), trades(40, 0.00, "2016-01-01", "high")],
        ignore_index=True,
    )
    by = {
        "SMA_STACK": pd.concat([trades(40, 0.10, "2016-01-01", "low"), trades(40, 0.00, "2016-01-01", "high")], ignore_index=True),
        "ADX": pd.concat([trades(40, 0.20, "2016-01-01", "low"), trades(10, 0.50, "2016-01-01", "high")], ignore_index=True),  # high underpowered
        "DONCHIAN": pd.concat([trades(40, 0.20, "2016-01-01", "low"), trades(40, -0.10, "2016-01-01", "high")], ignore_index=True),  # tie with ADX on low
        "SWING": trades(40, -0.05, "2016-01-01", "low"),
        "RSI_REGIME": trades(40, 0.01, "2016-01-01", "low"),
        "SMA200": trades(40, 0.01, "2016-01-01", "low"),
        "WEEKLY": trades(40, 0.01, "2016-01-01", "low"),
    }
    picks = R.select_for_year(2017, baseline, by)
    # low: ADX and DONCHIAN both mean ~0.20, ADX comes first in DIRECTIONAL? ADX is before DONCHIAN, both > baseline.
    # They're random around 0.20 so not an exact tie. We test exact tie separately.
    assert picks["unknown"] == "no-filter"
    # high: ADX has only 10 trades, DONCHIAN mean -0.10 is not > baseline 0, others missing or not better -> no-filter
    assert picks["high"] == "no-filter"

    # exact tie: same r series for two lenses; earlier in DIRECTIONAL wins
    same = trades(40, 0.15, "2016-01-01", "low")
    by2 = {name: trades(5, 0.0, "2016-01-01", "low") for name in L.DIRECTIONAL}
    by2["SMA_STACK"] = same.copy()
    by2["ADX"] = same.copy()
    base = trades(40, 0.0, "2016-01-01", "low")
    p2 = R.select_for_year(2017, base, by2)
    assert p2["low"] == "SMA_STACK"  # earlier in the frozen order

    # best not strictly greater than baseline -> no-filter
    by3 = {name: trades(40, 0.0, "2016-01-01", "low") for name in L.DIRECTIONAL}
    p3 = R.select_for_year(2017, trades(40, 0.0, "2016-01-01", "low"), by3)
    assert p3["low"] == "no-filter"

    # training uses only entries before Y (2018-01-01): a great 2018 trade must not be used for 2018 selection
    early = trades(40, 0.0, "2016-01-01", "low")
    late = trades(40, 1.0, "2018-06-01", "low")
    by4 = {name: early.copy() for name in L.DIRECTIONAL}
    by4["WEEKLY"] = pd.concat([early, late], ignore_index=True)
    p4 = R.select_for_year(2018, pd.concat([early, late], ignore_index=True), by4)
    assert p4["low"] == "no-filter"


def test_best_signals_year_and_holdout_freeze():
    idx = pd.date_range("2015-06-01", periods=8, freq="YS")  # 2015..2022 year starts, naive
    # Use specific timestamps: 2015, 2016, 2025, 2026
    times = pd.DatetimeIndex(
        [
            "2015-06-01 10:00",
            "2016-06-01 10:00",
            "2025-03-01 10:00",
            "2026-03-01 10:00",
        ]
    )
    pred = pd.Series([R.BUY] * 4, index=times)
    st = pd.DataFrame(
        {
            "ATR_VOL": ["low", "low", "high", "high"],
            "ADX": [1.0, -1.0, 1.0, 1.0],  # 2016 BUY disagrees with ADX -1
            "SMA_STACK": [1.0, 1.0, -1.0, -1.0],  # 2025/26 BUY disagrees with SMA_STACK
            "DONCHIAN": [1, 1, 1, 1],
            "SWING": [1, 1, 1, 1],
            "RSI_REGIME": [1, 1, 1, 1],
            "SMA200": [1, 1, 1, 1],
            "WEEKLY": [1, 1, 1, 1],
        },
        index=times,
    )
    picks = {
        2016: {"low": "ADX", "high": "no-filter", "unknown": "no-filter"},
        2025: {"low": "no-filter", "high": "SMA_STACK", "unknown": "no-filter"},
    }
    # fill other years unused
    for y in range(2017, 2025):
        picks[y] = {"low": "no-filter", "high": "no-filter", "unknown": "no-filter"}
    out = R.best_signals(pred, st, picks)
    assert int(out.iloc[0]) == R.BUY  # 2015 unfiltered
    assert int(out.iloc[1]) == R.HOLD  # 2016 low -> ADX disagrees
    assert int(out.iloc[2]) == R.HOLD  # 2025 high -> SMA_STACK disagrees
    assert int(out.iloc[3]) == R.HOLD  # 2026 uses frozen 2025 picks


def test_pass_rules_thin_and_underpowered():
    base = {"pf": 0.94, "net_r": -10.0, "n": 2000, "mean_r": -0.02, "max_dd_r": -20.0, "p": 0.9, "win": 0.5}
    # underpowered
    m = {"n": 99, "pf": 1.5, "net_r": 20.0, "mean_r": 0.2, "max_dd_r": -5.0, "p": 1e-6, "win": 0.6}
    c = R.is_pass(m, base, 5)
    assert c["underpowered"] and not c["PASS"]
    # thin but otherwise passing numbers
    m2 = {"n": 150, "pf": 1.20, "net_r": 20.0, "mean_r": 0.13, "max_dd_r": -10.0, "p": 1e-6, "win": 0.55}
    c2 = R.is_pass(m2, base, 4)
    assert c2["thin"] and c2["PASS"]
    # PF not high enough vs baseline
    m3 = dict(m2)
    m3["pf"] = 0.98
    assert not R.is_pass(m3, base, 4)["PASS"]


def test_wilder_smooth_matches_hand_calculation():
    s = pd.Series([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = L.wilder_smooth(s, 3)
    # first 3 finite at indices 1,2,3: mean=2.0 at index 3
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1]) and np.isnan(out.iloc[2])
    assert out.iloc[3] == pytest.approx(2.0)
    # next: 2 - 2/3 + 5 = 6.333...? wait x[4]=5: 2 - 2/3 + 5 = 6.333 yes
    # x = [nan, 1, 2, 3, 4, 5, 6]; Wilder is prev - prev/n + x/n
    assert out.iloc[4] == pytest.approx(2.0 - 2.0 / 3.0 + 4.0 / 3.0)
    assert out.iloc[5] == pytest.approx(out.iloc[4] - out.iloc[4] / 3.0 + 5.0 / 3.0)
    assert out.iloc[6] == pytest.approx(out.iloc[5] - out.iloc[5] / 3.0 + 6.0 / 3.0)
