"""ATR/vol regime gate (SERIAL step 7) — unit tests."""
from __future__ import annotations

import pandas as pd

from forex_lab.features import LABEL_MAP
from forex_lab.vol_regime import (
    RESOLVED_OFF,
    RESOLVED_SKIP_EXTREMES,
    RESOLVED_SKIP_HIGH,
    apply_replay_vol_regime,
    apply_vol_regime_to_pred,
    build_vol_regime_mask,
    normalize_vol_regime_metric,
    normalize_vol_regime_mode,
    vol_regime_enabled,
    vol_regime_report_line,
    vol_regime_thresholds,
)


def _cfg(**vr_kwargs):
    return {
        "replay": {
            "session_gate": "overlap",
            "slippage_pips": 0.2,
            "exit_slippage": True,
            "vol_regime": {
                "enabled": True,
                "mode": "skip_extremes",
                "metric": "atr_pctile",
                "high": 0.90,
                "low": 0.10,
                **vr_kwargs,
            },
        },
        "signals": {"min_confidence": 0.55, "sessions": []},
    }


def test_normalize_mode_and_metric():
    assert normalize_vol_regime_mode("skip_extremes") == RESOLVED_SKIP_EXTREMES
    assert normalize_vol_regime_mode("skip_high") == RESOLVED_SKIP_HIGH
    assert normalize_vol_regime_mode("off") == RESOLVED_OFF
    assert normalize_vol_regime_metric("atr_pctile") == "atr_pctile"
    assert normalize_vol_regime_metric("vol_pct") == "vol_pct"
    assert normalize_vol_regime_metric("atr") == "atr_pctile"


def test_thresholds():
    low, high = vol_regime_thresholds(_cfg())
    assert abs(low - 0.10) < 1e-9
    assert abs(high - 0.90) < 1e-9
    low2, high2 = vol_regime_thresholds(_cfg(low=0.95, high=0.90))
    assert low2 == 0.0  # degenerate -> quiet off


def test_apply_replay_resolves_skip_extremes():
    cfg = _cfg()
    apply_replay_vol_regime(cfg)
    assert cfg["replay"]["vol_regime_resolved"] == RESOLVED_SKIP_EXTREMES
    assert cfg["replay"]["vol_regime_metric"] == "atr_pctile"
    assert cfg["replay"]["vol_regime_high"] == 0.90
    assert cfg["replay"]["vol_regime_low"] == 0.10
    assert vol_regime_enabled(cfg)


def test_apply_replay_off():
    cfg = _cfg(enabled=False)
    apply_replay_vol_regime(cfg)
    assert cfg["replay"]["vol_regime_resolved"] == RESOLVED_OFF
    assert not vol_regime_enabled(cfg)


def test_skip_high_zeros_low():
    cfg = _cfg(mode="skip_high", low=0.10)
    apply_replay_vol_regime(cfg)
    assert cfg["replay"]["vol_regime_resolved"] == RESOLVED_SKIP_HIGH
    assert cfg["replay"]["vol_regime_low"] == 0.0


def test_mask_and_pred_skip_extremes():
    cfg = _cfg()
    apply_replay_vol_regime(cfg)
    idx = pd.RangeIndex(5)
    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"]] * 5,
            "side": ["BUY"] * 5,
            "confidence": [0.7] * 5,
            "atr_pctile": [0.05, 0.50, 0.95, 0.10, 0.90],
        },
        index=idx,
    )
    mask = build_vol_regime_mask(pred, cfg)
    # block: 0.05 (<=0.10), 0.95 (>=0.90), 0.10 (<=0.10), 0.90 (>=0.90)
    assert list(mask.astype(int)) == [1, 0, 1, 1, 1]
    out = apply_vol_regime_to_pred(pred, cfg)
    assert list(out["pred"]) == [
        LABEL_MAP["HOLD"],
        LABEL_MAP["BUY"],
        LABEL_MAP["HOLD"],
        LABEL_MAP["HOLD"],
        LABEL_MAP["HOLD"],
    ]
    assert out.loc[1, "side"] == "BUY"
    assert out.loc[0, "side"] == "HOLD"


def test_skip_high_only():
    cfg = _cfg(mode="skip_high")
    apply_replay_vol_regime(cfg)
    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["SELL"], LABEL_MAP["SELL"]],
            "atr_pctile": [0.05, 0.95],
        }
    )
    out = apply_vol_regime_to_pred(pred, cfg)
    assert list(out["pred"]) == [LABEL_MAP["SELL"], LABEL_MAP["HOLD"]]


def test_fail_soft_missing_metric():
    cfg = _cfg()
    apply_replay_vol_regime(cfg)
    pred = pd.DataFrame({"pred": [LABEL_MAP["BUY"]], "confidence": [0.8]})
    out = apply_vol_regime_to_pred(pred, cfg)
    assert list(out["pred"]) == [LABEL_MAP["BUY"]]
    assert "missing" in (cfg["replay"].get("vol_regime_note") or "")


def test_vol_pct_metric():
    cfg = _cfg(metric="vol_pct", mode="skip_high", high=0.8)
    apply_replay_vol_regime(cfg)
    assert cfg["replay"]["vol_regime_metric"] == "vol_pct"
    pred = pd.DataFrame({"pred": [LABEL_MAP["BUY"], LABEL_MAP["BUY"]], "vol_pct": [0.5, 0.9]})
    out = apply_vol_regime_to_pred(pred, cfg)
    assert list(out["pred"]) == [LABEL_MAP["BUY"], LABEL_MAP["HOLD"]]


def test_report_line():
    cfg = _cfg()
    apply_replay_vol_regime(cfg)
    line = vol_regime_report_line(cfg)
    assert "skip_extremes" in line
    assert "atr_pctile" in line
    assert "0.9" in line


def test_atr_pctile_feature_exists():
    """Smoke: feature builder emits atr_pctile when vol_percentile_window > 0."""
    import numpy as np
    from forex_lab.features import build_features

    n = 200
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0005, n))
    df = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.001,
            "Low": close - 0.001,
            "Close": close,
            "Volume": 1000,
        },
        index=idx,
    )
    cfg = {
        "atr_period": 14,
        "vol_window": 20,
        "vol_long_window": 100,
        "sma_windows": [10, 20],
        "ema_windows": [12],
        "rsi_period": 14,
        "range_windows": [20],
        "feature_extras": {"vol_percentile_window": 50, "session_overlap": False, "higher_tf": []},
    }
    out = build_features(df, cfg, pair="EURUSD")
    assert "atr_pctile" in out.columns
    assert "vol_pct" in out.columns
    valid = out["atr_pctile"].dropna()
    assert len(valid) > 20
    assert float(valid.min()) >= -1e-9
    assert float(valid.max()) <= 1.0 + 1e-9
