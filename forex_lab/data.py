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
SOURCE_DUKASCOPY = "dukascopy"
SOURCE_YFINANCE_M1 = "yfinance_1m"
SOURCE_RESAMPLED_FROM_1H = "resampled_from_1h"
PROVIDER_SOURCES = frozenset({SOURCE_YFINANCE, SOURCE_DUKASCOPY, SOURCE_YFINANCE_M1})


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




def _joblib_has_vol_z(pair: str, cfg: dict[str, Any], interval: str | None = None) -> bool:
    """True when the Active pair's saved joblib feature list includes ``vol_z``."""
    try:
        from forex_lab.model import model_paths
        import json
    except Exception:
        return False
    mtype = str((cfg.get("model") or {}).get("type") or "xgboost").lower()
    try:
        paths = model_paths(pair, cfg, mtype, interval=interval)
        meta_path = paths.get("meta")
    except Exception:
        return False
    if meta_path is None or not Path(meta_path).is_file():
        return False
    try:
        raw = json.loads(Path(meta_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    feats = raw.get("features") if isinstance(raw, dict) else None
    if not isinstance(feats, list):
        return False
    return "vol_z" in {str(x) for x in feats}


def _flat_volume_value(existing: pd.DataFrame | None) -> float:
    """Constant volume for schema-safe live writes when joblib has no vol_z."""
    if existing is None or existing.empty or "Volume" not in existing.columns:
        return 0.0
    series = pd.to_numeric(existing["Volume"], errors="coerce").dropna()
    if series.empty:
        return 0.0
    # Prefer the mode of the existing cache (yfinance is typically all zeros).
    try:
        mode = series.mode()
        if not mode.empty:
            return float(mode.iloc[0])
    except Exception:
        pass
    return float(series.iloc[-1])


def _align_live_volume_to_model(
    frame: pd.DataFrame,
    pair: str,
    cfg: dict[str, Any],
    interval: str,
    *,
    existing: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Keep denser OHLC; keep real volume only when joblib already has ``vol_z``.

    Dukascopy tick counts make Volume vary. That flips Decision schema to
    expect ``vol_z``. Until a matching retrain persists ``vol_z``, flatten
    volume so the Model strip does not report a schema mismatch. yfinance
    fallback stays flat either way.
    """
    if frame is None or frame.empty or "Volume" not in frame.columns:
        return frame
    out = frame.copy()
    if _joblib_has_vol_z(pair, cfg, interval):
        return out
    flat = _flat_volume_value(existing)
    out["Volume"] = flat
    return out


def _merge_live_ohlcv_parts(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge live OHLCV frames.

    Later parts win OHLC (so yfinance 1m can densify the forming bar). Volume
    is different: a later Volume of 0 / NaN must never clobber an earlier
    positive Dukascopy (or cache) volume. Newer positive volume still wins so
    Dukascopy can update mid-hour tick counts. yfinance volume is used only
    when no denser source has volume and yfinance volume is > 0.
    """
    cleaned: list[pd.DataFrame] = []
    for part in parts:
        if part is None or getattr(part, "empty", True):
            continue
        cleaned.append(_normalize_ohlcv(part))
    if not cleaned:
        return pd.DataFrame(columns=REQUIRED_COLS)
    out = cleaned[0].copy()
    for nxt in cleaned[1:]:
        if nxt.empty:
            continue
        combined = pd.concat([out, nxt])
        winners = combined[~combined.index.duplicated(keep="last")].sort_index()
        prior_vol = pd.to_numeric(out["Volume"], errors="coerce").reindex(winners.index)
        new_vol = pd.to_numeric(nxt["Volume"], errors="coerce").reindex(winners.index)
        new_pos = new_vol.fillna(0.0)
        # Later positive volume wins; otherwise keep prior (incl. prior 0).
        coalesced = new_pos.where(new_pos > 0, prior_vol)
        winners["Volume"] = coalesced.fillna(0.0)
        out = winners
    return out.sort_index()


def try_dukascopy_refresh(
    pair: str,
    cfg: dict[str, Any],
    period: str | None = None,
    interval: str | None = None,
    *,
    incremental: bool = False,
) -> tuple[pd.DataFrame | None, str]:
    """Denser live OHLCV: Dukascopy recent hours + optional 1m forming overlay.

    Dukascopy bi5 often lags the *current* UTC hour, so completed hours alone
    cannot move the forming H1/D1 bar. After merging Dukascopy mid OHLC, this
    also pulls a short yfinance 1m window and aggregates it into the target
    interval so mid-candle High/Low/Close can update. Never writes synthetic
    prices. Does not touch Replay ``data/history/``.
    """
    from forex_lab.history import (
        HistoryError,
        fetch_dukascopy_recent_bars,
        resample_bars,
    )

    _ = period
    iv = str(interval or cfg.get("interval") or "1h")
    out_path = data_path(pair, cfg, iv)
    board = dict(cfg.get("board") or {})
    if iv == "1d":
        lookback = int(board.get("dukascopy_daily_lookback_hours") or 24)
    elif incremental:
        lookback = int(board.get("dukascopy_lookback_hours") or 6)
    else:
        lookback = int(board.get("dukascopy_bootstrap_hours") or 48)
    min_bars = int(board.get("incremental_min_bars") or 20) if incremental else MIN_CACHE_BARS

    existing = None
    if out_path.exists():
        try:
            existing = load_cached_ohlcv(pair, cfg, iv)
        except Exception:
            existing = None

    duka_bars: pd.DataFrame | None = None
    m1_bars: pd.DataFrame | None = None
    duka_err = ""
    m1_err = ""

    # Live Decision budget: never block the ~18s poll on a hung Dukascopy feed.
    duka_budget_s = float(board.get("dukascopy_live_budget_s") or 5.0)

    def _pull_duka() -> pd.DataFrame | None:
        if iv == "1d":
            hourly = fetch_dukascopy_recent_bars(
                pair, lookback_hours=lookback, grain="1h"
            )
            if hourly is None or hourly.empty:
                return None
            cols = [c for c in REQUIRED_COLS if c in hourly.columns]
            return aggregate_hourly_to_daily(hourly[cols])
        grain = "15m" if iv == "15m" else "1h"
        raw = fetch_dukascopy_recent_bars(
            pair, lookback_hours=lookback, grain=grain
        )
        if raw is None or raw.empty:
            return None
        if iv == "4h":
            raw = resample_bars(raw, "4h")
        cols = [c for c in REQUIRED_COLS if c in raw.columns]
        return raw[cols]

    if iv not in {"1h", "15m", "4h", "1d"}:
        return None, f"dukascopy unsupported interval {iv}"

    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

        # shutdown(wait=False): a timed-out Dukascopy pull must not block the
        # Decision poll while bi5 workers finish in the background.
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(_pull_duka)
            try:
                duka_bars = fut.result(timeout=max(1.0, duka_budget_s))
            except FuturesTimeout:
                duka_err = f"dukascopy timeout ({duka_budget_s:.0f}s budget)"
                duka_bars = None
                fut.cancel()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if duka_bars is None and not duka_err:
            duka_err = "dukascopy empty"
    except HistoryError as exc:
        duka_err = f"dukascopy failed ({exc})"
    except Exception as exc:  # noqa: BLE001
        text_err = str(exc)
        low = text_err.lower()
        if "429" in text_err or "503" in text_err or "too many" in low or "rate limit" in low:
            duka_err = f"dukascopy rate limited ({exc})"
        else:
            duka_err = f"dukascopy failed ({exc})"

    # Forming-bar densification: yfinance 1m -> target interval (no synthetic).
    # Dukascopy bi5 often omits the current UTC hour; only then pull 1m.
    # 1m overlay is the reliable mid-candle densifier (~1s). Always try unless disabled.
    if bool(board.get("live_m1_overlay", True)):
        try:
            m1_bars, m1_err = _yfinance_m1_aggregate(pair, cfg, iv)
        except Exception as exc:  # noqa: BLE001
            m1_err = f"yfinance_1m failed ({exc})"
            m1_bars = None
    else:
        m1_err = "yfinance_1m disabled"

    parts: list[pd.DataFrame] = []
    if existing is not None and not existing.empty:
        parts.append(existing)
    used_duka = False
    used_m1 = False
    if duka_bars is not None and not duka_bars.empty:
        parts.append(_normalize_ohlcv(duka_bars))
        used_duka = True
    if m1_bars is not None and not m1_bars.empty:
        parts.append(_normalize_ohlcv(m1_bars))
        used_m1 = True
    if not parts or (existing is not None and len(parts) == 1 and not used_duka and not used_m1):
        reason = duka_err or m1_err or "dukascopy empty"
        if duka_err and m1_err:
            reason = f"{duka_err}; {m1_err}"
        return None, reason

    # Later parts win OHLC; never let yfinance_1m Volume=0 clobber Dukascopy/cache.
    df = _merge_live_ohlcv_parts(parts)
    if len(df) < min_bars:
        return None, f"dukascopy too few bars: {len(df)}"
    # Require an actual denser contribution; otherwise fall through to 1h yfinance.
    if not used_duka and not used_m1:
        return None, duka_err or m1_err or "dukascopy empty"
    # Prefer denser OHLC + real volume when joblib has vol_z; otherwise flatten
    # volume so Decision schema stays matched until a catch-up Train.
    df = _align_live_volume_to_model(df, pair, cfg, iv, existing=existing)
    df.to_csv(out_path)
    source = SOURCE_DUKASCOPY if used_duka else SOURCE_YFINANCE_M1
    write_cache_source(pair, cfg, iv, source)
    return df, source


def _yfinance_m1_aggregate(
    pair: str,
    cfg: dict[str, Any],
    interval: str,
) -> tuple[pd.DataFrame | None, str]:
    """Short yfinance 1m window aggregated into ``interval``. Never synthetic."""
    from forex_lab.config_loader import pair_to_ticker

    iv = str(interval)
    if iv not in {"1h", "15m", "4h", "1d"}:
        return None, f"yfinance_1m unsupported interval {iv}"
    board = dict(cfg.get("board") or {})
    period = str(board.get("m1_period") or "1d")
    try:
        ticker = pair_to_ticker(pair, cfg)
    except KeyError as exc:
        return None, str(exc)
    try:
        import yfinance as yf

        raw = yf.download(
            ticker,
            period=period,
            interval="1m",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        low = text.lower()
        if "429" in text or "too many" in low or "rate limit" in low:
            return None, f"yfinance_1m rate limited ({exc})"
        return None, f"yfinance_1m failed ({exc})"
    if raw is None or raw.empty:
        return None, "yfinance_1m empty"
    try:
        m1 = _normalize_ohlcv(raw)
    except Exception as exc:
        return None, f"yfinance_1m normalize failed ({exc})"
    if m1.empty:
        return None, "yfinance_1m empty"
    rule = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[iv]
    agg = (
        m1.resample(rule, label="left", closed="left")
        .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    if agg.empty:
        return None, "yfinance_1m produced no bars"
    agg.index.name = "Datetime"
    return agg[REQUIRED_COLS], ""


def try_live_refresh(
    pair: str,
    cfg: dict[str, Any],
    period: str | None = None,
    interval: str | None = None,
    *,
    incremental: bool = False,
) -> tuple[pd.DataFrame | None, str]:
    """Primary denser Dukascopy, then yfinance. Never writes synthetic bars."""
    fetched, tag = try_dukascopy_refresh(
        pair, cfg, period=period, interval=interval, incremental=incremental
    )
    if fetched is not None:
        return fetched, tag
    duka_reason = str(tag or "dukascopy failed").strip()
    fetched, tag = try_yfinance_refresh(
        pair, cfg, period=period, interval=interval, incremental=incremental
    )
    if fetched is not None:
        return fetched, tag
    yf_reason = str(tag or "yfinance failed").strip()
    return None, f"dukascopy: {duka_reason}; {yf_reason}"


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

    Prefer a denser live provider (``try_live_refresh``: Dukascopy then
    yfinance). A missing or
    short **1d** cache that the provider cannot fill is aggregated from the
    1h cache (see ``aggregate_hourly_to_daily``). An existing usable cache is
    left untouched when the download fails — it is not replaced by a resample.

    A first fill is a full-period download. Incremental 1d (a few sessions)
    is shorter than the minimum bar count and would never create the file.

    Returns ``(frame, source, reason)`` where ``source`` is ``dukascopy``,
    ``yfinance``, ``resampled_from_1h``, ``cache`` (download failed, previous
    file kept), or ``missing``. ``reason`` is empty on a provider hit, the
    provider error when daily bars were aggregated from 1h, and the failure
    text otherwise.
    """
    iv = str(interval or cfg.get("interval") or "1h")
    usable = cache_bar_count(pair, cfg, iv) >= MIN_CACHE_BARS
    do_refresh = refresh or try_live_refresh
    fetched, tag = do_refresh(
        pair,
        cfg,
        period=period,
        interval=iv,
        incremental=bool(incremental and usable),
    )
    if fetched is not None:
        source = tag if tag in PROVIDER_SOURCES else SOURCE_YFINANCE
        if isinstance(fetched, pd.DataFrame):
            write_cache_source(pair, cfg, iv, source)
        return fetched, source, ""
    provider_reason = str(tag or "").strip()
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
