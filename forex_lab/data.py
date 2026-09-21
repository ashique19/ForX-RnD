"""OHLCV data fetch (yfinance) with synthetic fallback."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.config_loader import pair_to_ticker
from forex_lab.console import safe_print
from forex_lab.paths import resolve_under_root


REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def data_path(pair: str, cfg: dict[str, Any], interval: str | None = None) -> Path:
    data_dir = resolve_under_root(cfg.get("paths", {}).get("data_dir", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    iv = interval or cfg.get("interval", "1h")
    return data_dir / f"{pair.upper()}_{iv}.csv"


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    # yfinance sometimes returns Adj Close etc.
    rename = {c: c.strip().title() if isinstance(c, str) else c for c in df.columns}
    # Keep standard names
    colmap = {}
    for c in df.columns:
        cl = str(c).lower()
        if cl == "open":
            colmap[c] = "Open"
        elif cl == "high":
            colmap[c] = "High"
        elif cl == "low":
            colmap[c] = "Low"
        elif cl == "close":
            colmap[c] = "Close"
        elif cl == "volume":
            colmap[c] = "Volume"
    out = df.rename(columns=colmap)
    missing = [c for c in REQUIRED_COLS if c not in out.columns]
    if missing:
        raise ValueError(f"OHLCV missing columns: {missing}")
    out = out[REQUIRED_COLS].copy()
    out.index = pd.to_datetime(out.index, utc=True).tz_convert(None)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    return out


def generate_synthetic_ohlcv(
    pair: str = "EURUSD",
    bars: int = 5000,
    interval: str = "1h",
    seed: int = 42,
    start: str = "2022-01-01",
) -> pd.DataFrame:
    """Geometric Brownian motion style FX series for offline demos."""
    rng = np.random.default_rng(seed + sum(ord(c) for c in pair.upper()))
    freq = "h" if interval.endswith("h") else "D"
    idx = pd.date_range(start=start, periods=bars, freq=freq)
    # Base level by pair
    base = 150.0 if "JPY" in pair.upper() else 1.10
    mu = 0.0
    sigma = 0.0008 if freq == "h" else 0.004
    rets = rng.normal(mu, sigma, size=bars)
    # Mild mean reversion + intraday seasonality
    hour = idx.hour.to_numpy() if hasattr(idx, "hour") else np.zeros(bars)
    rets = rets + 0.00005 * np.sin(2 * np.pi * hour / 24.0)
    close = base * np.exp(np.cumsum(rets))
    noise = np.abs(rng.normal(0, sigma * 0.5, size=bars))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) + noise
    low = np.minimum(open_, close) - noise
    volume = rng.integers(800, 5000, size=bars)
    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )
    df.index.name = "Datetime"
    return df


def fetch_ohlcv(
    pair: str,
    cfg: dict[str, Any],
    period: str | None = None,
    interval: str | None = None,
    force_synthetic: bool = False,
) -> tuple[pd.DataFrame, str]:
    """Download OHLCV; on failure generate synthetic data.

    Returns (dataframe, source) where source is 'yfinance' or 'synthetic'.
    """
    period = period or cfg.get("period", "2y")
    interval = interval or cfg.get("interval", "1h")
    out_path = data_path(pair, cfg, interval)

    if force_synthetic:
        if out_path.exists():
            safe_print(f"[fetch] warning: overwriting existing {out_path} with synthetic")
        df = generate_synthetic_ohlcv(pair=pair, interval=interval)
        df.to_csv(out_path)
        return df, "synthetic"

    ticker = pair_to_ticker(pair, cfg)
    try:
        import yfinance as yf

        raw = yf.download(
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        if raw is None or raw.empty:
            raise RuntimeError("empty yfinance response")
        df = _normalize_ohlcv(raw)
        if len(df) < 100:
            raise RuntimeError(f"too few bars: {len(df)}")
        # Save before any caller prints — a console encoding error must not
        # look like a fetch failure or trigger a synthetic overwrite.
        df.to_csv(out_path)
        return df, "yfinance"
    except Exception as exc:  # noqa: BLE001 — intentional fallback
        safe_print(f"[fetch] yfinance failed ({exc}); using synthetic OHLCV")
        df = generate_synthetic_ohlcv(pair=pair, interval=interval)
        df.to_csv(out_path)
        return df, "synthetic"


def load_ohlcv(pair: str, cfg: dict[str, Any], interval: str | None = None) -> pd.DataFrame:
    path = data_path(pair, cfg, interval)
    if not path.exists():
        df, _ = fetch_ohlcv(pair, cfg, interval=interval)
        return df
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return _normalize_ohlcv(df)
