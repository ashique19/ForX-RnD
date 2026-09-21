"""Feature engineering and labeling (no leakage).

Label schemes
-------------
``triple_barrier`` (default)
    Decision at bar ``t`` (features use data at/before ``t`` only).
    Simulated fill is the **next bar open** ``Open[t+1]``.
    From that fill, walk the next ``horizon`` bars' High/Low (or Close):

        BUY  if the upper barrier (fill + tp_atr * ATR[t]) is touched first
        SELL if the lower barrier (fill - sl_atr * ATR[t]) is touched first
        HOLD if neither barrier is touched before the vertical barrier
             (or both are touched in the same bar — ambiguous path)

    ATR[t] is causal (Wilder average of true range up to t). Barrier *hits*
    use future OHLC; that is the target, never a feature.

``forward_return`` (legacy)
    ``forward_return[t] = Close[t+N] / Close[t] - 1``
    BUY / SELL / HOLD vs ``label_threshold``. Uses same-bar close as the
    reference price (more optimistic than next-open).

Features at t use only information available at or before t.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

LABEL_MAP = {"SELL": 0, "HOLD": 1, "BUY": 2}
INV_LABEL_MAP = {v: k for k, v in LABEL_MAP.items()}
REQUIRED_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


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


def true_range_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR; uses High/Low/Close up to the current bar only."""
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


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range_atr(df, period)


def _rolling_z(s: pd.Series, window: int) -> pd.Series:
    mu = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std()
    return (s - mu) / sd.replace(0, np.nan)


