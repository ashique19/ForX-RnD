"""Causal Fibonacci swing features (SERIAL step 11)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.features import build_features, _confirmed_swing_series


def _cfg_fib(**fib_kwargs):
    cfg = load_config()
    extras = dict(cfg.get("feature_extras") or {})
    levels = dict(extras.get("levels") or {})
    levels["enabled"] = True
    extras["levels"] = levels
    extras["fib"] = {
        "enabled": True,
        "swing_order": 3,
        "ratios": [0.236, 0.382, 0.5, 0.618],
        "extensions": [1.272, 1.618],
        "proximity_frac": 0.0005,
        "fail_soft_fill": 0.0,
        **fib_kwargs,
    }
    dxy = dict(extras.get("dxy_yields") or {})
    dxy["enabled"] = False
    extras["dxy_yields"] = dxy
    cfg["feature_extras"] = extras
    return cfg


def test_fib_features_present_when_enabled():
    df = generate_synthetic_ohlcv(bars=500, seed=11)
    feats = build_features(df, _cfg_fib())
    for col in (
        "fib_swing_high_dist",
        "fib_swing_low_dist",
        "fib_range_pct",
        "fib_pos",
        "fib_swing_dir",
        "fib_r0236_dist",
        "fib_r0382_dist",
        "fib_r0500_dist",
        "fib_r0618_dist",
        "fib_near_0236",
        "fib_near_0382",
        "fib_near_0500",
        "fib_near_0618",
        "fib_ext1272_dist",
        "fib_ext1618_dist",
    ):
        assert col in feats.columns, col
    assert "lvl_swing_high_dist" in feats.columns


def test_fib_features_off_when_disabled():
    df = generate_synthetic_ohlcv(bars=200, seed=3)
    feats = build_features(df, _cfg_fib(enabled=False))
    assert "fib_r0618_dist" not in feats.columns
    assert "fib_swing_high_dist" not in feats.columns
    assert "lvl_swing_high_dist" in feats.columns


def test_fib_features_are_causal():
    df = generate_synthetic_ohlcv(bars=600, seed=19)
    cfg = _cfg_fib()
    t = 300
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.25
    f1 = build_features(df, cfg)
    f2 = build_features(poisoned, cfg)
    cols = [c for c in f1.columns if c.startswith("fib_")]
    assert cols
    pd.testing.assert_frame_equal(
        f1.iloc[: t + 1][cols],
        f2.iloc[: t + 1][cols],
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def test_fib_retracement_level_math():
    """Fib distances must match lo+r*(hi-lo) from the same confirmed SH/SL."""
    df = generate_synthetic_ohlcv(bars=500, seed=42)
    order = 3
    sh, sl = _confirmed_swing_series(df["High"], df["Low"], order)
    mask = sh.notna() & sl.notna() & ((sh - sl).abs() > 1e-8)
    assert mask.sum() > 50
    i = int(np.flatnonzero(mask.to_numpy())[-1])
    hi = float(max(sh.iloc[i], sl.iloc[i]))
    lo = float(min(sh.iloc[i], sl.iloc[i]))
    close = float(df["Close"].iloc[i])
    feats = build_features(df, _cfg_fib(swing_order=order, proximity_frac=1.0))  # generous near
    row = feats.iloc[i]
    for r, tag in ((0.236, "0236"), (0.382, "0382"), (0.5, "0500"), (0.618, "0618")):
        level = lo + r * (hi - lo)
        expected = (close - level) / close
        assert abs(float(row[f"fib_r{tag}_dist"]) - expected) < 1e-9, tag
    assert abs(float(row["fib_range_pct"]) - (hi - lo) / close) < 1e-9
    assert abs(float(row["fib_pos"]) - (close - lo) / (hi - lo)) < 1e-9
    # proximity_frac=1.0 => every valid bar is "near"
    assert float(row["fib_near_0500"]) == 1.0
    # extension stub: nearest of up/down 1.618
    up = hi + (1.618 - 1.0) * (hi - lo)
    dn = lo - (1.618 - 1.0) * (hi - lo)
    nearest = up if abs(close - up) <= abs(close - dn) else dn
    assert abs(float(row["fib_ext1618_dist"]) - (close - nearest) / close) < 1e-9


def test_fib_fail_soft_when_swings_sparse():
    idx = pd.date_range("2024-01-02", periods=20, freq="h", tz="UTC")
    close = np.linspace(1.10, 1.11, len(idx))
    df = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.0002,
            "Low": close - 0.0002,
            "Close": close,
            "Volume": 1000.0,
        },
        index=idx,
    )
    feats = build_features(df, _cfg_fib(swing_order=5, fail_soft_fill=0.0))
    assert float(feats["fib_r0618_dist"].iloc[5]) == 0.0
    assert float(feats["fib_range_pct"].iloc[5]) == 0.0


def test_shared_swing_helper_matches_levels():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    sh, sl = _confirmed_swing_series(df["High"], df["Low"], 3)
    feats = build_features(df, _cfg_fib(swing_order=3))
    close = df["Close"].astype(float).replace(0, np.nan)
    expected_h = close / sh.replace(0, np.nan) - 1.0
    mask = sh.notna() & sl.notna()
    a = feats.loc[mask, "fib_swing_high_dist"].to_numpy()
    b = expected_h.loc[mask].to_numpy()
    assert np.allclose(a, b, equal_nan=True)
    c = feats.loc[mask, "lvl_swing_high_dist"].to_numpy()
    assert np.allclose(a, c, equal_nan=True)
