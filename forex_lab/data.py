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


def csv_mtime_utc(pair: str, cfg: dict[str, Any], interval: str | None = None):
    """Filesystem mtime of the pair CSV (last successful write), or None."""
    path = data_path(pair, cfg, interval)
    if not path.exists() or path.stat().st_size <= 0:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)


def try_yfinance_refresh(
    pair: str,
    cfg: dict[str, Any],
    period: str | None = None,
    interval: str | None = None,
    *,
    incremental: bool = False,
) -> tuple[pd.DataFrame | None, str]:
    """Refresh OHLCV from yfinance only. Never writes synthetic prices.

    On success, overwrites the pair CSV and returns ``(df, "yfinance")``.
    On failure, leaves any existing CSV untouched and returns ``(None, reason)``.

    ``incremental=True`` downloads a short window (board default ``5d``) and
    merges onto the existing cache so realtime ticks do not re-pull 2y.
    """
    interval = interval or cfg.get("interval", "1h")
    out_path = data_path(pair, cfg, interval)
    board = dict(cfg.get("board") or {})
    if incremental:
        period = period or str(board.get("incremental_period") or "5d")
        min_bars = int(board.get("incremental_min_bars") or 20)
    else:
        period = period or cfg.get("period", "2y")
        min_bars = 100
    try:
        ticker = pair_to_ticker(pair, cfg)
    except KeyError as exc:
        return None, str(exc)
    existing = None
    if incremental and out_path.exists():
        try:
            existing = load_cached_ohlcv(pair, cfg, interval)
        except Exception:
            existing = None
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
            return None, "yfinance empty"
        df = _normalize_ohlcv(raw)
        if existing is not None and not existing.empty:
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        if len(df) < min_bars:
            return None, f"too few bars: {len(df)}"
        df.to_csv(out_path)
        return df, "yfinance"
    except Exception as exc:  # noqa: BLE001 — board must not invent prices
        text = str(exc)
        low = text.lower()
        if "429" in text or "too many" in low or "rate limit" in low:
            return None, f"yfinance rate limited ({exc})"
        return None, f"yfinance failed ({exc})"


def load_cached_ohlcv(
    pair: str, cfg: dict[str, Any], interval: str | None = None
) -> pd.DataFrame | None:
    """Load on-disk OHLCV. Does not fetch and never generates synthetic bars."""
    path = data_path(pair, cfg, interval)
    if not path.exists() or path.stat().st_size <= 0:
        return None
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return _normalize_ohlcv(df)


# A daily cache is usable once it clears the same short-history bar as freshness.
MIN_CACHE_BARS = 20
SOURCE_YFINANCE = "yfinance"
SOURCE_RESAMPLED_FROM_1H = "resampled_from_1h"


def cache_source_path(pair: str, cfg: dict[str, Any], interval: str | None = None) -> Path:
    """Sidecar next to the CSV: ``yfinance`` or ``resampled_from_1h``."""
    return data_path(pair, cfg, interval).with_suffix(".source")


def write_cache_source(pair: str, cfg: dict[str, Any], interval: str, source: str) -> None:
    path = cache_source_path(pair, cfg, interval)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(source).strip() + "\n", encoding="utf-8")


