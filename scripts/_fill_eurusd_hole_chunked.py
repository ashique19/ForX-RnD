"""EURUSD H1 hole fill 2022-02..2024-09 — sequential retries, chunked saves."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forex_lab.history import (  # noqa: E402
    SOURCE_DUKA,
    HistoryError,
    _download_bi5,
    _iter_hours,
    _merge,
    _save_bars,
    dukascopy_point,
    fx_hour_open,
    history_path,
    load_history_csv,
    parse_bi5,
    resample_bars,
    ticks_to_bars,
)

HOLE_START = datetime(2022, 2, 1)
HOLE_END = datetime(2024, 9, 30, 23, 0, 0)
CKPT_EVERY = 50
MAX_ATTEMPTS = 5


def _todo(hist: pd.DataFrame, start: datetime, end: datetime) -> list[datetime]:
    hours = [h for h in _iter_hours(start, end) if fx_hour_open(h)]
    have = set(pd.DatetimeIndex(hist.index).floor("h")) if hist is not None and not hist.empty else set()
    return [h for h in hours if pd.Timestamp(h) not in have]


def _fetch_hour(pair: str, hour: datetime) -> pd.DataFrame | None:
    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            blob = _download_bi5(pair, hour)
            if not blob:
                return None
            bars = ticks_to_bars(parse_bi5(blob, hour, dukascopy_point(pair)), "1h")
            if bars is None or bars.empty:
                return None
            return bars
        except HistoryError as exc:
            last_err = exc
            time.sleep(min(8.0, 0.6 * attempt * attempt))
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(min(8.0, 0.6 * attempt * attempt))
    if last_err is not None:
        print(f"  FAIL {hour} after {MAX_ATTEMPTS}: {last_err}", flush=True)
    return None


def main() -> int:
    pair = "EURUSD"
    cfg: dict = {}
    path = history_path(pair, "1h", cfg)
    hist = load_history_csv(path)
    print(f"BEFORE bars={len(hist)} {hist.index.min()} -> {hist.index.max()}", flush=True)
    todo = _todo(hist, HOLE_START, HOLE_END)
    print(f"hole todo={len(todo)} window {HOLE_START} -> {HOLE_END}", flush=True)
    if todo:
        rows: list[pd.DataFrame] = []
        ok = 0
        empty = 0
        since_ckpt = 0
        for i, hour in enumerate(todo, start=1):
            bars = _fetch_hour(pair, hour)
            if bars is None:
                empty += 1
            else:
                rows.append(bars)
                ok += 1
            since_ckpt += 1
            if i % 25 == 0 or i == len(todo):
                print(
                    f"progress {i}/{len(todo)} ok={ok} empty={empty} pending_rows={len(rows)}",
                    flush=True,
                )
            if since_ckpt >= CKPT_EVERY or i == len(todo):
                if rows:
                    fresh = pd.concat(rows)
                    rows.clear()
                    hist = _merge(hist, fresh)
                    _save_bars(pair, "1h", hist, SOURCE_DUKA, cfg)
                    print(
                        f"checkpoint bars={len(hist)} ok={ok} empty={empty} remaining_est={len(todo) - i}",
                        flush=True,
                    )
                since_ckpt = 0
                time.sleep(0.35)

    hist = load_history_csv(path)
    vol = pd.to_numeric(hist["Volume"], errors="coerce")
    left = len(_todo(hist, HOLE_START, HOLE_END))
    print(
        f"AFTER 1h bars={len(hist)} {hist.index.min()} -> {hist.index.max()} "
        f"zero_vol={int((vol.fillna(0) <= 0).sum())} hole_remaining={left}",
        flush=True,
    )
    daily = resample_bars(hist, "1d")
    _save_bars(pair, "1d", daily, SOURCE_DUKA, cfg)
    print(f"AFTER 1d bars={len(daily)} {daily.index.min()} -> {daily.index.max()}", flush=True)
    print("DONE_EURUSD_HOLE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
