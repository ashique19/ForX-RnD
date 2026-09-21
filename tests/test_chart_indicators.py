"""Chart indicator series stay aligned with the OHLCV window."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from forex_lab.chart_indicators import (
    INDICATOR_KEYS,
    chart_indicators,
    chart_indicators_aligned,
    chart_price_digits,
    indicator_frame,
)
from forex_lab.data import generate_synthetic_ohlcv


def test_price_digits_match_desk_scale():
    assert chart_price_digits("EURUSD") == 5
    assert chart_price_digits("USDJPY") == 3
    assert chart_price_digits("GBPJPY") == 3
    assert chart_price_digits("XAUUSD") == 2
    assert chart_price_digits("XAGUSD") == 2


def test_series_length_matches_bars_and_warmup_is_null():
    df = generate_synthetic_ohlcv(bars=80, seed=4)
    lists = chart_indicators(df)
    assert set(lists) == set(INDICATOR_KEYS)
    for key, values in lists.items():
        assert len(values) == len(df), key
    assert all(value is None for value in lists["ema21"][:20])
    assert lists["ema21"][20] is not None
    assert all(value is None for value in lists["sma200"])
    assert all(value is None for value in lists["rsi"][:14])
    assert lists["rsi"][-1] is not None
    assert lists["atr"][-1] is not None
    rsi = [value for value in lists["rsi"] if value is not None]
    assert rsi and all(0.0 <= value <= 100.0 for value in rsi)
    stoch = [value for value in lists["stoch_k"] if value is not None]
    assert stoch and all(0.0 <= value <= 100.0 for value in stoch)


def test_macd_histogram_and_bands_are_consistent():
    df = generate_synthetic_ohlcv(bars=120, seed=9)
    frame = indicator_frame(df)
    assert len(frame) == len(df)
    ok = frame[["macd", "macd_signal", "macd_hist"]].dropna()
    assert len(ok) > 0
    np.testing.assert_allclose(ok["macd_hist"], ok["macd"] - ok["macd_signal"], atol=1e-12)
    bands = frame[["bb_lower", "bb_mid", "bb_upper"]].dropna()
    assert len(bands) == len(df) - 19
    assert (bands["bb_upper"] + 1e-12 >= bands["bb_mid"]).all()
    assert (bands["bb_mid"] + 1e-12 >= bands["bb_lower"]).all()


def test_window_uses_prior_bars_without_returning_them():
    df = generate_synthetic_ohlcv(bars=260, seed=2)
    window = df.index[-40:]
    aligned = chart_indicators_aligned(df, window)
    short = chart_indicators(df.iloc[-40:])
    for key in INDICATOR_KEYS:
        assert len(aligned[key]) == 40
        assert len(short[key]) == 40
    assert aligned["ema21"][0] is not None
    assert aligned["sma200"][-1] is not None
    assert short["ema21"][0] is None
    assert short["sma200"][-1] is None
    full = indicator_frame(df)
    assert aligned["rsi"][-1] == pytest.approx(float(full["rsi"].iloc[-1]))
    assert aligned["macd"][-1] == pytest.approx(float(full["macd"].iloc[-1]))


def test_indicators_are_causal():
    df = generate_synthetic_ohlcv(bars=240, seed=6)
    full = indicator_frame(df)
    t = 180
    poisoned = df.copy()
    for col in ("Open", "High", "Low", "Close"):
        poisoned.iloc[t + 1 :, poisoned.columns.get_loc(col)] *= 1.25
    prefix = indicator_frame(poisoned)
    pd.testing.assert_frame_equal(
        full.iloc[: t + 1],
        prefix.iloc[: t + 1],
        check_exact=False,
        rtol=1e-10,
        atol=1e-10,
    )


def test_empty_frame_is_empty_not_invented():
    empty = chart_indicators(pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))
    assert set(empty) == set(INDICATOR_KEYS)
    assert all(values == [] for values in empty.values())

