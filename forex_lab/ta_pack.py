"""Extra causal technical indicators (pandas-ta-style subset).

Default backend is native pandas/numpy so CI does not need numba / pandas-ta.
If ``pandas-ta`` is installed, set ``feature_extras.pandas_ta.backend: pandas_ta``.

Every column at bar ``t`` uses High/Low/Close at or before ``t`` only.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

DEFAULT_INDICATORS = ("stoch", "adx", "bbands", "cci", "willr", "roc", "kc")


def pandas_ta_cfg(extra: dict[str, Any]) -> dict[str, Any]:
    raw = extra.get("pandas_ta")
    if raw is True:
        return {"enabled": True}
    if not raw:
        return {"enabled": False}
    if isinstance(raw, dict):
        return dict(raw)
    return {"enabled": False}


def _rma(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat(
        [
            (df["High"] - df["Low"]).abs(),
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    return _rma(_true_range(df), period)


def _native_stoch(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int, d: int, smooth_k: int
) -> pd.DataFrame:
    lowest = low.rolling(k, min_periods=k).min()
    highest = high.rolling(k, min_periods=k).max()
    raw_k = 100.0 * (close - lowest) / (highest - lowest).replace(0, np.nan)
    stoch_k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    stoch_d = stoch_k.rolling(d, min_periods=d).mean()
    return pd.DataFrame({"ta_stoch_k": stoch_k / 100.0, "ta_stoch_d": stoch_d / 100.0})


def _native_adx(df: pd.DataFrame, period: int) -> pd.DataFrame:
    high, low = df["High"], df["Low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(
        np.where((up > down) & (up > 0), up, 0.0), index=df.index, dtype=float
    )
    minus_dm = pd.Series(
        np.where((down > up) & (down > 0), down, 0.0), index=df.index, dtype=float
    )
    atr = _atr(df, period).replace(0, np.nan)
    plus_di = 100.0 * _rma(plus_dm, period) / atr
    minus_di = 100.0 * _rma(minus_dm, period) / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = _rma(dx, period)
    return pd.DataFrame(
        {
            "ta_adx": adx / 100.0,
            "ta_plus_di": plus_di / 100.0,
            "ta_minus_di": minus_di / 100.0,
        }
    )


def _native_bbands(close: pd.Series, window: int, n_std: float) -> pd.DataFrame:
    mid = close.rolling(window, min_periods=window).mean()
    sd = close.rolling(window, min_periods=window).std()
    upper = mid + n_std * sd
    lower = mid - n_std * sd
    span = (upper - lower).replace(0, np.nan)
    return pd.DataFrame(
        {
            "ta_bb_pctb": (close - lower) / span,
            "ta_bb_bw": span / mid.replace(0, np.nan),
        }
    )


def _native_cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tp = (high + low + close) / 3.0
    sma = tp.rolling(period, min_periods=period).mean()
    mad = tp.rolling(period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    return (tp - sma) / (0.015 * mad.replace(0, np.nan))


def _native_willr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    lowest = low.rolling(period, min_periods=period).min()
    highest = high.rolling(period, min_periods=period).max()
    raw = -100.0 * (highest - close) / (highest - lowest).replace(0, np.nan)
    return (raw + 100.0) / 100.0


def _native_kc(df: pd.DataFrame, window: int, scalar: float) -> pd.Series:
    ema = df["Close"].ewm(span=window, adjust=False, min_periods=window).mean()
    atr = _atr(df, window)
    upper = ema + scalar * atr
    lower = ema - scalar * atr
    return (df["Close"] - lower) / (upper - lower).replace(0, np.nan)


def _try_pandas_ta(df: pd.DataFrame, cfg: dict[str, Any], wanted: set[str]) -> pd.DataFrame | None:
    """Best-effort pandas-ta backend. Returns None if import or calls fail."""
    try:
        import pandas_ta as ta  # type: ignore
    except Exception:
        return None
    high, low, close = df["High"], df["Low"], df["Close"]
    out = pd.DataFrame(index=df.index)
    try:
        if "stoch" in wanted:
            k = int(cfg.get("stoch_k", 14) or 14)
            d = int(cfg.get("stoch_d", 3) or 3)
            smooth = int(cfg.get("stoch_smooth_k", 3) or 3)
            block = ta.stoch(high, low, close, k=k, d=d, smooth_k=smooth)
            if block is not None and not block.empty:
                cols = list(block.columns)
                kcol = next((c for c in cols if str(c).upper().startswith("STOCHK")), cols[0])
                dcol = next((c for c in cols if str(c).upper().startswith("STOCHD")), cols[min(1, len(cols) - 1)])
                out["ta_stoch_k"] = block[kcol].astype(float) / 100.0
                out["ta_stoch_d"] = block[dcol].astype(float) / 100.0
        if "adx" in wanted:
            period = int(cfg.get("adx_period", 14) or 14)
            block = ta.adx(high, low, close, length=period)
            if block is not None and not block.empty:
                cols = {str(c).upper(): c for c in block.columns}
                adx_c = next((cols[c] for c in cols if c.startswith("ADX_") and "ADXR" not in c), None)
                dmp_c = next((cols[c] for c in cols if c.startswith("DMP")), None)
                dmn_c = next((cols[c] for c in cols if c.startswith("DMN")), None)
                if adx_c is not None:
                    out["ta_adx"] = block[adx_c].astype(float) / 100.0
                if dmp_c is not None:
                    out["ta_plus_di"] = block[dmp_c].astype(float) / 100.0
                if dmn_c is not None:
                    out["ta_minus_di"] = block[dmn_c].astype(float) / 100.0
        if "bbands" in wanted:
            window = int(cfg.get("bb_window", 20) or 20)
            n_std = float(cfg.get("bb_std", 2.0) or 2.0)
            block = ta.bbands(close, length=window, std=n_std)
            if block is not None and not block.empty:
                cols = {str(c).upper(): c for c in block.columns}
                pctb = next((cols[c] for c in cols if c.startswith("BBP")), None)
                bw = next((cols[c] for c in cols if c.startswith("BBB")), None)
                if pctb is not None:
                    out["ta_bb_pctb"] = block[pctb].astype(float)
                if bw is not None:
                    out["ta_bb_bw"] = block[bw].astype(float)
        if "cci" in wanted:
            period = int(cfg.get("cci_period", 20) or 20)
            series = ta.cci(high, low, close, length=period)
            if series is not None:
                out["ta_cci"] = pd.Series(series, index=df.index).astype(float) / 200.0
        if "willr" in wanted:
            period = int(cfg.get("willr_period", 14) or 14)
            series = ta.willr(high, low, close, length=period)
            if series is not None:
                raw = pd.Series(series, index=df.index).astype(float)
                out["ta_willr"] = (raw + 100.0) / 100.0
        if "roc" in wanted:
            period = int(cfg.get("roc_period", 12) or 12)
            series = ta.roc(close, length=period)
            if series is not None:
                out["ta_roc"] = pd.Series(series, index=df.index).astype(float) / 100.0
        if "kc" in wanted:
            # pandas-ta kc column names vary; native formula is the documented one
            window = int(cfg.get("kc_window", 20) or 20)
            scalar = float(cfg.get("kc_atr", 1.5) or 1.5)
            out["ta_kc_pos"] = _native_kc(df, window, scalar)
    except Exception:
        return None
    return out if len(out.columns) else None


def _native_pack(df: pd.DataFrame, cfg: dict[str, Any], wanted: set[str]) -> pd.DataFrame:
    high, low, close = df["High"], df["Low"], df["Close"]
    parts: list[pd.DataFrame] = []
    if "stoch" in wanted:
        parts.append(
            _native_stoch(
                high,
                low,
                close,
                int(cfg.get("stoch_k", 14) or 14),
                int(cfg.get("stoch_d", 3) or 3),
                int(cfg.get("stoch_smooth_k", 3) or 3),
            )
        )
    if "adx" in wanted:
        parts.append(_native_adx(df, int(cfg.get("adx_period", 14) or 14)))
    if "bbands" in wanted:
        parts.append(
            _native_bbands(
                close,
                int(cfg.get("bb_window", 20) or 20),
                float(cfg.get("bb_std", 2.0) or 2.0),
            )
        )
    if "cci" in wanted:
        cci = _native_cci(high, low, close, int(cfg.get("cci_period", 20) or 20))
        parts.append(pd.DataFrame({"ta_cci": cci / 200.0}))
    if "willr" in wanted:
        parts.append(
            pd.DataFrame(
                {"ta_willr": _native_willr(high, low, close, int(cfg.get("willr_period", 14) or 14))}
            )
        )
    if "roc" in wanted:
        period = int(cfg.get("roc_period", 12) or 12)
        parts.append(pd.DataFrame({"ta_roc": close.pct_change(period)}))
    if "kc" in wanted:
        parts.append(
            pd.DataFrame(
                {
                    "ta_kc_pos": _native_kc(
                        df,
                        int(cfg.get("kc_window", 20) or 20),
                        float(cfg.get("kc_atr", 1.5) or 1.5),
                    )
                }
            )
        )
    if not parts:
        return pd.DataFrame(index=df.index)
    out = parts[0]
    for extra in parts[1:]:
        out = out.join(extra)
    return out


def add_pandas_ta_features(
    out: pd.DataFrame,
    df: pd.DataFrame,
    extra: dict[str, Any],
) -> None:
    """Append TA-pack columns onto ``out`` when enabled. No-op if disabled."""
    cfg = pandas_ta_cfg(extra)
    if not bool(cfg.get("enabled", False)):
        return
    raw_inds = cfg.get("indicators") or list(DEFAULT_INDICATORS)
    wanted = {str(x).strip().lower() for x in raw_inds if str(x).strip()}
    if not wanted:
        return
    backend = str(cfg.get("backend") or "native").strip().lower()
    block = None
    if backend in {"pandas_ta", "pandas-ta", "ta"}:
        block = _try_pandas_ta(df, cfg, wanted)
    if block is None:
        block = _native_pack(df, cfg, wanted)
    for col in block.columns:
        out[col] = block[col].to_numpy()
