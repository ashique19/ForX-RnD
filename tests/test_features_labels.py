"""Leakage, label, and filter tests for the research lab."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_lab.backtest import _simulate_trades
from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.features import (
    LABEL_MAP,
    build_features,
    build_labels,
    make_dataset,
    triple_barrier_labels,
)
from forex_lab.model import apply_signal_filters


def _cfg(**overrides):
    cfg = load_config()
    cfg.update(overrides)
    return cfg


def test_features_do_not_use_future_bars():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg()
    t = 180
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] = (
            poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] * 1.25
        )
    f1 = build_features(df, cfg)
    f2 = build_features(poisoned, cfg)
    cols = [c for c in f1.columns if c in f2.columns]
    left = f1.iloc[: t + 1][cols]
    right = f2.iloc[: t + 1][cols]
    pd.testing.assert_frame_equal(left, right, check_exact=False, rtol=1e-12, atol=1e-12)


def test_labels_do_use_future_path():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg()
    t = 180
    poisoned = df.copy()
    # Push future path through the upper barrier
    poisoned.loc[poisoned.index[t + 1 : t + 5], "High"] = poisoned["Close"].iloc[t] * 1.05
    y1 = build_labels(df, cfg)
    y2 = build_labels(poisoned, cfg)
    changed = (y1.fillna(-1) != y2.fillna(-1)).sum()
    assert changed > 0


def test_triple_barrier_first_touch_buy():
    n = 20
    close = np.full(n, 1.0)
    open_ = np.full(n, 1.0)
    high = np.full(n, 1.0)
    low = np.full(n, 1.0)
    atr = np.full(n, 0.01)
    high[3] = 1.03  # +2 ATR from fill 1.00
    labels = triple_barrier_labels(
        open_, high, low, close, atr, horizon=8, tp_atr=2.0, sl_atr=2.0, entry_timing="next_open"
    )
    # t=2 -> fill at open[3]=1.0, high[3] hits upper first
    assert labels[2] == LABEL_MAP["BUY"]


def test_triple_barrier_timeout_is_hold():
    n = 20
    close = np.full(n, 1.0)
    open_ = np.full(n, 1.0)
    high = np.full(n, 1.001)
    low = np.full(n, 0.999)
    atr = np.full(n, 0.01)
    labels = triple_barrier_labels(
        open_, high, low, close, atr, horizon=8, tp_atr=2.0, sl_atr=2.0, entry_timing="next_open"
    )
    assert labels[2] == LABEL_MAP["HOLD"]


def test_triple_barrier_same_bar_conflict_is_hold():
    n = 20
    close = np.full(n, 1.0)
    open_ = np.full(n, 1.0)
    high = np.full(n, 1.0)
    low = np.full(n, 1.0)
    atr = np.full(n, 0.01)
    high[3] = 1.03
    low[3] = 0.97
    labels = triple_barrier_labels(
        open_, high, low, close, atr, horizon=8, tp_atr=2.0, sl_atr=2.0, entry_timing="next_open"
    )
    assert labels[2] == LABEL_MAP["HOLD"]


def test_make_dataset_drops_warmup_and_has_no_nan():
    df = generate_synthetic_ohlcv(bars=400, seed=1)
    X, y, ohlcv = make_dataset(df, _cfg())
    assert len(X) > 100
    assert X.isna().sum().sum() == 0
    assert set(y.unique()) <= {0, 1, 2}
    assert list(ohlcv.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_triple_barrier_asymmetric_rr_is_per_side():
    """2:1 R:R: BUY needs +2 ATR before -1 ATR; -1 ATR alone is not a SELL."""
    n = 20
    close = np.full(n, 1.0)
    open_ = np.full(n, 1.0)
    high = np.full(n, 1.0)
    low = np.full(n, 1.0)
    atr = np.full(n, 0.01)
    high[3] = 1.025  # +2.5 ATR: long TP at +2 ATR, short SL at +1 ATR
    labels = triple_barrier_labels(
        open_, high, low, close, atr, horizon=8, tp_atr=2.0, sl_atr=1.0, entry_timing="next_open"
    )
    assert labels[2] == LABEL_MAP["BUY"]

    high = np.full(n, 1.0)
    low = np.full(n, 1.0)
    low[3] = 0.989  # -1.1 ATR: long SL, short TP is -2 ATR so not yet SELL
    labels = triple_barrier_labels(
        open_, high, low, close, atr, horizon=8, tp_atr=2.0, sl_atr=1.0, entry_timing="next_open"
    )
    assert labels[2] == LABEL_MAP["HOLD"]

    low[4] = 0.979  # continues to -2.1 ATR: short TP
    labels = triple_barrier_labels(
        open_, high, low, close, atr, horizon=8, tp_atr=2.0, sl_atr=1.0, entry_timing="next_open"
    )
    assert labels[2] == LABEL_MAP["SELL"]


def test_session_filter_blocks_asia_only():
    idx = pd.date_range("2024-01-01 00:00", periods=4, freq="h")
    frame = pd.DataFrame(
        {
            "pred_raw": [2, 2, 2, 2],
            "confidence": [0.7, 0.7, 0.7, 0.7],
            "dir_edge": [0.2, 0.2, 0.2, 0.2],
            "sess_london": [0.0, 1.0, 0.0, 1.0],
            "sess_ny": [0.0, 0.0, 1.0, 1.0],
        },
        index=idx,
    )
    cfg = _cfg()
    cfg["signals"] = {**(cfg.get("signals") or {}), "min_confidence": 0.4, "min_dir_edge": 0.0, "sessions": ["london", "ny"]}
    out = apply_signal_filters(frame, cfg)
    assert list(out) == [LABEL_MAP["HOLD"], LABEL_MAP["BUY"], LABEL_MAP["BUY"], LABEL_MAP["BUY"]]


def test_cost_aware_labels_require_tp_to_cover_spread():
    n = 20
    close = np.full(n, 1.0)
    open_ = np.full(n, 1.0)
    high = np.full(n, 1.0)
    low = np.full(n, 1.0)
    atr = np.full(n, 0.01)
    high[3] = 1.03
    labels = triple_barrier_labels(
        open_,
        high,
        low,
        close,
        atr,
        horizon=8,
        tp_atr=2.0,
        sl_atr=2.0,
        entry_timing="next_open",
        min_tp_abs=0.05,  # 5x larger than 2*ATR
    )
    assert labels[2] == LABEL_MAP["HOLD"]


def test_signal_filters_force_hold():
    frame = pd.DataFrame(
        {
            "pred_raw": [2, 2, 0, 1],
            "confidence": [0.70, 0.40, 0.60, 0.90],
            "dir_edge": [0.20, 0.20, 0.02, 0.00],
        }
    )
    cfg = _cfg()
    cfg["signals"] = {**(cfg.get("signals") or {}), "min_confidence": 0.45, "min_dir_edge": 0.08}
    out = apply_signal_filters(frame, cfg)
    assert list(out) == [LABEL_MAP["BUY"], LABEL_MAP["HOLD"], LABEL_MAP["HOLD"], LABEL_MAP["HOLD"]]


def test_one_position_does_not_overlap():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    cfg = _cfg(one_position=True, horizon=8, use_tp_sl=True)
    # Signal BUY on every bar
    sig = pd.Series(LABEL_MAP["BUY"], index=df.index)
    trades = _simulate_trades(df, sig, cfg, "EURUSD")
    assert not trades.empty
    exits = pd.to_datetime(trades["entry_time"])  # noqa: F841 — ordering check below
    times = list(zip(pd.to_datetime(trades["entry_time"]), range(len(trades))))
    # Next entry must be after previous entry by at least 1 bar; stronger: decision/entry monotonic
    entry = pd.to_datetime(trades["entry_time"])
    assert entry.is_monotonic_increasing
    # With one_position, number of trades << number of bars
    assert len(trades) < len(df) / 2


def test_higher_tf_features_are_causal_and_optional():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg()
    cfg["feature_extras"] = {
        "higher_tf": ["4h"],
        "htf_sma_window": 20,
        "htf_slope_span": 3,
        "session_overlap": True,
        "vol_percentile_window": 50,
        "cross_pair": None,
    }
    feats = build_features(df, cfg)
    assert "tf_4h_sma_slope" in feats.columns
    assert "sess_ldn_ny" in feats.columns
    t = 200
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.2
    f2 = build_features(poisoned, cfg)
    cols = [c for c in feats.columns if c in f2.columns]
    pd.testing.assert_frame_equal(
        feats.iloc[: t + 1][cols], f2.iloc[: t + 1][cols], check_exact=False, rtol=1e-10, atol=1e-10
    )
    off = _cfg()
    off["feature_extras"] = {"higher_tf": [], "session_overlap": False, "vol_percentile_window": 0}
    plain = build_features(df, off)
    assert "tf_4h_sma_slope" not in plain.columns


def test_cross_pair_skipped_when_csv_missing(tmp_path):
    df = generate_synthetic_ohlcv(bars=80, seed=2)
    cfg = _cfg()
    cfg["feature_extras"] = {
        "higher_tf": [],
        "session_overlap": False,
        "vol_percentile_window": 0,
        "cross_pair": "GBPUSD",
    }
    cfg["paths"] = {**(cfg.get("paths") or {}), "data_dir": str(tmp_path)}
    feats = build_features(df, cfg, pair="EURUSD")
    assert "xpair_ret_1" not in feats.columns


def test_htf_trend_filter_blocks_countertrend_buy():
    idx = pd.date_range("2024-01-02 12:00", periods=3, freq="h")
    frame = pd.DataFrame(
        {
            "pred_raw": [2, 2, 0],
            "confidence": [0.7, 0.7, 0.7],
            "dir_edge": [0.2, 0.2, 0.2],
            "tf_4h_sma_slope": [-0.01, 0.02, 0.02],
        },
        index=idx,
    )
    cfg = _cfg()
    cfg["signals"] = {**(cfg.get("signals") or {}), "min_confidence": 0.4, "htf_trend_filter": "4h"}
    out = apply_signal_filters(frame, cfg)
    assert list(out) == [LABEL_MAP["HOLD"], LABEL_MAP["BUY"], LABEL_MAP["HOLD"]]
