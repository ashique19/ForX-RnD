"""Causal chart series for the Decision desk.

Computed from cached OHLCV only. Warm-up bars stay NaN — prices are never
filled in, and the returned frame has one row per input bar.

EMA / RSI / ATR reuse ``forex_lab.features``. Stochastic reuses the native
TA-pack formula (14, 3, 3) on a 0–100 scale. Bollinger Bands are the common
chart definition: SMA 20 ± 2 population standard deviations.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from forex_lab.features import _ema, _rsi, _sma, true_range_atr
from forex_lab.ta_pack import _native_stoch

INDICATOR_KEYS: tuple[str, ...] = (
    "ema21",
    "ema50",
    "sma200",
    "bb_mid",
    "bb_upper",
    "bb_lower",
    "rsi",
    "macd",
    "macd_signal",
    "macd_hist",
    "stoch_k",
    "stoch_d",
    "atr",
)

_NEEDED = ("High", "Low", "Close")


def chart_price_digits(pair: str) -> int:
    """Desk price scale: 5 dp FX, 3 dp JPY, 2 dp gold/silver."""
    upper = "".join(ch for ch in str(pair).upper() if ch.isalpha())
    if "JPY" in upper:
        return 3
    if "XAU" in upper or "XAG" in upper:
        return 2
    return 5


def empty_indicators() -> dict[str, list[float | None]]:
    return {key: [] for key in INDICATOR_KEYS}


def _ema_from_line(line: pd.Series, span: int) -> pd.Series:
    """EMA of a line that may start with NaN (MACD signal)."""
    out = pd.Series(np.nan, index=line.index, dtype=float)
    valid = line.dropna()
    if valid.empty:
        return out
    out.loc[valid.index] = valid.ewm(span=span, adjust=False, min_periods=span).mean()
    return out


def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """One row per input bar. Leading warm-up values are NaN."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=list(INDICATOR_KEYS))
    index = df.index
    if any(col not in df.columns for col in _NEEDED):
        return pd.DataFrame(np.nan, index=index, columns=list(INDICATOR_KEYS))

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema_from_line(macd, 9)
    mid = _sma(close, 20)
    sd = close.rolling(20, min_periods=20).std(ddof=0)
    stoch = _native_stoch(high, low, close, 14, 3, 3)
    out = pd.DataFrame(
        {
            "ema21": _ema(close, 21),
            "ema50": _ema(close, 50),
            "sma200": _sma(close, 200),
            "bb_mid": mid,
            "bb_upper": mid + 2.0 * sd,
            "bb_lower": mid - 2.0 * sd,
            "rsi": _rsi(close, 14),
            "macd": macd,
            "macd_signal": signal,
            "macd_hist": macd - signal,
            "stoch_k": stoch["ta_stoch_k"] * 100.0,
            "stoch_d": stoch["ta_stoch_d"] * 100.0,
            "atr": true_range_atr(df, 14),
        },
        index=index,
    )
    return out.reindex(columns=list(INDICATOR_KEYS))


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _lists(frame: pd.DataFrame) -> dict[str, list[float | None]]:
    if frame is None or len(frame) == 0:
        return empty_indicators()
    out: dict[str, list[float | None]] = {}
    for key in INDICATOR_KEYS:
        column = frame[key] if key in frame.columns else pd.Series(np.nan, index=frame.index)
        values = [_finite(value) for value in column.to_numpy()]
        if len(values) != len(frame):
            raise RuntimeError(f"{key} length {len(values)} != bars {len(frame)}")
        out[key] = values
    return out


def chart_indicators(df: pd.DataFrame | None) -> dict[str, list[float | None]]:
    if df is None or len(df) == 0:
        return empty_indicators()
    return _lists(indicator_frame(df))


def chart_indicators_aligned(df: pd.DataFrame, index: pd.Index) -> dict[str, list[float | None]]:
    """Indicators for ``index`` using earlier rows of ``df`` only as warm-up.

    The result length is ``len(index)``. Bars before ``index`` are not returned.
    """
    if index is None or len(index) == 0:
        return empty_indicators()
    if df is None or len(df) == 0:
        return {key: [None] * len(index) for key in INDICATOR_KEYS}
    block = indicator_frame(df)
    if index.is_unique and block.index.is_unique:
        sliced = block.reindex(index)
    else:
        sliced = block.iloc[-len(index) :]
        if len(sliced) != len(index):
            sliced = indicator_frame(df.iloc[-len(index) :])
    lists = _lists(sliced)
    for key, values in lists.items():
        if len(values) != len(index):
            raise RuntimeError(f"{key} length {len(values)} != window {len(index)}")
    return lists