def read_cache_source(pair: str, cfg: dict[str, Any], interval: str | None = None) -> str:
    path = cache_source_path(pair, cfg, interval)
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def aggregate_hourly_to_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate 1h OHLCV into UTC calendar-day bars.

    Path used when the provider has no usable 1d download. Each day is
    open=first, high=max, low=min, close=last, volume=sum of the 1h bars
    already on disk. The current UTC day is kept as a partial bar. No
    synthetic prices are added.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=REQUIRED_COLS)
    base = frame.copy()
    base.index = pd.to_datetime(base.index, utc=True).tz_convert(None)
    base = base.sort_index()
    base = base[~base.index.duplicated(keep="last")]
    daily = (
        base.resample("1D", label="left", closed="left")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    daily.index.name = "Datetime"
    return daily[REQUIRED_COLS]


def cache_bar_count(pair: str, cfg: dict[str, Any], interval: str | None = None) -> int:
    try:
        frame = load_cached_ohlcv(pair, cfg, interval)
    except Exception:
        return 0
    if frame is None or frame.empty:
        return 0
    return int(len(frame))


def resample_daily_cache_from_hourly(
    pair: str,
    cfg: dict[str, Any],
    *,
    min_bars: int = MIN_CACHE_BARS,
) -> tuple[pd.DataFrame | None, str]:
    """Write ``{PAIR}_1d.csv`` from the 1h cache. Does not call the provider.

    Returns ``(frame, note)``. ``note`` is ``resampled_from_1h`` on success,
    otherwise why the 1h cache could not fill a daily file.
    """
    try:
        hourly = load_cached_ohlcv(pair, cfg, "1h")
    except Exception as exc:
        return None, f"1h cache unreadable ({exc})"
    if hourly is None or hourly.empty:
        return None, "no 1h cache to resample"
    try:
        daily = aggregate_hourly_to_daily(hourly)
    except Exception as exc:
        return None, f"resample failed ({exc})"
    if daily.empty:
        return None, "1h cache produced no daily bars"
    if len(daily) < min_bars:
        return None, f"1h cache cannot build {min_bars} daily bars (have {len(daily)})"
    daily.to_csv(data_path(pair, cfg, "1d"))
    write_cache_source(pair, cfg, "1d", SOURCE_RESAMPLED_FROM_1H)
    return daily, SOURCE_RESAMPLED_FROM_1H


def ensure_interval_ohlcv(
    pair: str,
    cfg: dict[str, Any],
    interval: str | None = None,
    *,
    incremental: bool = True,
    period: str | None = None,
    refresh=None,
) -> tuple[pd.DataFrame | None, str, str]:
    """Fill one OHLCV cache without synthetic prices.

    Prefer a real provider download (``try_yfinance_refresh``). A missing or
    short **1d** cache that the provider cannot fill is aggregated from the
    1h cache (see ``aggregate_hourly_to_daily``). An existing usable cache is
    left untouched when the download fails — it is not replaced by a resample.

    A first fill is a full-period download. Incremental 1d (a few sessions)
    is shorter than the minimum bar count and would never create the file.

    Returns ``(frame, source, reason)`` where ``source`` is ``yfinance``,
    ``resampled_from_1h``, ``cache`` (download failed, previous file kept),
    or ``missing``. ``reason`` is empty on a provider hit, the provider error
    when daily bars were aggregated from 1h, and the failure text otherwise.
    """
    iv = str(interval or cfg.get("interval") or "1h")
    usable = cache_bar_count(pair, cfg, iv) >= MIN_CACHE_BARS
    do_refresh = refresh or try_yfinance_refresh
    fetched, reason = do_refresh(
        pair,
        cfg,
        period=period,
        interval=iv,
        incremental=bool(incremental and usable),
    )
    if fetched is not None:
        if isinstance(fetched, pd.DataFrame):
            write_cache_source(pair, cfg, iv, SOURCE_YFINANCE)
        return fetched, SOURCE_YFINANCE, ""
    provider_reason = str(reason or "").strip()
    if iv == "1d" and not usable:
        resampled, resample_note = resample_daily_cache_from_hourly(pair, cfg)
        if resampled is not None:
            return resampled, SOURCE_RESAMPLED_FROM_1H, provider_reason
        parts = [part for part in (provider_reason, str(resample_note or "").strip()) if part]
        return None, "missing", "; ".join(parts) or "no OHLCV cache"
    if usable:
        return None, "cache", provider_reason or "refresh failed"
    return None, "missing", provider_reason or "no OHLCV cache"


def load_ohlcv(pair: str, cfg: dict[str, Any], interval: str | None = None) -> pd.DataFrame:
    path = data_path(pair, cfg, interval)
    if not path.exists():
        df, _ = fetch_ohlcv(pair, cfg, interval=interval)
        return df
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return _normalize_ohlcv(df)
