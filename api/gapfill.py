"""Background idle gap-filler for Decision desk history + tip freshness.

Runs as a low-priority daemon while the API is up. One pair / one small chunk
at a time — never races a manual historic pull or Replay job.

FX pairs use Dukascopy for ``data/history``. BTCUSD uses yfinance only
(Dukascopy does not apply). Live tip gaps use the existing densify/refresh
helpers lightly and never write synthetic prices.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

log = logging.getLogger("forx.gapfill")

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_STATE: dict[str, Any] = {
    "enabled": True,
    "last_tick": None,
    "last_pair": None,
    "last_message": "idle",
    "fills": 0,
    "skips": 0,
}

# Idle cadence — keep Decision UI / live refresh responsive.
SCAN_SECONDS = 90.0
CHUNK_HOURS = 72  # FX Dukascopy hours per tick
CHUNK_PAUSE = 0.4
HISTORY_START = datetime(2015, 1, 1)
CRYPTO_PAIRS = frozenset({"BTCUSD"})


def status() -> dict[str, Any]:
    return dict(_STATE)


def is_manual_pull_busy() -> bool:
    """True when a historic pull / Replay worker owns the serial lock."""
    try:
        from api.replayjob import _LOCK, _running_locked

        with _LOCK:
            current = _running_locked()
            return current is not None
    except Exception:  # noqa: BLE001
        return False


def start_gapfill(*, daemon: bool = True) -> None:
    """Start once from API lifespan. Idempotent."""
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="forx-gapfill", daemon=daemon)
    _THREAD.start()
    _STATE["last_message"] = "started"
    log.info("gapfill background started")


def stop_gapfill() -> None:
    _STOP.set()
    _STATE["last_message"] = "stopped"


def _loop() -> None:
    # Stagger first tick so Decision board finishes boot.
    _STOP.wait(15.0)
    while not _STOP.is_set():
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            _STATE["last_message"] = f"tick error: {exc}"
            log.exception("gapfill tick failed")
        _STOP.wait(SCAN_SECONDS)


def _tick() -> None:
    _STATE["last_tick"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if not _STATE.get("enabled", True):
        _STATE["last_message"] = "disabled"
        _STATE["skips"] = int(_STATE.get("skips") or 0) + 1
        return
    if is_manual_pull_busy():
        _STATE["last_message"] = "skip: manual pull/replay running"
        _STATE["skips"] = int(_STATE.get("skips") or 0) + 1
        return

    from forex_lab.config_loader import load_config
    from forex_lab.ui.watchlist import load_watchlist

    cfg = load_config()
    wl = load_watchlist(None, cfg, create=False)
    pairs = list(wl.pair_symbols()) or ["EURUSD"]
    # Prefer Active first so tip/history stay warm, then the rest.
    active = (wl.active or "EURUSD").upper()
    ordered = [active] + [p for p in pairs if p != active]

    for pair in ordered:
        if _STOP.is_set() or is_manual_pull_busy():
            _STATE["last_message"] = "skip: busy mid-scan"
            return
        did = _fill_one_pair_chunk(pair, cfg)
        if did:
            _STATE["fills"] = int(_STATE.get("fills") or 0) + 1
            _STATE["last_pair"] = pair
            # One pair per scan — keep load tiny.
            return
    _STATE["last_message"] = "idle: no gaps this scan"


def _fill_one_pair_chunk(pair: str, cfg: dict[str, Any]) -> bool:
    pair_u = str(pair).upper()
    if pair_u in CRYPTO_PAIRS:
        return _fill_crypto_chunk(pair_u, cfg)
    return _fill_fx_chunk(pair_u, cfg)


def _fill_fx_chunk(pair: str, cfg: dict[str, Any]) -> bool:
    from forex_lab.history import (
        SOURCE_DUKA,
        _download_bi5,
        _iter_hours,
        _merge,
        _now_closed_hour,
        _save_bars,
        dukascopy_point,
        fx_hour_open,
        history_path,
        load_history_csv,
        parse_bi5,
        resample_bars,
        ticks_to_bars,
        HistoryError,
    )

    path = history_path(pair, "1h", cfg)
    hist = load_history_csv(path)
    end = _now_closed_hour()
    start = HISTORY_START
    hours = [h for h in _iter_hours(start, end) if fx_hour_open(h)]
    have = set(pd.DatetimeIndex(hist.index).floor("h")) if not hist.empty else set()
    todo = [h for h in hours if pd.Timestamp(h) not in have]
    if not todo:
        # Maybe 1d is stale vs 1h — resample if needed.
        return _ensure_daily_from_h1(pair, hist, SOURCE_DUKA, cfg)

    chunk = todo[:CHUNK_HOURS]
    _STATE["last_message"] = f"{pair} FX gapfill {len(chunk)}/{len(todo)} hours"
    rows: list[pd.DataFrame] = []
    for hour in chunk:
        if is_manual_pull_busy() or _STOP.is_set():
            break
        try:
            blob = _download_bi5(pair, hour)
            if not blob:
                continue
            bars = ticks_to_bars(parse_bi5(blob, hour, dukascopy_point(pair)), "1h")
            if bars is not None and not bars.empty:
                rows.append(bars)
        except HistoryError:
            time.sleep(0.8)
            continue
        except Exception:  # noqa: BLE001
            continue
        time.sleep(CHUNK_PAUSE)

    if not rows:
        _STATE["last_message"] = f"{pair}: chunk empty (404/unreachable)"
        return False

    fresh = pd.concat(rows)
    hist = _merge(hist, fresh)
    _save_bars(pair, "1h", hist, SOURCE_DUKA, cfg)
    daily = resample_bars(hist, "1d")
    _save_bars(pair, "1d", daily, SOURCE_DUKA, cfg)
    _STATE["last_message"] = f"{pair}: filled {len(fresh)} bars; hist={len(hist)}"
    # Light tip refresh for Active-like warmth without starving UI.
    _maybe_refresh_tip(pair, cfg)
    return True


def _fill_crypto_chunk(pair: str, cfg: dict[str, Any]) -> bool:
    """yfinance-only path for BTCUSD. Dukascopy does not apply."""
    from forex_lab.config_loader import pair_to_ticker
    from forex_lab.data import _normalize_ohlcv
    from forex_lab.history import _merge, _save_bars, history_path, load_history_csv, resample_bars

    path = history_path(pair, "1h", cfg)
    hist = load_history_csv(path)
    ticker = pair_to_ticker(pair, cfg)
    # If we already have recent coverage, only extend the tip.
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    if not hist.empty and hist.index.max() >= now - timedelta(hours=6):
        return _ensure_daily_from_h1(pair, hist, "yfinance", cfg)

    import yfinance as yf

    # Small recent window — deep backfill is the dedicated script.
    start = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    _STATE["last_message"] = f"{pair} yfinance gapfill {start}->{end}"
    try:
        raw = yf.download(
            ticker,
            start=start,
            end=end,
            interval="1h",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as exc:  # noqa: BLE001
        _STATE["last_message"] = f"{pair} yfinance err: {exc}"
        return False
    if raw is None or raw.empty:
        _STATE["last_message"] = f"{pair} yfinance empty"
        return False
    chunk = _normalize_ohlcv(raw)
    if getattr(chunk.index, "tz", None) is not None:
        chunk.index = chunk.index.tz_convert("UTC").tz_localize(None)
    before = len(hist)
    hist = _merge(hist, chunk)
    if len(hist) <= before and not hist.empty and hist.index.max() == (
        load_history_csv(path).index.max() if path.exists() else hist.index.max()
    ):
        # Still refresh daily.
        return _ensure_daily_from_h1(pair, hist, "yfinance", cfg)
    _save_bars(pair, "1h", hist, "yfinance", cfg)
    daily = resample_bars(hist, "1d")
    # Prefer merging onto any deeper 1d store.
    dpath = history_path(pair, "1d", cfg)
    daily = _merge(load_history_csv(dpath), daily)
    _save_bars(pair, "1d", daily, "yfinance", cfg)
    _STATE["last_message"] = f"{pair}: yfinance +{len(hist) - before} bars; hist={len(hist)}"
    _maybe_refresh_tip(pair, cfg)
    return True


def _ensure_daily_from_h1(pair: str, hist: pd.DataFrame, source: str, cfg: dict[str, Any]) -> bool:
    from forex_lab.history import _merge, _save_bars, history_path, load_history_csv, resample_bars

    if hist is None or hist.empty:
        return False
    daily = resample_bars(hist, "1d")
    dpath = history_path(pair, "1d", cfg)
    existing = load_history_csv(dpath)
    if not existing.empty and not daily.empty:
        if existing.index.max() >= daily.index.max() and len(existing) >= len(daily) * 0.95:
            return False
    merged = _merge(existing, daily)
    _save_bars(pair, "1d", merged, source, cfg)
    _STATE["last_message"] = f"{pair}: refreshed 1d from 1h ({len(merged)} bars)"
    return True


def _maybe_refresh_tip(pair: str, cfg: dict[str, Any]) -> None:
    """Best-effort live tip densify; never synthetic; swallow errors."""
    try:
        from forex_lab.data import try_live_refresh

        try_live_refresh(pair, cfg, interval="1h")
    except Exception:  # noqa: BLE001
        return
