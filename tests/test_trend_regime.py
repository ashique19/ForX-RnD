"""Trend vs chop regime gate (measured-intel) — unit tests."""
from __future__ import annotations

import pandas as pd

from forex_lab.features import LABEL_MAP
from forex_lab.trend_regime import (
    RESOLVED_OFF,
    RESOLVED_SKIP_CHOP,
    apply_replay_trend_regime,
    apply_trend_regime_to_pred,
    attach_trend_regime_columns,
    build_trend_regime_mask,
    normalize_trend_regime_metric,
    normalize_trend_regime_mode,
    trend_regime_enabled,
    trend_regime_report_line,
)


def _cfg(**tr_kwargs):
    return {
        "replay": {
            "session_gate": "overlap",
            "slippage_pips": 0.2,
            "exit_slippage": True,
            "trend_regime": {
                "enabled": True,
                "mode": "skip_chop",
                "metric": "adx",
                "period": 14,
                "min_adx": 0.20,
                "min_er": 0.25,
                **tr_kwargs,
            },
        },
        "signals": {"min_confidence": 0.60, "sessions": []},
    }


def test_normalize_mode_and_metric():
    assert normalize_trend_regime_mode("skip_chop") == RESOLVED_SKIP_CHOP
    assert normalize_trend_regime_mode("off") == RESOLVED_OFF
    assert normalize_trend_regime_metric("adx") == "adx"
    assert normalize_trend_regime_metric("er") == "er"
    assert normalize_trend_regime_metric("kaufman") == "er"


def test_apply_replay_resolves_skip_chop():
    cfg = _cfg()
    apply_replay_trend_regime(cfg)
    assert cfg["replay"]["trend_regime_resolved"] == RESOLVED_SKIP_CHOP
    assert cfg["replay"]["trend_regime_metric"] == "adx"
    assert cfg["replay"]["trend_regime_min"] == 0.20
    assert cfg["replay"]["trend_regime_period"] == 14
    assert trend_regime_enabled(cfg)


def test_apply_replay_off():
    cfg = _cfg(enabled=False)
    apply_replay_trend_regime(cfg)
    assert cfg["replay"]["trend_regime_resolved"] == RESOLVED_OFF
    assert not trend_regime_enabled(cfg)


def test_mask_and_pred_skip_chop_adx():
    cfg = _cfg()
    apply_replay_trend_regime(cfg)
    idx = pd.RangeIndex(5)
    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"]] * 5,
            "side": ["BUY"] * 5,
            "confidence": [0.7] * 5,
            "trend_adx": [0.10, 0.25, 0.19, 0.50, 0.05],
        },
        index=idx,
    )
    mask = build_trend_regime_mask(pred, cfg)
    # block when < 0.20: 0.10, 0.19, 0.05
    assert list(mask.astype(int)) == [1, 0, 1, 0, 1]
    out = apply_trend_regime_to_pred(pred, cfg)
    assert list(out["pred"]) == [
        LABEL_MAP["HOLD"],
        LABEL_MAP["BUY"],
        LABEL_MAP["HOLD"],
        LABEL_MAP["BUY"],
        LABEL_MAP["HOLD"],
    ]


def test_er_metric_threshold():
    cfg = _cfg(metric="er", min_er=0.30)
    apply_replay_trend_regime(cfg)
    assert cfg["replay"]["trend_regime_metric"] == "er"
    assert cfg["replay"]["trend_regime_min"] == 0.30
    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["SELL"], LABEL_MAP["SELL"]],
            "side": ["SELL", "SELL"],
            "trend_er": [0.10, 0.50],
        }
    )
    out = apply_trend_regime_to_pred(pred, cfg)
    assert out["pred"].iloc[0] == LABEL_MAP["HOLD"]
    assert out["pred"].iloc[1] == LABEL_MAP["SELL"]


def test_missing_metric_fail_soft():
    cfg = _cfg()
    apply_replay_trend_regime(cfg)
    pred = pd.DataFrame({"pred": [LABEL_MAP["BUY"]], "side": ["BUY"]})
    out = apply_trend_regime_to_pred(pred, cfg)
    assert out["pred"].iloc[0] == LABEL_MAP["BUY"]
    assert "missing" in (cfg["replay"].get("trend_regime_note") or "")


def test_attach_columns_from_ohlcv():
    cfg = _cfg()
    apply_replay_trend_regime(cfg)
    n = 40
    idx = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    # trending up then flat
    close = pd.Series(range(n), index=idx, dtype=float) + 1.0
    ohlcv = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
        },
        index=idx,
    )
    frame = pd.DataFrame({"pred": [LABEL_MAP["BUY"]] * n}, index=idx)
    out = attach_trend_regime_columns(frame, ohlcv, cfg)
    assert "trend_adx" in out.columns
    assert "trend_er" in out.columns
    assert out["trend_adx"].notna().sum() > 0
    assert out["trend_er"].notna().sum() > 0


def test_report_line():
    cfg = _cfg()
    apply_replay_trend_regime(cfg)
    line = trend_regime_report_line(cfg)
    assert "skip_chop" in line
    assert "adx" in line
