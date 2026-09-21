"""Feature engineering and labeling (no leakage).

Label scheme
------------
For each bar t, compute the forward return over N=`horizon` bars:

    forward_return[t] = Close[t+N] / Close[t] - 1

Then:
    BUY  if forward_return >  +label_threshold
    SELL if forward_return <  -label_threshold
    HOLD otherwise

Features at t use only information available at or before t (shifted
indicators; forward return is the target and is never used as a feature).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

LABEL_MAP = {"SELL": 0, "HOLD": 1, "BUY": 2}
INV_LABEL_MAP = {v: k for k, v in LABEL_MAP.items()}


def _sma(s: pd.Series, w: int) -> pd.Series:
    return s.rolling(w, min_periods=w).mean()


def _ema(s: pd.Series, w: int) -> pd.Series:
    return s.ewm(span=w, adjust=False, min_periods=w).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            (df["High"] - df["Low"]).abs(),
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def build_features(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Return feature frame aligned to df index. All features causal."""
    close = df["Close"]
    out = pd.DataFrame(index=df.index)
    out["ret_1"] = close.pct_change(1)
    out["ret_3"] = close.pct_change(3)
    out["ret_6"] = close.pct_change(6)

    for w in cfg.get("sma_windows", [10, 20, 50]):
        sma = _sma(close, int(w))
        out[f"sma_{w}_ratio"] = close / sma - 1.0

    for w in cfg.get("ema_windows", [12, 26]):
        ema = _ema(close, int(w))
        out[f"ema_{w}_ratio"] = close / ema - 1.0

    rsi_p = int(cfg.get("rsi_period", 14))
    out["rsi"] = _rsi(close, rsi_p) / 100.0

    atr_p = int(cfg.get("atr_period", 14))
    atr = _atr(df, atr_p)
    out["atr_pct"] = atr / close

    vol_w = int(cfg.get("vol_window", 20))
    out["volatility"] = out["ret_1"].rolling(vol_w, min_periods=vol_w).std()

    # Calendar features (available at bar open/close time; no future info)
    out["hour_sin"] = np.sin(2 * np.pi * df.index.hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * df.index.hour / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * df.index.dayofweek / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * df.index.dayofweek / 7.0)

    return out


def build_labels(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.Series:
    horizon = int(cfg.get("horizon", 4))
    thr = float(cfg.get("label_threshold", 0.0005))
    fwd = df["Close"].shift(-horizon) / df["Close"] - 1.0
    labels = pd.Series(float(LABEL_MAP["HOLD"]), index=df.index, dtype=float)
    labels = labels.mask(fwd > thr, float(LABEL_MAP["BUY"]))
    labels = labels.mask(fwd < -thr, float(LABEL_MAP["SELL"]))
    labels.name = "label"
    # Last `horizon` bars have unknown forward return
    labels.iloc[-horizon:] = np.nan
    return labels


def make_dataset(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Features X, labels y, and raw OHLCV aligned after dropping NaNs."""
    feats = build_features(df, cfg)
    labels = build_labels(df, cfg)
    combined = feats.join(labels).join(df[REQUIRED_OHLCV])
    combined = combined.dropna()
    feature_cols = [c for c in feats.columns]
    X = combined[feature_cols]
    y = combined["label"].astype(int)
    ohlcv = combined[REQUIRED_OHLCV]
    return X, y, ohlcv


REQUIRED_OHLCV = ["Open", "High", "Low", "Close", "Volume"]
