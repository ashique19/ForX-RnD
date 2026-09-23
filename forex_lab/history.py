"""Backtest-grade FX history for walk-forward replay.

Primary source is the public Dukascopy tick feed (bi5). Ticks are aggregated to
OHLC **with bid and ask**, then discarded — raw ticks are not written to git.
HistData ASCII minute bars are the fallback when Dukascopy returns nothing.
Those bars have no bid/ask; replay then uses ``spread_pips`` / ``slippage_pips``.

Download cache (gitignored)::

    data/history/{PAIR}_1h.csv
    data/history/{PAIR}_1h.meta.json

One-command pull::

    python -m forex_lab history --pair EURUSD --interval 1h --start 2015-01-01
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from zipfile import ZipFile

import lzma
import struct

import numpy as np
import pandas as pd

from forex_lab.paths import resolve_under_root

HISTORY_INTERVALS = ("15m", "1h", "4h", "1d")
GRAIN_15M = "15m"
GRAIN_1H = "1h"
SOURCE_DUKA = "dukascopy"
SOURCE_HIST = "histdata"

_DUKA_URL = "https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02d}/{day:02d}/{hour:02d}h_ticks.bi5"
_HIST_PAGE = (
    "https://www.histdata.com/download-free-forex-historical-data/"
    "?/ascii/1-minute-bar-quotes/{pair}/{year}/{month}"
)
_HIST_POST = "https://www.histdata.com/get.php"
_UA = "Mozilla/5.0 (compatible; ForX-RnD/replay; research)"

_TICK_DTYPE = np.dtype(
    [
        ("ms", ">u4"),
        ("ask", ">u4"),
        ("bid", ">u4"),
        ("av", ">f4"),
        ("bv", ">f4"),
    ]
)

_OHLC_AGG = {
    "Open": "first",
    "High": "max",
    "Low": "min",
    "Close": "last",
    "Volume": "sum",
    "BidOpen": "first",
    "BidHigh": "max",
    "BidLow": "min",
    "BidClose": "last",
    "AskOpen": "first",
    "AskHigh": "max",
    "AskLow": "min",
    "AskClose": "last",
    "Spread": "mean",
}


class HistoryError(RuntimeError):
    """Download or cache error. Never replaced with synthetic prices."""


def replay_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("replay") or {})
    raw.setdefault("start", "2015-01-01")
    raw.setdefault("slippage_pips", 0.0)
    raw.setdefault("use_bid_ask", True)
    raw.setdefault("history_dir", "data/history")
    raw.setdefault("store_dir", "data/replay")
    raw.setdefault("workers", 8)
    raw.setdefault("source", "auto")
    return raw


def history_dir(cfg: dict[str, Any] | None = None) -> Path:
    env = os.environ.get("FORX_HISTORY_DIR")
    if env:
        path = Path(env)
    else:
        path = resolve_under_root(replay_cfg(cfg).get("history_dir") or "data/history")
    path.mkdir(parents=True, exist_ok=True)
    return path


def replay_store_dir(cfg: dict[str, Any] | None = None) -> Path:
    env = os.environ.get("FORX_REPLAY_DIR")
    if env:
        path = Path(env)
    else:
        path = resolve_under_root(replay_cfg(cfg).get("store_dir") or "data/replay")
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_interval(interval: str | None) -> str:
    key = str(interval or "1h").strip()
    aliases = {"h1": "1h", "60m": "1h", "m15": "15m", "h4": "4h", "d1": "1d", "1D": "1d"}
    key = aliases.get(key, key)
    if key not in HISTORY_INTERVALS:
        raise HistoryError(f"interval must be one of {', '.join(HISTORY_INTERVALS)} (got {interval!r})")
    return key


def grain_for(interval: str) -> str:
    """Finest bar we download. 4h and 1d are resampled from 1h."""
    interval = normalize_interval(interval)
    return GRAIN_15M if interval == GRAIN_15M else GRAIN_1H


def dukascopy_point(pair: str) -> int:
    """Integer divisor for Dukascopy tick prices (price = raw / point)."""
    p = str(pair).upper().replace("/", "")
    if p.startswith("XAU") or p.startswith("XAG"):
        return 1000
    if "JPY" in p:
        return 1000
    return 100_000


def dukascopy_url(symbol: str, ts: datetime) -> str:
    """Public bi5 URL. Month is zero-based (January = 00)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return _DUKA_URL.format(
        symbol=str(symbol).upper(),
        year=ts.year,
        month=ts.month - 1,
        day=ts.day,
        hour=ts.hour,
    )


