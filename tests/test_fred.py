"""FRED as-of alignment and fail-soft tests (no network)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.features import build_features, make_dataset
from forex_lab.fred import (
    FredStatus,
    _series_ids,
    align_fred_asof,
    daily_fred_transforms,
    fred_cfg,
    fred_feed_status,
)
from forex_lab.ui.health import build_health_rows


def _write_series(path, sid: str, dates, values):
    path.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({sid: values}, index=pd.to_datetime(dates))
    frame.index.name = "observation_date"
    frame.to_csv(path / f"{sid}.csv")


def _fred_cfg(tmp_path, **pack):
    cfg = load_config()
    extra = dict(cfg.get("feature_extras") or {})
    extra["pandas_ta"] = {"enabled": False}
    extra["fred"] = {
        "enabled": True,
        "allow_network": False,
        "lag_days": 1,
        "z_window": 5,
        "cache_dir": str(tmp_path / "fred_cache"),
        "cache_ttl_s": 10**9,
        "series": ["DFF", "DGS10"],
        **pack,
    }
    cfg["feature_extras"] = extra
    return cfg


def test_align_fred_asof_hides_same_day_print():
    daily = pd.DataFrame(
        {"DFF": [1.0, 2.0, 3.0]},
        index=pd.to_datetime(["2024-01-10", "2024-01-11", "2024-01-12"]),
    )
    bars = pd.date_range("2024-01-10 00:00", periods=72, freq="h")
    aligned = align_fred_asof(daily, bars, lag_days=1)
    assert pd.isna(aligned.loc[pd.Timestamp("2024-01-10 15:00"), "DFF"])
    assert aligned.loc[pd.Timestamp("2024-01-11 00:00"), "DFF"] == 1.0
    assert aligned.loc[pd.Timestamp("2024-01-11 23:00"), "DFF"] == 1.0
    assert aligned.loc[pd.Timestamp("2024-01-12 00:00"), "DFF"] == 2.0
    assert aligned.loc[pd.Timestamp("2024-01-12 12:00"), "DFF"] == 2.0
    assert aligned.loc[pd.Timestamp("2024-01-13 00:00"), "DFF"] == 3.0


def test_fred_transforms_are_causal_on_daily_then_lagged():
    daily = pd.Series(
        [1.0, 2.0, 4.0, 7.0, 11.0, 16.0],
        index=pd.date_range("2024-01-01", periods=6, freq="D"),
        name="DFF",
    )
    trans = daily_fred_transforms(pd.DataFrame({"DFF": daily}), z_window=3)
    # chg1 at 2024-01-04 uses 7-4 = 3, not a future print
    assert trans.loc[pd.Timestamp("2024-01-04"), "fred_DFF_chg1"] == 3.0
    bars = pd.date_range("2024-01-01", periods=8, freq="D")
    aligned = align_fred_asof(trans[["fred_DFF_chg1"]], bars, lag_days=1)
    # 2024-01-04 00:00 sees chg1 from obs 2024-01-03 (available 2024-01-04) = 4-2=2
    assert aligned.loc[pd.Timestamp("2024-01-04"), "fred_DFF_chg1"] == 2.0


def test_own_fx_series_skipped_for_pair():
    cfg = {"series": ["DEXUSEU", "DFF", "DTWEXBGS"]}
    ids = _series_ids(cfg, pair="EURUSD")
    assert "DEXUSEU" not in ids
    assert ids == ["DFF", "DTWEXBGS"]


def test_fred_pack_skipped_when_cache_missing(tmp_path):
    df = generate_synthetic_ohlcv(bars=80, seed=2, start="2024-01-01")
    cfg = _fred_cfg(tmp_path)
    feats = build_features(df, cfg, pair="EURUSD")
    assert not any(c.startswith("fred_") for c in feats.columns)


def test_fred_features_from_cache_and_future_revision_is_causal(tmp_path):
    cache = tmp_path / "fred_cache"
    dates = pd.date_range("2022-01-01", periods=40, freq="D")
    _write_series(cache, "DFF", dates, np.linspace(0.1, 0.5, len(dates)))
    _write_series(cache, "DGS10", dates, np.linspace(1.5, 2.5, len(dates)))
    df = generate_synthetic_ohlcv(bars=200, seed=5, start="2022-01-01")
    cfg = _fred_cfg(tmp_path)
    feats = build_features(df, cfg, pair="EURUSD")
    fred_cols = [c for c in feats.columns if c.startswith("fred_")]
    assert "fred_DFF" in fred_cols
    assert "fred_DGS10_chg1" in fred_cols

    t = 80
    cutoff = pd.Timestamp(df.index[t]).normalize()
    poisoned_dates = dates
    poisoned_vals = np.linspace(0.1, 0.5, len(dates))
    # Spike only *future* FRED observations (after the bar's calendar day)
    poisoned_vals = np.where(poisoned_dates > cutoff, poisoned_vals + 10.0, poisoned_vals)
    _write_series(cache, "DFF", poisoned_dates, poisoned_vals)
    feats2 = build_features(df, cfg, pair="EURUSD")
    pd.testing.assert_series_equal(
        feats["fred_DFF"].iloc[: t + 1],
        feats2["fred_DFF"].iloc[: t + 1],
        check_names=False,
    )


def test_make_dataset_with_fred_drops_warmup_only(tmp_path):
    cache = tmp_path / "fred_cache"
    dates = pd.date_range("2022-01-01", periods=60, freq="D")
    _write_series(cache, "DFF", dates, np.linspace(0.08, 0.2, len(dates)))
    _write_series(cache, "DGS10", dates, np.linspace(1.4, 1.8, len(dates)))
    df = generate_synthetic_ohlcv(bars=400, seed=8, start="2022-01-01")
    X, y, _ = make_dataset(df, _fred_cfg(tmp_path), pair="EURUSD")
    assert len(X) > 100
    fred_cols = [c for c in X.columns if c.startswith("fred_")]
    assert fred_cols
    assert X[fred_cols].isna().sum().sum() == 0
    assert set(y.unique()) <= {0, 1, 2}


def test_fred_feed_status_missing_without_cache(tmp_path):
    cfg = _fred_cfg(tmp_path)
    status = fred_feed_status(cfg)
    assert status is not None and status.enabled
    assert status.source == "missing"
    row = SimpleNamespace(
        pair="EURUSD",
        timeframe="1h",
        validity="OK",
        validity_reason="ok",
        last_bar_at="2026-09-21 10:00 UTC",
        n_bars=10,
        data_source="cached",
    )
    rows = build_health_rows([row], fred=status)
    feed = next(r for r in rows if r["Feed"] == "FRED macro")
    assert feed["Status"] in {"MISSING", "FAIL"}


def test_fred_disabled_is_omitted_from_health():
    assert fred_cfg({"fred": {"enabled": False}})["enabled"] is False
    status = FredStatus(enabled=False)
    row = SimpleNamespace(
        pair="EURUSD",
        timeframe="1h",
        validity="OK",
        validity_reason="ok",
        last_bar_at="n/a",
        n_bars=1,
        data_source="cached",
    )
    rows = build_health_rows([row], fred=status)
    assert "FRED macro" not in [r["Feed"] for r in rows]


def test_default_config_keeps_fred_off():
    cfg = load_config()
    extra = fred_cfg(cfg.get("feature_extras") or {})
    assert extra.get("enabled") is False
    feats = build_features(generate_synthetic_ohlcv(bars=80, seed=1), cfg)
    assert not any(c.startswith("fred_") for c in feats.columns)
