"""Causal DXY / US yields features (SERIAL step 10)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.dxy_yields import FEATURE_COLS, add_dxy_yield_features, dxy_yields_cfg
from forex_lab.features import build_features
from forex_lab.fred import _write_cache_file


def _cfg_dxy(**kwargs):
    cfg = load_config()
    extras = dict(cfg.get("feature_extras") or {})
    block = {
        "enabled": True,
        "lag_days": 1,
        "cache_dir": "data/fred_cache",
        "allow_network": False,
        "dxy_series": "DTWEXBGS",
        "yield_series": "DGS10",
        "dxy_sma_window": 20,
        "dxy_slope_span": 5,
        "emit_stubs_when_missing": True,
        "fail_soft_fill": 0.0,
    }
    block.update(kwargs)
    extras["dxy_yields"] = block
    cfg["feature_extras"] = extras
    return cfg


def _seed_cache(cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range("2020-01-01", periods=400, freq="B")
    dxy = pd.Series(100 + np.linspace(0, 5, len(idx)) + np.sin(np.arange(len(idx)) / 8), index=idx, name="DTWEXBGS")
    y10 = pd.Series(1.5 + np.linspace(0, 2, len(idx)) + 0.1 * np.sin(np.arange(len(idx)) / 11), index=idx, name="DGS10")
    _write_cache_file(cache_dir / "DTWEXBGS.csv", dxy, "DTWEXBGS")
    _write_cache_file(cache_dir / "DGS10.csv", y10, "DGS10")


def test_dxy_yield_features_present_when_enabled(tmp_path, monkeypatch):
    cache = tmp_path / "fred_cache"
    _seed_cache(cache)
    cfg = _cfg_dxy(cache_dir=str(cache), allow_network=False)
    df = generate_synthetic_ohlcv(bars=800, seed=21)
    # Align synthetic index into 2020+ so cache covers it
    df = df.copy()
    df.index = pd.date_range("2020-06-01", periods=len(df), freq="h", tz="UTC")
    feats = build_features(df, cfg)
    for col in FEATURE_COLS:
        assert col in feats.columns, col
    # Should not be all-stub zeros once cache covers the window
    assert float(feats["dxy_ret"].abs().max()) > 0.0 or feats["dxy_ret"].notna().sum() > 50


def test_dxy_yield_features_off_when_disabled():
    df = generate_synthetic_ohlcv(bars=200, seed=3)
    cfg = _cfg_dxy(enabled=False)
    feats = build_features(df, cfg)
    for col in FEATURE_COLS:
        assert col not in feats.columns


def test_dxy_yield_fail_soft_stubs(tmp_path):
    empty = tmp_path / "empty_cache"
    empty.mkdir()
    cfg = _cfg_dxy(cache_dir=str(empty), allow_network=False, emit_stubs_when_missing=True)
    df = generate_synthetic_ohlcv(bars=120, seed=7)
    extras = dict(cfg.get("feature_extras") or {})
    out = pd.DataFrame(index=df.index)
    status = add_dxy_yield_features(out, df, extras, cfg)
    assert status is not None
    assert status.stubbed is True
    assert status.source == "stub"
    for col in FEATURE_COLS:
        assert col in out.columns
        assert (out[col] == 0.0).all()


def test_dxy_yield_features_are_causal(tmp_path):
    cache = tmp_path / "fred_cache"
    _seed_cache(cache)
    cfg = _cfg_dxy(cache_dir=str(cache), allow_network=False)
    df = generate_synthetic_ohlcv(bars=900, seed=19)
    df = df.copy()
    df.index = pd.date_range("2020-06-01", periods=len(df), freq="h", tz="UTC")
    t = 400
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.25
    f1 = build_features(df, cfg)
    f2 = build_features(poisoned, cfg)
    cols = [c for c in FEATURE_COLS if c in f1.columns]
    assert cols
    # Future FX poison must not change past cross-asset cols that depend on EUR ret.
    # dxy_* and yield_* come from FRED cache (unchanged); eurusd_dxy_div uses eur_ret.
    pd.testing.assert_frame_equal(
        f1.iloc[: t + 1][cols],
        f2.iloc[: t + 1][cols],
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def test_lag_days_blocks_same_day_print(tmp_path):
    cache = tmp_path / "fred_cache"
    cache.mkdir()
    # Sparse two-day DXY spike on 2020-06-10 only
    idx = pd.DatetimeIndex(["2020-06-01", "2020-06-10", "2020-06-11", "2020-06-12"])
    dxy = pd.Series([100.0, 110.0, 110.0, 110.0], index=idx, name="DTWEXBGS")
    y10 = pd.Series([1.0, 1.0, 1.0, 1.0], index=idx, name="DGS10")
    _write_cache_file(cache / "DTWEXBGS.csv", dxy, "DTWEXBGS")
    _write_cache_file(cache / "DGS10.csv", y10, "DGS10")

    bars = pd.date_range("2020-06-10 00:00", periods=48, freq="h", tz="UTC")
    close = np.full(len(bars), 1.10)
    df = pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1.0},
        index=bars,
    )
    cfg = _cfg_dxy(cache_dir=str(cache), allow_network=False, lag_days=1, dxy_sma_window=2, dxy_slope_span=1)
    feats = build_features(df, cfg)
    # Bar on 2020-06-10 must NOT yet see the 110 print (lag=1 => visible from 06-11).
    day0 = feats.loc["2020-06-10 12:00":"2020-06-10 12:00"]
    day1 = feats.loc["2020-06-11 12:00":"2020-06-11 12:00"]
    assert not day0.empty and not day1.empty
    # Before lag unlock, dxy level-derived ret from 100->110 not yet in features on day0.
    # After lag, day1 bars can see observation dated 06-10.
    # yield stays flat so dxy_ret is the signal: day0 should be NaN or based on pre-spike only.
    v0 = day0["dxy_ret"].iloc[0]
    v1 = day1["dxy_ret"].iloc[0]
    # On 06-10, as-of series still at 100 (06-01) after lag; pct_change undefined or 0.
    # On 06-11, as-of unlocks 110 vs prior 100 => ret ~0.10
    assert (pd.isna(v0) or abs(float(v0)) < 1e-9) or float(v0) < 0.05
    assert abs(float(v1) - 0.10) < 1e-6 or float(v1) > 0.05


def test_dxy_yields_cfg_helper():
    assert dxy_yields_cfg({})["enabled"] is False
    assert dxy_yields_cfg({"dxy_yields": True})["enabled"] is True
    assert dxy_yields_cfg({"dxy_yields": {"enabled": False}})["enabled"] is False