def fx_hour_open(ts: datetime) -> bool:
    """Rough FX week: Sunday 21:00 UTC through Friday 21:59 UTC. Saturday is closed."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    wd = ts.weekday()
    if wd == 5:
        return False
    if wd == 6 and ts.hour < 21:
        return False
    if wd == 4 and ts.hour >= 22:
        return False
    return True


def history_path(pair: str, interval: str, cfg: dict[str, Any] | None = None) -> Path:
    iv = normalize_interval(interval)
    return history_dir(cfg) / f"{str(pair).upper()}_{iv}.csv"


def history_meta_path(pair: str, interval: str, cfg: dict[str, Any] | None = None) -> Path:
    path = history_path(pair, interval, cfg)
    return path.with_suffix(".meta.json")


def parse_bi5(blob: bytes, hour: datetime, point: int) -> pd.DataFrame:
    """Decompress one Dukascopy hour and return tick bid/ask. Empty if the hour is blank."""
    if not blob:
        return _empty_ticks()
    try:
        raw = lzma.decompress(blob)
    except lzma.LZMAError:
        return _empty_ticks()
    width = _TICK_DTYPE.itemsize
    n = len(raw) // width
    if n <= 0:
        return _empty_ticks()
    recs = np.frombuffer(raw[: n * width], dtype=_TICK_DTYPE, count=n)
    if hour.tzinfo is None:
        hour = hour.replace(tzinfo=timezone.utc)
    else:
        hour = hour.astimezone(timezone.utc)
    origin = pd.Timestamp(hour).tz_convert("UTC").tz_localize(None)
    idx = origin + pd.to_timedelta(recs["ms"].astype(np.int64), unit="ms")
    point_f = float(point)
    out = pd.DataFrame(
        {
            "bid": recs["bid"].astype(np.float64) / point_f,
            "ask": recs["ask"].astype(np.float64) / point_f,
        },
        index=pd.DatetimeIndex(idx, name="Datetime"),
    )
    out = out[(out["bid"] > 0) & (out["ask"] > 0)]
    return out.sort_index()


def ticks_to_bars(ticks: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate ticks to mid OHLC plus bid/ask OHLC and mean spread."""
    if ticks is None or ticks.empty:
        return _empty_bars()
    mid = (ticks["bid"] + ticks["ask"]) / 2.0
    frame = pd.DataFrame(
        {
            "Open": mid,
            "High": mid,
            "Low": mid,
            "Close": mid,
            "Volume": 1.0,
            "BidOpen": ticks["bid"],
            "BidHigh": ticks["bid"],
            "BidLow": ticks["bid"],
            "BidClose": ticks["bid"],
            "AskOpen": ticks["ask"],
            "AskHigh": ticks["ask"],
            "AskLow": ticks["ask"],
            "AskClose": ticks["ask"],
            "Spread": ticks["ask"] - ticks["bid"],
        },
        index=ticks.index,
    )
    grouped = frame.groupby(pd.Grouper(freq=rule, label="left", closed="left")).agg(_OHLC_AGG)
    grouped = grouped.dropna(subset=["Open", "Close"])
    grouped = grouped[grouped["Volume"] > 0]
    grouped.index.name = "Datetime"
    return grouped


