"""Causal MTF confirmation badge + optional conflict flash."""
from __future__ import annotations

from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.mtf import (
    FLASH_HOLD,
    MTF_AGREE,
    MTF_CONFLICT,
    MTF_NA,
    assess_mtf,
    last_htf_slope,
)
from forex_lab.ui.board import BoardRow, apply_mtf_flash, row_from_signal


def _cfg(**mtf_extra):
    block = {
        "enabled": True,
        "timeframes": ["4h"],
        "conflict_flash": "off",
        "flat_eps": 1e-8,
    }
    block.update(mtf_extra)
    return {
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "entry_timing": "next_open",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
        "feature_extras": {"higher_tf": ["4h"], "htf_sma_window": 20, "htf_slope_span": 3},
        "board": {"mtf_confirm": block, "sparkline_bars": 24},
    }


def test_htf_slope_is_causal():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg()
    slope = last_htf_slope(df, cfg, "4h")
    assert slope is not None
    t = 200
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.2
    slope_p = last_htf_slope(poisoned.iloc[: t + 1], cfg, "4h")
    slope_h = last_htf_slope(df.iloc[: t + 1], cfg, "4h")
    assert slope_p == slope_h


def test_assess_agree_conflict_na():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg()
    slope = last_htf_slope(df, cfg, "4h")
    assert slope is not None
    want = "BUY" if slope > 0 else "SELL"
    other = "SELL" if want == "BUY" else "BUY"
    agree = assess_mtf(df, cfg, want, validity="OK")
    conflict = assess_mtf(df, cfg, other, validity="OK")
    hold = assess_mtf(df, cfg, "HOLD", validity="OK")
    stale = assess_mtf(df, cfg, want, validity="STALE")
    assert agree.status == MTF_AGREE
    assert conflict.status == MTF_CONFLICT
    assert hold.status == MTF_NA
    assert stale.status == MTF_NA
    assert "4h" in agree.as_label()


def test_conflict_flash_hold_keeps_raw():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg(conflict_flash=FLASH_HOLD)
    slope = last_htf_slope(df, cfg, "4h")
    assert slope is not None
    other = "SELL" if slope > 0 else "BUY"
    last = {
        "datetime": "2024-01-02 15:00:00",
        "signal": other,
        "raw_signal": other,
        "confidence": 0.51,
        "dir_edge": 0.22,
        "p_buy": 0.20 if other == "SELL" else 0.42,
        "p_sell": 0.42 if other == "SELL" else 0.20,
        "p_hold": 0.38,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, cfg)
    assert row.mtf is not None and row.mtf.status == MTF_CONFLICT
    assert row.buy_sell == "HOLD"
    assert row.raw_signal == other
    assert "MTF conflict" in row.signal_details


def test_conflict_flash_off_keeps_signal():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    cfg = _cfg(conflict_flash="off")
    last = {
        "datetime": "2024-01-02 15:00:00",
        "signal": "BUY",
        "raw_signal": "BUY",
        "confidence": 0.51,
        "dir_edge": 0.22,
        "p_buy": 0.42,
        "p_sell": 0.20,
        "p_hold": 0.38,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, cfg)
    assert row.buy_sell == "BUY"
    assert row.mtf is not None
    assert row.flash_weak is False


def test_apply_mtf_flash_weaken():
    row = BoardRow(
        pair="EURUSD",
        timeframe="1h",
        buy_sell="BUY",
        target="n/a",
        signal_details="conf=0.5",
        status="ready",
        raw_signal="BUY",
    )
    from forex_lab.mtf import MtfStatus

    row.mtf = MtfStatus(status=MTF_CONFLICT, timeframe="4h", direction="down", signal="BUY", note="down vs BUY")
    cfg = _cfg(conflict_flash="weaken")
    out = apply_mtf_flash(row, cfg)
    assert out.buy_sell == "BUY"
    assert out.flash_weak is True
    assert "weak" in out.signal_details.lower()
