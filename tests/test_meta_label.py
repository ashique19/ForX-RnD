"""Unit tests for meta-label asia_quiet / asia_loud_weak / conf_atr / atr attach."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_lab.meta_label import (
    apply_meta_label_to_pred,
    apply_replay_meta_label,
    attach_atr_pctile_for_meta,
    build_meta_label_mask,
)


def _cfg(rule: str, **kwargs):
    block = {
        "enabled": True,
        "mode": "rule",
        "rule": rule,
        "atr_max": 0.20,
        "atr_min": 0.60,
        "conf_max": 0.65,
        "product_max": 0.10,
        "atr_window": 100,
    }
    block.update(kwargs)
    cfg = {"replay": {"meta_label": block}, "atr_period": 14, "feature_extras": {}}
    return apply_replay_meta_label(cfg)


def test_asia_quiet_vetoes_asia_low_atr_only():
    cfg = _cfg("asia_quiet", atr_max=0.20)
    idx = pd.date_range("2020-01-06 01:00", periods=4, freq="h", tz="UTC")  # Asia hours
    # bar0 Asia quiet, bar1 Asia loud, bar2 London quiet, bar3 Asia quiet
    pred = pd.DataFrame(
        {
            "pred": [2, 2, 0, 2],
            "side": ["BUY", "BUY", "SELL", "BUY"],
            "confidence": [0.8, 0.8, 0.8, 0.55],
            "atr_pctile": [0.10, 0.50, 0.10, 0.10],
            "sess_asia": [1.0, 1.0, 0.0, 1.0],
            "sess_london": [0.0, 0.0, 1.0, 0.0],
            "sess_ny": [0.0, 0.0, 0.0, 0.0],
        },
        index=idx,
    )
    out = apply_meta_label_to_pred(pred, cfg)
    assert list(out["meta_label_block"].astype(int)) == [1, 0, 0, 1]
    assert int(out.iloc[0]["pred"]) == 1  # HOLD
    assert int(out.iloc[1]["pred"]) == 2  # kept
    assert int(out.iloc[2]["pred"]) == 0  # london kept


def test_conf_atr_product_rule():
    cfg = _cfg("conf_atr", product_max=0.10)
    idx = pd.RangeIndex(3)
    pred = pd.DataFrame(
        {
            "pred": [2, 2, 2],
            "confidence": [0.5, 0.8, 0.9],
            "atr_pctile": [0.1, 0.05, 0.5],  # products 0.05, 0.04, 0.45
        },
        index=idx,
    )
    mask = build_meta_label_mask(pred, cfg)
    assert list(mask.astype(int)) == [1, 1, 0]


def test_disabled_is_noop():
    cfg = apply_replay_meta_label(
        {"replay": {"meta_label": {"enabled": False, "rule": "asia_quiet"}}}
    )
    pred = pd.DataFrame({"pred": [2], "confidence": [0.5], "atr_pctile": [0.05]})
    out = apply_meta_label_to_pred(pred, cfg)
    assert "meta_label_block" not in out.columns
    assert int(out.iloc[0]["pred"]) == 2


def test_attach_atr_pctile_from_ohlcv():
    cfg = _cfg("asia_quiet", atr_window=30)
    n = 80
    idx = pd.date_range("2020-01-01", periods=n, freq="h", tz="UTC")
    close = pd.Series(np.linspace(1.1, 1.2, n), index=idx)
    high = close + 0.001
    low = close - 0.001
    ohlcv = pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close}, index=idx)
    pred = pd.DataFrame({"pred": [2] * 10, "confidence": [0.7] * 10}, index=idx[-10:])
    out = attach_atr_pctile_for_meta(pred, ohlcv, cfg)
    assert "atr_pctile" in out.columns
    assert out["atr_pctile"].notna().all()

def test_asia_loud_weak_vetoes_asia_high_atr_low_conf():
    cfg = _cfg("asia_loud_weak", atr_min=0.60, conf_max=0.70)
    idx = pd.date_range("2020-01-06 01:00", periods=4, freq="h", tz="UTC")  # Asia hours
    pred = pd.DataFrame(
        {
            "pred": [2, 2, 0, 2],
            "side": ["BUY", "BUY", "SELL", "BUY"],
            "confidence": [0.55, 0.80, 0.55, 0.55],  # low, high, low, low
            "atr_pctile": [0.70, 0.70, 0.70, 0.40],  # loud, loud, loud, quiet
            "sess_asia": [1.0, 1.0, 0.0, 1.0],
            "sess_london": [0.0, 0.0, 1.0, 0.0],
            "sess_ny": [0.0, 0.0, 0.0, 0.0],
        },
        index=idx,
    )
    out = apply_meta_label_to_pred(pred, cfg)
    # bar0 Asia loud+weak -> veto; bar1 Asia loud+strong conf -> keep;
    # bar2 London loud+weak -> keep; bar3 Asia quiet+weak -> keep
    assert list(out["meta_label_block"].astype(int)) == [1, 0, 0, 0]
    assert int(out.iloc[0]["pred"]) == 1  # HOLD
    assert int(out.iloc[1]["pred"]) == 2
    assert int(out.iloc[2]["pred"]) == 0