def build_features(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """Return feature frame aligned to df index. All features causal."""
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    out = pd.DataFrame(index=df.index)

    # Multi-horizon close-to-close returns (past only)
    for n in (1, 3, 6, 12, 24):
        out[f"ret_{n}"] = close.pct_change(n)

    for w in cfg.get("sma_windows", [10, 20, 50]):
        sma = _sma(close, int(w))
        out[f"sma_{w}_ratio"] = close / sma - 1.0

    ema_fast = None
    ema_slow = None
    for w in cfg.get("ema_windows", [12, 26]):
        ema = _ema(close, int(w))
        out[f"ema_{w}_ratio"] = close / ema - 1.0
        if ema_fast is None:
            ema_fast = ema
        else:
            ema_slow = ema

    if ema_fast is not None and ema_slow is not None:
        out["macd_norm"] = (ema_fast - ema_slow) / close.replace(0, np.nan)

    rsi_p = int(cfg.get("rsi_period", 14))
    out["rsi"] = _rsi(close, rsi_p) / 100.0

    atr_p = int(cfg.get("atr_period", 14))
    atr = true_range_atr(df, atr_p)
    out["atr_pct"] = atr / close.replace(0, np.nan)

    vol_w = int(cfg.get("vol_window", 20))
    out["volatility"] = out["ret_1"].rolling(vol_w, min_periods=vol_w).std()
    vol_long_w = int(cfg.get("vol_long_window", 100))
    vol_long = out["ret_1"].rolling(vol_long_w, min_periods=vol_long_w).std()
    out["vol_regime"] = out["volatility"] / vol_long.replace(0, np.nan)

    # ATR-normalized recent moves
    atr_pct = out["atr_pct"].replace(0, np.nan)
    for n in (1, 6, 12):
        out[f"ret_{n}_atr"] = out[f"ret_{n}"] / atr_pct

    out["range_pct"] = (high - low) / close.replace(0, np.nan)
    out["range_pct_z"] = _rolling_z(out["range_pct"], vol_w)
    out["body_pct"] = (close - df["Open"]) / close.replace(0, np.nan)

    # Location in recent range (causal rolling incl. current bar)
    for w in cfg.get("range_windows", [20, 50]):
        w = int(w)
        roll_high = high.rolling(w, min_periods=w).max()
        roll_low = low.rolling(w, min_periods=w).min()
        out[f"dist_high_{w}"] = close / roll_high.replace(0, np.nan) - 1.0
        out[f"dist_low_{w}"] = close / roll_low.replace(0, np.nan) - 1.0
        span = (roll_high - roll_low).replace(0, np.nan)
        out[f"range_pos_{w}"] = (close - roll_low) / span

    sma20 = _sma(close, 20)
    out["sma20_slope"] = sma20 / sma20.shift(5) - 1.0
    # Momentum vs stretch: same sign => trend continuation; opposite => mean-reversion
    if "sma_20_ratio" in out.columns:
        out["mom_mr"] = np.sign(out["ret_6"].fillna(0.0)) * out["sma_20_ratio"]

    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    hour = idx.hour.to_numpy()
    dow = idx.dayofweek.to_numpy()
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    out["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    out["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)
    # FX sessions in UTC (overlap is intentional: London/NY is a real regime)
    out["sess_asia"] = ((hour >= 0) & (hour < 7)).astype(float)
    out["sess_london"] = ((hour >= 7) & (hour < 16)).astype(float)
    out["sess_ny"] = ((hour >= 13) & (hour < 21)).astype(float)

    vol = df["Volume"].astype(float)
    if float(vol.std() or 0.0) > 0:
        out["vol_z"] = _rolling_z(vol, vol_w)

    return out


def _barrier_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    b = cfg.get("barrier") or {}
    return {
        "tp_atr": float(b.get("tp_atr", 2.0)),
        "sl_atr": float(b.get("sl_atr", 2.0)),
        "timeout_label": str(b.get("timeout_label", "hold")).lower(),
        "path": str(b.get("path", "high_low")).lower(),
    }


def triple_barrier_labels(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    horizon: int,
    tp_atr: float,
    sl_atr: float,
    *,
    path: str = "high_low",
    timeout_label: str = "hold",
    entry_timing: str = "next_open",
) -> np.ndarray:
    """Vector of {0,1,2,nan} labels. Future OHLC is used only as the target.

    ``entry_timing='next_open'``: fill at Open[t+1], scan bars t+1..t+horizon.
    ``entry_timing='same_close'``: fill at Close[t], scan bars t+1..t+horizon.
    """
    n = len(close)
    labels = np.full(n, np.nan, dtype=float)
    use_hl = path != "close"
    timeout_sign = timeout_label == "sign"

    for t in range(n):
        a = atr[t]
        if not np.isfinite(a) or a <= 0:
            continue
        if entry_timing == "same_close":
            entry = close[t]
            start = t + 1
        else:
            if t + 1 >= n:
                continue
            entry = open_[t + 1]
            start = t + 1
        end = start + horizon  # exclusive
        if end > n or not np.isfinite(entry) or entry <= 0:
            continue

        up = entry + tp_atr * a
        dn = entry - sl_atr * a
        lab = float(LABEL_MAP["HOLD"])
        hit = False
        for i in range(start, end):
            px_up = high[i] if use_hl else close[i]
            px_dn = low[i] if use_hl else close[i]
            hit_up = px_up >= up
            hit_dn = px_dn <= dn
            if hit_up and hit_dn:
                lab = float(LABEL_MAP["HOLD"])
                hit = True
                break
            if hit_up:
                lab = float(LABEL_MAP["BUY"])
                hit = True
                break
            if hit_dn:
                lab = float(LABEL_MAP["SELL"])
                hit = True
                break
        if not hit and timeout_sign:
            exit_px = close[end - 1]
            fwd = exit_px / entry - 1.0
            if fwd > 0:
                lab = float(LABEL_MAP["BUY"])
            elif fwd < 0:
                lab = float(LABEL_MAP["SELL"])
        labels[t] = lab
    return labels


def build_labels(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.Series:
    scheme = str(cfg.get("label_scheme", "triple_barrier")).lower()
    horizon = int(cfg.get("horizon", 8))
    entry_timing = str(cfg.get("entry_timing", "next_open")).lower()
    atr_p = int(cfg.get("atr_period", 14))

    if scheme == "forward_return":
        thr = float(cfg.get("label_threshold", 0.0005))
        if entry_timing == "next_open":
            entry = df["Open"].shift(-1)
            exit_px = df["Close"].shift(-horizon)
            fwd = exit_px / entry - 1.0
        else:
            fwd = df["Close"].shift(-horizon) / df["Close"] - 1.0
        labels = pd.Series(float(LABEL_MAP["HOLD"]), index=df.index, dtype=float)
        labels = labels.mask(fwd > thr, float(LABEL_MAP["BUY"]))
        labels = labels.mask(fwd < -thr, float(LABEL_MAP["SELL"]))
        labels.iloc[-max(horizon, 1) :] = np.nan
        labels.name = "label"
        return labels

    b = _barrier_cfg(cfg)
    atr = true_range_atr(df, atr_p).to_numpy(dtype=float)
    raw = triple_barrier_labels(
        df["Open"].to_numpy(dtype=float),
        df["High"].to_numpy(dtype=float),
        df["Low"].to_numpy(dtype=float),
        df["Close"].to_numpy(dtype=float),
        atr,
        horizon,
        b["tp_atr"],
        b["sl_atr"],
        path=b["path"],
        timeout_label=b["timeout_label"],
        entry_timing=entry_timing,
    )
    labels = pd.Series(raw, index=df.index, name="label")
    return labels


def make_dataset(df: pd.DataFrame, cfg: dict[str, Any]) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame]:
    """Features X, labels y, and raw OHLCV aligned after dropping NaNs."""
    feats = build_features(df, cfg)
    labels = build_labels(df, cfg)
    combined = feats.join(labels).join(df[REQUIRED_OHLCV])
    combined = combined.dropna()
    feature_cols = list(feats.columns)
    X = combined[feature_cols]
    y = combined["label"].astype(int)
    ohlcv = combined[REQUIRED_OHLCV]
    return X, y, ohlcv


def label_distribution(y: pd.Series) -> dict[str, float]:
    counts = y.value_counts(normalize=True)
    out = {}
    for name, code in LABEL_MAP.items():
        out[name.lower()] = float(counts.get(code, 0.0))
    out["n"] = int(len(y))
    return out