def resample_bars(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Resample a finer cache (1h or 15m) up to 4h or 1d. No prices are invented."""
    interval = normalize_interval(interval)
    rule = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1D"}[interval]
    if df is None or df.empty:
        return _empty_bars()
    cols = {k: v for k, v in _OHLC_AGG.items() if k in df.columns}
    out = df.groupby(pd.Grouper(freq=rule, label="left", closed="left")).agg(cols)
    out = out.dropna(subset=["Open", "Close"])
    out.index.name = "Datetime"
    return out


def parse_histdata_text(text: str) -> pd.DataFrame:
    """HistData ASCII minute bars: ``YYYYMMDD HHMMSS;open;high;low;close;volume``."""
    rows: list[tuple[pd.Timestamp, float, float, float, float, float]] = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("<") or raw.lower().startswith("date"):
            continue
        if ";" in raw:
            parts = [p.strip() for p in raw.split(";")]
            stamp = parts[0]
            nums = parts[1:]
        elif "," in raw:
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) >= 6 and " " not in parts[0]:
                stamp = f"{parts[0]} {parts[1]}"
                nums = parts[2:]
            else:
                stamp = parts[0]
                nums = parts[1:]
        else:
            continue
        if len(nums) < 4:
            continue
        ts = pd.to_datetime(stamp, format="%Y%m%d %H%M%S", errors="coerce")
        if pd.isna(ts):
            ts = pd.to_datetime(stamp, errors="coerce")
        if pd.isna(ts):
            continue
        try:
            o, h, low, c = (float(nums[0]), float(nums[1]), float(nums[2]), float(nums[3]))
            vol = float(nums[4]) if len(nums) > 4 and nums[4] else 0.0
        except ValueError:
            continue
        if min(o, h, low, c) <= 0:
            continue
        rows.append((pd.Timestamp(ts), o, h, low, c, vol))
    if not rows:
        return _empty_bars()
    idx = pd.DatetimeIndex([r[0] for r in rows], name="Datetime")
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [r[5] for r in rows],
        },
        index=idx,
    ).sort_index()


def load_history_csv(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size <= 0:
        return _empty_bars()
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(None)
    df.index.name = "Datetime"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def load_meta(pair: str, interval: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    path = history_meta_path(pair, interval, cfg)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(pair: str, interval: str, df: pd.DataFrame, source: str, cfg: dict[str, Any] | None) -> None:
    bid_ask = "BidClose" in df.columns and "AskClose" in df.columns and df["BidClose"].notna().any()
    payload = {
        "pair": str(pair).upper(),
        "interval": normalize_interval(interval),
        "source": source,
        "rows": int(len(df)),
        "start": None if df.empty else df.index.min().strftime("%Y-%m-%d %H:%M:%S"),
        "end": None if df.empty else df.index.max().strftime("%Y-%m-%d %H:%M:%S"),
        "bid_ask": bool(bid_ask),
        "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    history_meta_path(pair, interval, cfg).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _save_bars(pair: str, interval: str, df: pd.DataFrame, source: str, cfg: dict[str, Any] | None) -> Path:
    path = history_path(pair, interval, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.to_csv(path)
    _write_meta(pair, interval, out, source, cfg)
    return path


def _parse_bound(value: object, *, default: datetime | None = None) -> datetime:
    if value is None or str(value).strip() == "":
        if default is None:
            raise HistoryError("missing date")
        return default
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        raise HistoryError(f"could not parse date {value!r}")
    py = ts.to_pydatetime()
    if py.tzinfo is not None:
        py = py.astimezone(timezone.utc).replace(tzinfo=None)
    return py.replace(minute=0, second=0, microsecond=0)


def _now_closed_hour() -> datetime:
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    return now - timedelta(hours=1)


def covers_range(df: pd.DataFrame, start: datetime, end: datetime) -> bool:
    """True when the cache spans the request and is not full of holes.

    A three-day grace covers the weekend. Open hours that never landed (a
    dropped download) keep the cache stale so the next pull fills them.
    """
    if df is None or df.empty:
        return False
    grace = timedelta(days=3)
    if df.index.min().to_pydatetime() > start + grace:
        return False
    if df.index.max().to_pydatetime() < end - grace:
        return False
    expected = [h for h in _iter_hours(start, end) if fx_hour_open(h)]
    if len(expected) < 8:
        return True
    have = set(pd.DatetimeIndex(df.index).floor("h"))
    got = sum(1 for hour in expected if pd.Timestamp(hour) in have)
    return got / len(expected) >= 0.85


def history_status(pair: str, interval: str, cfg: dict[str, Any] | None, start: object, end: object) -> dict[str, Any]:
    iv = normalize_interval(interval)
    grain = grain_for(iv)
    start_dt = _parse_bound(start, default=datetime(2015, 1, 1))
    end_dt = _parse_bound(end, default=_now_closed_hour())
    grain_df = load_history_csv(history_path(pair, grain, cfg))
    meta = load_meta(pair, grain, cfg)
    stale = not covers_range(grain_df, start_dt, end_dt)
    return {
        "pair": str(pair).upper(),
        "interval": iv,
        "grain": grain,
        "rows": int(len(grain_df)),
        "source": meta.get("source"),
        "bid_ask": bool(meta.get("bid_ask")),
        "start": None if grain_df.empty else str(grain_df.index.min()),
        "end": None if grain_df.empty else str(grain_df.index.max()),
        "stale": stale,
        "requested_start": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "requested_end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
    }


ProgressFn = Callable[[dict[str, Any]], None]


def pull_history(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    interval: str = "1h",
    start: object = None,
    end: object = None,
    source: str | None = None,
    progress: ProgressFn | None = None,
    fetch_hour: Callable[[datetime], bytes | None] | None = None,
    fetch_histdata_month: Callable[[str, int, int], bytes | None] | None = None,
) -> dict[str, Any]:
    """Download and cache OHLC for ``pair``. Does not synthesize prices.

    ``fetch_hour`` / ``fetch_histdata_month`` are test seams. Production uses
    Dukascopy, then HistData when ``source`` is ``auto`` and Dukascopy is empty.
    """
    cfg = cfg or {}
    pair_u = str(pair).upper().replace("/", "")
    iv = normalize_interval(interval)
    grain = grain_for(iv)
    rc = replay_cfg(cfg)
    start_dt = _parse_bound(start if start is not None else rc.get("start"), default=datetime(2015, 1, 1))
    end_dt = _parse_bound(end, default=_now_closed_hour())
    if end_dt < start_dt:
        raise HistoryError("end is before start")
    which = str(source or rc.get("source") or "auto").strip().lower()
    if which not in {"auto", SOURCE_DUKA, SOURCE_HIST}:
        raise HistoryError(f"unknown history source {which!r}")

    existing = load_history_csv(history_path(pair_u, grain, cfg))
    if covers_range(existing, start_dt, end_dt) and which != SOURCE_HIST:
        frame = _finalize(existing, iv, pair_u, str(load_meta(pair_u, grain, cfg).get("source") or SOURCE_DUKA), cfg)
        _emit(progress, phase="pull", fraction=1.0, message=f"{pair_u} {iv} cache already covers the range", rows=len(frame))
        return _result(pair_u, iv, frame, str(load_meta(pair_u, grain, cfg).get("source") or SOURCE_DUKA), cached=True, cfg=cfg)

    used = SOURCE_DUKA
    grain_df = existing
    if which in {"auto", SOURCE_DUKA}:
        try:
            grain_df = _pull_dukascopy(
                pair_u,
                grain,
                start_dt,
                end_dt,
                existing,
                cfg,
                workers=int(rc.get("workers") or 8),
                progress=progress,
                fetch_hour=fetch_hour,
            )
        except HistoryError:
            if which == SOURCE_DUKA:
                raise
            grain_df = existing
        if grain_df is not None and not grain_df.empty and (covers_range(grain_df, start_dt, end_dt) or which == SOURCE_DUKA):
            used = SOURCE_DUKA
        elif which == SOURCE_DUKA:
            raise HistoryError(f"Dukascopy returned no {pair_u} bars for {start_dt.date()} -> {end_dt.date()}")
        elif grain_df is None or grain_df.empty or not covers_range(grain_df, start_dt, end_dt):
            _emit(progress, phase="pull", message=f"Dukascopy incomplete for {pair_u}; trying HistData", fraction=0.0)
            grain_df = _pull_histdata(
                pair_u,
                grain,
                start_dt,
                end_dt,
                cfg,
                progress=progress,
                fetch_month=fetch_histdata_month,
            )
            used = SOURCE_HIST
    else:
        grain_df = _pull_histdata(
            pair_u,
            grain,
            start_dt,
            end_dt,
            cfg,
            progress=progress,
            fetch_month=fetch_histdata_month,
        )
        used = SOURCE_HIST

    if grain_df is None or grain_df.empty:
        raise HistoryError(
            f"No historical bars for {pair_u} {iv}. Dukascopy and HistData both returned nothing. "
            "Prices were not invented."
        )
    _save_bars(pair_u, grain, grain_df, used, cfg)
    frame = _finalize(grain_df, iv, pair_u, used, cfg)
    _emit(progress, phase="pull", fraction=1.0, message=f"Saved {len(frame)} {iv} bars from {used}", rows=len(frame))
    return _result(pair_u, iv, frame, used, cached=False, cfg=cfg)


def _finalize(grain_df: pd.DataFrame, interval: str, pair: str, source: str, cfg: dict[str, Any] | None) -> pd.DataFrame:
    iv = normalize_interval(interval)
    grain = grain_for(iv)
    if iv == grain:
        return grain_df
    out = resample_bars(grain_df, iv)
    if out.empty:
        raise HistoryError(f"Resample of {pair} {grain} -> {iv} produced no bars")
    _save_bars(pair, iv, out, source, cfg)
    return out


def _result(
    pair: str,
    interval: str,
    df: pd.DataFrame,
    source: str,
    *,
    cached: bool,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bid_ask = "BidClose" in df.columns and bool(df["BidClose"].notna().any()) if len(df) else False
    return {
        "ok": True,
        "pair": pair,
        "interval": interval,
        "source": source,
        "cached": cached,
        "rows": int(len(df)),
        "bid_ask": bid_ask,
        "start": None if df.empty else df.index.min().strftime("%Y-%m-%d %H:%M:%S"),
        "end": None if df.empty else df.index.max().strftime("%Y-%m-%d %H:%M:%S"),
        "path": str(history_path(pair, interval, cfg)),
    }


def load_history(
    pair: str,
    cfg: dict[str, Any] | None,
    interval: str,
    *,
    start: object = None,
    end: object = None,
) -> pd.DataFrame:
    """Load the cached frame. Raises HistoryError when the file is missing."""
    iv = normalize_interval(interval)
    path = history_path(pair, iv, cfg)
    df = load_history_csv(path)
    if df.empty:
        grain = grain_for(iv)
        grain_df = load_history_csv(history_path(pair, grain, cfg))
        if grain_df.empty:
            raise HistoryError(f"No cached history for {str(pair).upper()} {iv}. Pull it first.")
        meta = load_meta(pair, grain, cfg)
        df = _finalize(grain_df, iv, str(pair).upper(), str(meta.get("source") or SOURCE_DUKA), cfg)
    if start is not None:
        df = df.loc[df.index >= _parse_bound(start)]
    if end is not None:
        df = df.loc[df.index <= _parse_bound(end, default=_now_closed_hour()) + timedelta(hours=1)]
    if df.empty:
        raise HistoryError(f"Cached {str(pair).upper()} {iv} has no bars in the requested range.")
    return df


def _pull_dukascopy(
    pair: str,
    grain: str,
    start: datetime,
    end: datetime,
    existing: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    workers: int,
    progress: ProgressFn | None,
    fetch_hour: Callable[[datetime], bytes | None] | None,
) -> pd.DataFrame:
    hours = [h for h in _iter_hours(start, end) if fx_hour_open(h)]
    have: set[pd.Timestamp] = set()
    if existing is not None and not existing.empty:
        have = set(pd.DatetimeIndex(existing.index).floor("h"))
    todo = [h for h in hours if pd.Timestamp(h) not in have]
    rule = "15min" if grain == GRAIN_15M else "1h"
    point = dukascopy_point(pair)
    total = max(1, len(todo))
    if not todo:
        return existing
    _emit(progress, phase="pull", fraction=0.0, message=f"Dukascopy {pair} {len(todo)} hours", rows=0 if existing is None else len(existing))

    rows: list[pd.DataFrame] = []
    errors = 0
    failed: list[datetime] = []
    done = 0
    lock = threading.Lock()
    last_emit = 0.0

    def _one(hour: datetime) -> None:
        nonlocal done, errors, last_emit
        try:
            blob = fetch_hour(hour) if fetch_hour is not None else _download_bi5(pair, hour)
            bars = ticks_to_bars(parse_bi5(blob, hour, point), rule) if blob else _empty_bars()
        except Exception:
            bars = _empty_bars()
            with lock:
                errors += 1
                failed.append(hour)
        with lock:
            done += 1
            if bars is not None and not bars.empty:
                rows.append(bars)
            now = time.monotonic()
            if progress is not None and (done == total or now - last_emit > 0.4):
                last_emit = now
                _emit(
                    progress,
                    phase="pull",
                    fraction=done / total,
                    message=f"Dukascopy {pair} {done}/{total} hours",
                    rows=(0 if existing is None else len(existing)) + sum(len(r) for r in rows),
                )

    worker_n = 1 if fetch_hour is not None else max(1, min(int(workers or 8), 16))
    if worker_n == 1:
        for hour in todo:
            _one(hour)
    else:
        with ThreadPoolExecutor(max_workers=worker_n) as pool:
            list(pool.map(_one, todo))

    if failed and fetch_hour is None:
        for hour in list(failed):
            try:
                blob = _download_bi5(pair, hour)
                bars = ticks_to_bars(parse_bi5(blob, hour, point), rule) if blob else _empty_bars()
            except Exception:
                continue
            if bars is not None and not bars.empty:
                rows.append(bars)

    fresh = pd.concat(rows) if rows else _empty_bars()
    if not fresh.empty:
        fresh = fresh[~fresh.index.duplicated(keep="last")].sort_index()
    merged = _merge(existing, fresh)
    if merged.empty and errors and fetch_hour is None:
        raise HistoryError(f"Dukascopy downloads failed for {pair} ({errors} hours errored, 0 bars)")
    if not merged.empty:
        _save_bars(pair, grain, merged, SOURCE_DUKA, cfg)
    return merged


_HTTP = threading.local()


def _http_client():
    import httpx

    client = getattr(_HTTP, "client", None)
    if client is None:
        client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept": "*/*"},
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=8),
        )
        _HTTP.client = client
    return client


def _download_bi5(pair: str, hour: datetime) -> bytes | None:
    url = dukascopy_url(pair, hour.replace(tzinfo=timezone.utc))
    last_status: object = None
    for attempt in range(3):
        try:
            res = _http_client().get(url)
            last_status = res.status_code
            if res.status_code == 404:
                return None
            if res.status_code == 200 and res.content:
                return res.content
        except Exception:
            last_status = "network"
        time.sleep(0.4 * (attempt + 1))
    if last_status == "network":
        raise HistoryError(f"Dukascopy unreachable for {url}")
    if last_status not in (None, 404, 200):
        raise HistoryError(f"Dukascopy HTTP {last_status} for {url}")
    return None


def _pull_histdata(
    pair: str,
    grain: str,
    start: datetime,
    end: datetime,
    cfg: dict[str, Any] | None,
    *,
    progress: ProgressFn | None,
    fetch_month: Callable[[str, int, int], bytes | None] | None,
) -> pd.DataFrame:
    months = _iter_months(start, end)
    if not months:
        raise HistoryError("HistData range is empty")
    parts: list[pd.DataFrame] = []
    for i, (year, month) in enumerate(months, start=1):
        _emit(
            progress,
            phase="pull",
            fraction=(i - 1) / len(months),
            message=f"HistData {pair} {year}-{month:02d}",
        )
        blob = (
            fetch_month(pair, year, month)
            if fetch_month is not None
            else _download_histdata_month(pair, year, month)
        )
        if not blob:
            continue
        text = _zip_or_text(blob)
        m1 = parse_histdata_text(text)
        if not m1.empty:
            parts.append(m1)
    if not parts:
        raise HistoryError(f"HistData returned no minute bars for {pair}")
    m1 = pd.concat(parts)
    m1 = m1[~m1.index.duplicated(keep="last")].sort_index()
    m1 = m1.loc[(m1.index >= pd.Timestamp(start)) & (m1.index <= pd.Timestamp(end) + pd.Timedelta(hours=1))]
    rule = "15min" if grain == GRAIN_15M else "1h"
    bars = resample_bars(m1, "15m" if rule == "15min" else "1h")
    if bars.empty:
        raise HistoryError(f"HistData resample produced no {grain} bars for {pair}")
    _save_bars(pair, grain, bars, SOURCE_HIST, cfg)
    return bars


def _download_histdata_month(pair: str, year: int, month: int) -> bytes | None:
    import httpx

    page = _HIST_PAGE.format(pair=pair.lower(), year=year, month=month)
    headers = {"User-Agent": _UA, "Referer": "https://www.histdata.com/"}
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
            html = client.get(page)
            if html.status_code != 200:
                return None
            match = re.search(r'id=["\']tk["\'][^>]*value=["\']([^"\']+)["\']', html.text)
            if match is None:
                match = re.search(r'name=["\']tk["\'][^>]*value=["\']([^"\']+)["\']', html.text)
            if match is None:
                return None
            token = match.group(1)
            posted = client.post(
                _HIST_POST,
                data={
                    "tk": token,
                    "date": str(year),
                    "datemonth": f"{year}{month}",
                    "platform": "ASCII",
                    "timeframe": "M1",
                    "fxpair": pair.upper(),
                },
                headers={"Referer": page},
            )
    except Exception as exc:
        raise HistoryError(f"HistData request failed ({exc})") from exc
    if posted.status_code != 200 or not posted.content:
        return None
    if posted.content[:1] == b"<":
        return None
    return posted.content


def _zip_or_text(blob: bytes) -> str:
    if blob[:2] == b"PK":
        with ZipFile(BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                return ""
            return zf.read(names[0]).decode("utf-8", errors="replace")
    return blob.decode("utf-8", errors="replace")


def _iter_hours(start: datetime, end: datetime) -> list[datetime]:
    cur = start.replace(minute=0, second=0, microsecond=0)
    stop = end.replace(minute=0, second=0, microsecond=0)
    out: list[datetime] = []
    while cur <= stop:
        out.append(cur)
        cur += timedelta(hours=1)
    return out


def _iter_months(start: datetime, end: datetime) -> list[tuple[int, int]]:
    y, m = start.year, start.month
    out: list[tuple[int, int]] = []
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        m += 1
        if m == 13:
            m = 1
            y += 1
    return out


def _merge(existing: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    parts = [p for p in (existing, fresh) if p is not None and not p.empty]
    if not parts:
        return _empty_bars()
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "Datetime"
    return out


def _emit(progress: ProgressFn | None, **payload: Any) -> None:
    if progress is not None:
        progress(payload)


def _empty_ticks() -> pd.DataFrame:
    return pd.DataFrame(columns=["bid", "ask"])


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
            "BidOpen",
            "BidHigh",
            "BidLow",
            "BidClose",
            "AskOpen",
            "AskHigh",
            "AskLow",
            "AskClose",
            "Spread",
        ]
    )


# struct is unused at runtime (numpy dtype parses bi5) but kept so the record
# width stays obvious next to the Dukascopy spec (20 bytes, big-endian).
_ = struct.calcsize(">IIIff")
