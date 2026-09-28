"""Causal level features (SERIAL step 9)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.features import build_features


def _cfg_levels(**lvl_kwargs):
    cfg = load_config()
    extras = dict(cfg.get("feature_extras") or {})
    extras["levels"] = {
        "enabled": True,
        "prior_day": True,
        "week_open": True,
        "swing_order": 3,
        "round_steps": [0.01, 0.005],
        **lvl_kwargs,
    }
    cfg["feature_extras"] = extras
    return cfg


def test_level_features_present_when_enabled():
    df = generate_synthetic_ohlcv(bars=500, seed=11)
    feats = build_features(df, _cfg_levels())
    for col in (
        "lvl_pd_high_dist",
        "lvl_pd_low_dist",
        "lvl_pd_close_dist",
        "lvl_pd_range_pos",
        "lvl_week_open_dist",
        "lvl_swing_high_dist",
        "lvl_swing_low_dist",
        "lvl_round_0100_dist",
        "lvl_round_0050_dist",
    ):
        assert col in feats.columns, col


def test_level_features_off_when_disabled():
    df = generate_synthetic_ohlcv(bars=200, seed=3)
    feats = build_features(df, _cfg_levels(enabled=False))
    assert "lvl_pd_high_dist" not in feats.columns
    assert "lvl_week_open_dist" not in feats.columns
    assert "lvl_swing_high_dist" not in feats.columns
    assert "lvl_round_0100_dist" not in feats.columns


def test_level_features_are_causal():
    df = generate_synthetic_ohlcv(bars=600, seed=19)
    cfg = _cfg_levels()
    t = 300
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.25
    f1 = build_features(df, cfg)
    f2 = build_features(poisoned, cfg)
    cols = [c for c in f1.columns if c.startswith("lvl_")]
    assert cols
    pd.testing.assert_frame_equal(
        f1.iloc[: t + 1][cols],
        f2.iloc[: t + 1][cols],
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def test_prior_day_uses_completed_day_only():
    # Build 3 full UTC days of flat-then-spike so prior-day high is known.
    idx = pd.date_range("2024-03-04 00:00", periods=72, freq="h", tz="UTC")
    close = np.full(len(idx), 1.1000)
    # Day 0 (Mar 4): high 1.1050 at hour 10
    close[10] = 1.1050
    # Day 1 (Mar 5): high 1.1020
    close[24 + 5] = 1.1020
    # Day 2 stays 1.1000
    high = np.maximum(close, 1.1000)
    high[10] = 1.1050
    high[24 + 5] = 1.1020
    low = np.full(len(idx), 1.0990)
    open_ = np.full(len(idx), 1.1000)
    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1000.0},
        index=idx,
    )
    feats = build_features(df, _cfg_levels(swing_order=0, week_open=False, round_steps=[]))
    # Mid day-2 bar should see prior day = Mar 5 high 1.1020, not Mar 4 1.1050
    mid = idx[24 + 24 + 12]  # Mar 6 12:00
    row = feats.loc[mid]
    # close 1.10 vs prior high 1.1020 -> dist = 1.10/1.1020 - 1
    expected = 1.1000 / 1.1020 - 1.0
    assert abs(float(row["lvl_pd_high_dist"]) - expected) < 1e-9


def test_round_distance_nearest_step():
    idx = pd.date_range("2024-01-02", periods=50, freq="h", tz="UTC")
    close = np.full(len(idx), 1.1037)  # nearest 0.01 = 1.10; dist = 0.0037/1.1037
    df = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.0001,
            "Low": close - 0.0001,
            "Close": close,
            "Volume": 1000.0,
        },
        index=idx,
    )
    feats = build_features(
        df, _cfg_levels(prior_day=False, week_open=False, swing_order=0, round_steps=[0.01])
    )
    d = float(feats["lvl_round_0100_dist"].iloc[-1])
    assert abs(d - (1.1037 - 1.10) / 1.1037) < 1e-9
