"""Causal TA-pack (pandas-ta subset) tests."""
from __future__ import annotations

import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.features import build_features, make_dataset


TA_COLS = {
    "ta_stoch_k",
    "ta_stoch_d",
    "ta_adx",
    "ta_plus_di",
    "ta_minus_di",
    "ta_bb_pctb",
    "ta_bb_bw",
    "ta_cci",
    "ta_willr",
    "ta_roc",
    "ta_kc_pos",
}


def _cfg(**overrides):
    cfg = load_config()
    cfg.update(overrides)
    return cfg


def _ta_on(cfg, **pack):
    extra = dict(cfg.get("feature_extras") or {})
    extra["pandas_ta"] = {
        "enabled": True,
        "backend": "native",
        "indicators": ["stoch", "adx", "bbands", "cci", "willr", "roc", "kc"],
        **pack,
    }
    extra["fred"] = {"enabled": False}
    cfg["feature_extras"] = extra
    return cfg


def test_default_config_keeps_ta_pack_off():
    cfg = load_config()
    feats = build_features(generate_synthetic_ohlcv(bars=80, seed=1), cfg)
    assert TA_COLS.isdisjoint(feats.columns)


def test_ta_pack_columns_when_enabled():
    df = generate_synthetic_ohlcv(bars=200, seed=3)
    feats = build_features(df, _ta_on(_cfg()))
    missing = TA_COLS - set(feats.columns)
    assert not missing, missing
    assert feats["ta_stoch_k"].dropna().between(-0.05, 1.05).all()


def test_ta_pack_is_causal():
    df = generate_synthetic_ohlcv(bars=300, seed=7)
    cfg = _ta_on(_cfg())
    t = 160
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.3
    f1 = build_features(df, cfg)
    f2 = build_features(poisoned, cfg)
    cols = [c for c in f1.columns if c in f2.columns]
    pd.testing.assert_frame_equal(
        f1.iloc[: t + 1][cols],
        f2.iloc[: t + 1][cols],
        check_exact=False,
        rtol=1e-10,
        atol=1e-10,
    )


def test_ta_pack_make_dataset_has_no_nan():
    df = generate_synthetic_ohlcv(bars=400, seed=4)
    X, y, _ = make_dataset(df, _ta_on(_cfg()))
    assert len(X) > 100
    ta = [c for c in X.columns if c.startswith("ta_")]
    assert ta
    assert X[ta].isna().sum().sum() == 0
    assert y.isna().sum() == 0
