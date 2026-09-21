"""FRED macro features, aligned to bar time without leakage.

A FRED observation dated calendar day ``D`` is treated as published *after*
that day's close. It becomes available at ``D + lag_days`` (default 1) 00:00
and is then forward-filled onto hourly bars. Same-day prints never enter
features for bars on day ``D``.

This is an as-of / publication-lag rule, **not** ALFRED vintage data.

Sources (graceful degrade, first success wins per series):
1. Local cache under ``feature_extras.fred.cache_dir`` (default ``data/fred_cache``)
2. ``fredapi`` when env ``FRED_API_KEY`` (or ``api_key_env``) is set
3. Public FRED CSV graph endpoint (no API key)

If nothing can be loaded, the pack adds **no columns** (train/backtest still run).
Do not put the pair's own FRED FX series (e.g. DEXUSEU on EURUSD) in the list.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.paths import resolve_under_root

DEFAULT_SERIES = ("DFF", "DGS10", "T10Y2Y", "DTWEXBGS", "VIXCLS")
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
USER_AGENT = "ForX-RnD-research-lab/0.3 (research; +https://github.com/ashique19/ForX-RnD)"
DEFAULT_TIMEOUT_S = 12.0
DEFAULT_TTL_S = 86400
DEFAULT_LAG_DAYS = 1
DEFAULT_Z_WINDOW = 60

# Daily FX prints of the *same* pair would be a lagged copy of the target.
OWN_FX_SERIES = {
    "EURUSD": "DEXUSEU",
    "GBPUSD": "DEXUSUK",
    "USDJPY": "DEXJPUS",
    "AUDUSD": "DEXUSAL",
    "USDCAD": "DEXCAUS",
}


@dataclass
class FredStatus:
    """UI / CLI snapshot. Never raises; missing data is a status, not an exception."""

    enabled: bool
    source: str = "disabled"  # disabled | cache | fredapi | csv | missing | mixed
    error: str | None = None
    series: list[str] = field(default_factory=list)
    fetched_at: str | None = None
    used_api_key: bool = False
    notes: list[str] = field(default_factory=list)


def fred_cfg(extra: dict[str, Any]) -> dict[str, Any]:
    raw = extra.get("fred")
    if raw is True:
        return {"enabled": True}
    if not raw:
        return {"enabled": False}
    if isinstance(raw, dict):
        return dict(raw)
    return {"enabled": False}


def _api_key(cfg: dict[str, Any]) -> str | None:
    env_name = str(cfg.get("api_key_env") or "FRED_API_KEY")
    val = os.environ.get(env_name) or os.environ.get("FRED_API_KEY")
    if val is None:
        return None
    text = str(val).strip()
    return text or None


def _cache_dir(cfg: dict[str, Any], lab_cfg: dict[str, Any] | None = None) -> Path:
    raw = cfg.get("cache_dir") or "data/fred_cache"
    path = Path(str(raw))
    if path.is_absolute():
        return path
    if lab_cfg is not None:
        data_dir = (lab_cfg.get("paths") or {}).get("data_dir")
        if data_dir and str(raw).startswith("data/"):
            return resolve_under_root(raw)
    return resolve_under_root(raw)


def _series_ids(cfg: dict[str, Any], pair: str | None = None) -> list[str]:
    raw = cfg.get("series") or list(DEFAULT_SERIES)
    ids = []
    skip = OWN_FX_SERIES.get(str(pair or "").upper().replace("/", ""), "")
    for item in raw:
        sid = str(item).strip().upper()
        if not sid:
            continue
        if skip and sid == skip:
            continue
        if sid not in ids:
            ids.append(sid)
    return ids


def _naive_index(idx) -> pd.DatetimeIndex:
    out = pd.DatetimeIndex(pd.to_datetime(idx))
    tz = getattr(out, "tz", None)
    if tz is not None:
        out = out.tz_convert("UTC").tz_localize(None)
    return out


def _parse_fred_csv(text: str, sid: str) -> pd.Series:
    df = pd.read_csv(StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty:
        raise ValueError(f"{sid}: empty FRED CSV")
    date_col = "observation_date" if "observation_date" in df.columns else df.columns[0]
    val_cols = [c for c in df.columns if c != date_col]
    if not val_cols:
        raise ValueError(f"{sid}: no value column")
    val_col = sid if sid in val_cols else val_cols[0]
    values = pd.to_numeric(df[val_col].replace(".", np.nan), errors="coerce")
    idx = pd.to_datetime(df[date_col], errors="coerce")
    series = pd.Series(values.to_numpy(), index=idx, name=sid)
    series = series[~series.index.isna()].sort_index()
    series = series[~series.index.duplicated(keep="last")]
    series.index = _naive_index(series.index).normalize()
    return series.dropna()


def _read_cache_file(path: Path, sid: str) -> pd.Series | None:
    if not path.exists() or path.stat().st_size <= 0:
        return None
    try:
        return _parse_fred_csv(path.read_text(encoding="utf-8"), sid)
    except Exception:
        return None


def _write_cache_file(path: Path, series: pd.Series, sid: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({sid: series})
    frame.index.name = "observation_date"
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp)
    tmp.replace(path)


def _download_csv(sid: str, timeout: float) -> pd.Series:
    url = CSV_URL.format(sid=sid)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", errors="replace")
    if text.lstrip().startswith("PK"):
        raise ValueError(f"{sid}: FRED returned a zip, not CSV")
    if "<html" in text[:200].lower():
        raise ValueError(f"{sid}: FRED returned HTML")
    return _parse_fred_csv(text, sid)


def _download_fredapi(sid: str, api_key: str) -> pd.Series:
    from fredapi import Fred  # type: ignore

    fred = Fred(api_key=api_key)
    raw = fred.get_series(sid)
    series = pd.Series(raw, name=sid).astype(float)
    series.index = _naive_index(series.index).normalize()
    series = series[~series.index.duplicated(keep="last")].sort_index().dropna()
    if series.empty:
        raise ValueError(f"{sid}: empty fredapi series")
    return series


def load_fred_series(
    sid: str,
    cfg: dict[str, Any],
    lab_cfg: dict[str, Any] | None = None,
    *,
    allow_network: bool = True,
) -> tuple[pd.Series | None, str, str | None]:
    """Return (series, source, error). source is cache | fredapi | csv | missing."""
    sid = str(sid).strip().upper()
    cache_path = _cache_dir(cfg, lab_cfg) / f"{sid}.csv"
    ttl = float(cfg.get("cache_ttl_s", DEFAULT_TTL_S) or DEFAULT_TTL_S)
    cached = _read_cache_file(cache_path, sid)
    fresh = False
    if cached is not None and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        fresh = age <= ttl if ttl > 0 else True
        if fresh:
            return cached, "cache", None

    if not allow_network:
        if cached is not None:
            return cached, "cache", None
        return None, "missing", f"{sid}: no local cache"

    timeout = float(cfg.get("timeout_s", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S)
    key = _api_key(cfg)
    last_err: str | None = None
    if key:
        try:
            series = _download_fredapi(sid, key)
            _write_cache_file(cache_path, series, sid)
            return series, "fredapi", None
        except Exception as exc:  # noqa: BLE001 — degrade to CSV
            last_err = f"fredapi {sid}: {exc}"
    try:
        series = _download_csv(sid, timeout)
        _write_cache_file(cache_path, series, sid)
        return series, "csv", None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, UnicodeError) as exc:
        last_err = f"{last_err + '; ' if last_err else ''}csv {sid}: {exc}"
    if cached is not None:
        return cached, "cache", last_err
    return None, "missing", last_err or f"{sid}: unavailable"


def load_fred_frame(
    cfg: dict[str, Any],
    lab_cfg: dict[str, Any] | None = None,
    *,
    pair: str | None = None,
    allow_network: bool = True,
) -> tuple[pd.DataFrame, FredStatus]:
    ids = _series_ids(cfg, pair=pair)
    status = FredStatus(enabled=True, source="missing")
    if _api_key(cfg):
        status.used_api_key = True
    cols: dict[str, pd.Series] = {}
    sources: list[str] = []
    errors: list[str] = []
    for sid in ids:
        series, source, err = load_fred_series(
            sid, cfg, lab_cfg, allow_network=allow_network
        )
        if series is not None and not series.empty:
            cols[sid] = series
            sources.append(source)
            status.series.append(sid)
        if err:
            errors.append(err)
    if errors:
        status.error = "; ".join(errors[:4])
        status.notes.extend(errors)
    if not cols:
        status.source = "missing"
        if not status.error:
            key_env = str(cfg.get("api_key_env") or "FRED_API_KEY")
            status.error = (
                f"No FRED series loaded. Set {key_env} or allow CSV download "
                "to fred.stlouisfed.org; otherwise the pack stays off."
            )
        return pd.DataFrame(), status
    uniq = set(sources)
    status.source = uniq.pop() if len(uniq) == 1 else "mixed"
    cache_dir = _cache_dir(cfg, lab_cfg)
    mtimes = [
        (cache_dir / f"{sid}.csv").stat().st_mtime
        for sid in status.series
        if (cache_dir / f"{sid}.csv").exists()
    ]
    if mtimes:
        status.fetched_at = datetime.fromtimestamp(max(mtimes), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    frame = pd.concat(cols, axis=1).sort_index()
    frame.index = _naive_index(frame.index).normalize()
    return frame, status


def align_fred_asof(
    daily: pd.DataFrame,
    bar_index: pd.Index,
    *,
    lag_days: int = DEFAULT_LAG_DAYS,
) -> pd.DataFrame:
    """Shift observation dates by ``lag_days``, then ffill onto ``bar_index``.

    Observation dated ``D`` is first visible on bars with timestamp >= ``D + lag_days``.
    """
    if daily.empty:
        return pd.DataFrame(index=bar_index)
    lag = int(lag_days)
    if lag < 0:
        lag = 0
    src = daily.copy()
    src.index = _naive_index(src.index).normalize()
    src = src[~src.index.duplicated(keep="last")].sort_index()
    src.index = src.index + pd.Timedelta(days=lag)
    target = _naive_index(bar_index)
    aligned = src.reindex(target).ffill()
    aligned.index = bar_index
    return aligned


def _rolling_z(s: pd.Series, window: int) -> pd.Series:
    mu = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std()
    return (s - mu) / sd.replace(0, np.nan)


def daily_fred_transforms(daily: pd.DataFrame, *, z_window: int) -> pd.DataFrame:
    """Causal transforms on the *daily* as-of series (before hourly ffill)."""
    parts: list[pd.DataFrame] = []
    z_window = int(z_window)
    for col in daily.columns:
        s = pd.to_numeric(daily[col], errors="coerce")
        block = pd.DataFrame(
            {
                f"fred_{col}": s,
                f"fred_{col}_chg1": s.diff(1),
                f"fred_{col}_chg5": s.diff(5),
            },
            index=daily.index,
        )
        if z_window > 1:
            block[f"fred_{col}_z"] = _rolling_z(s, z_window)
        parts.append(block)
    if not parts:
        return pd.DataFrame(index=daily.index)
    out = parts[0]
    for extra in parts[1:]:
        out = out.join(extra)
    return out


def fred_feed_status(lab_cfg: dict[str, Any] | None) -> FredStatus | None:
    """Cache/config snapshot for the health strip. No network."""
    extra = dict((lab_cfg or {}).get("feature_extras") or {})
    cfg = fred_cfg(extra)
    if not bool(cfg.get("enabled", False)):
        return None
    ids = _series_ids(cfg, pair=None)
    cache_dir = _cache_dir(cfg, lab_cfg or {})
    found: list[str] = []
    mtimes: list[float] = []
    for sid in ids:
        path = cache_dir / f"{sid}.csv"
        if path.exists() and path.stat().st_size > 0:
            found.append(sid)
            mtimes.append(path.stat().st_mtime)
    status = FredStatus(enabled=True, series=found, used_api_key=bool(_api_key(cfg)))
    if found:
        status.source = "cache"
        status.fetched_at = datetime.fromtimestamp(max(mtimes), tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        missing = [s for s in ids if s not in found]
        if missing:
            status.notes.append("cache missing: " + ", ".join(missing))
        return status
    status.source = "missing"
    key_env = str(cfg.get("api_key_env") or "FRED_API_KEY")
    if not _api_key(cfg):
        status.error = (
            f"No FRED cache yet; {key_env} unset. CSV download runs on train/backtest."
        )
    else:
        status.error = "No FRED cache yet; will try fredapi on train/backtest."
    return status


def add_fred_features(
    out: pd.DataFrame,
    df: pd.DataFrame,
    extra: dict[str, Any],
    lab_cfg: dict[str, Any] | None = None,
    pair: str | None = None,
) -> FredStatus | None:
    """Append as-of FRED columns. No-op (no columns) when disabled or empty."""
    cfg = fred_cfg(extra)
    if not bool(cfg.get("enabled", False)):
        return None
    allow_network = bool(cfg.get("allow_network", True))
    daily, status = load_fred_frame(
        cfg, lab_cfg, pair=pair, allow_network=allow_network
    )
    if daily.empty:
        return status
    lag = int(cfg.get("lag_days", DEFAULT_LAG_DAYS) or 0)
    z_window = int(cfg.get("z_window", DEFAULT_Z_WINDOW) or DEFAULT_Z_WINDOW)
    transformed = daily_fred_transforms(daily, z_window=z_window)
    aligned = align_fred_asof(transformed, df.index, lag_days=lag)
    for col in aligned.columns:
        col_s = aligned[col]
        if col_s.notna().sum() <= 0:
            continue
        out[col] = col_s.to_numpy()
    return status
