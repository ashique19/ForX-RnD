"""Backfill USDJPY 1h+1d history via Dukascopy (~2015→now), monthly chunks."""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forex_lab.history import (  # noqa: E402
    SOURCE_DUKA,
    _now_closed_hour,
    _pull_dukascopy,
    _save_bars,
    history_path,
    load_history_csv,
    resample_bars,
)


def main() -> int:
    pair = "USDJPY"
    cfg: dict = {}
    path = history_path(pair, "1h", cfg)
    hist = load_history_csv(path)
    start = datetime(2015, 1, 1)
    end = _now_closed_hour()
    print(f"BEFORE bars={len(hist)} path={path}", flush=True)
    if not hist.empty:
        print(f"  range {hist.index.min()} -> {hist.index.max()}", flush=True)

    months: list[tuple[datetime, datetime]] = []
    cur = datetime(start.year, start.month, 1)
    while cur <= end:
        nxt = datetime(cur.year + (1 if cur.month == 12 else 0), 1 if cur.month == 12 else cur.month + 1, 1)
        chunk_end = min(end, nxt - timedelta(hours=1))
        months.append((max(cur, start), chunk_end))
        cur = nxt

    def progress(payload: dict) -> None:
        msg = payload.get("message")
        if msg and ("checkpoint" in str(msg) or str(msg).endswith("hours")):
            print(msg, flush=True)

    for i, (cs, ce) in enumerate(months, start=1):
        hist = load_history_csv(path)
        print(f"\n=== USDJPY chunk {i}/{len(months)} {cs} -> {ce} ===", flush=True)
        before = len(hist)
        filled = _pull_dukascopy(
            pair, "1h", cs, ce, hist, cfg, workers=6, progress=progress, fetch_hour=None
        )
        if filled is None or filled.empty:
            print("empty pull", flush=True)
            continue
        _save_bars(pair, "1h", filled, SOURCE_DUKA, cfg)
        vol = pd.to_numeric(filled["Volume"], errors="coerce")
        print(
            f"saved bars={len(filled)} (+{len(filled) - before}) "
            f"zero_vol={int((vol.fillna(0) <= 0).sum())}",
            flush=True,
        )
        time.sleep(0.25)

    hist = load_history_csv(path)
    print(f"AFTER 1h bars={len(hist)} {hist.index.min()} -> {hist.index.max()}", flush=True)
    daily = resample_bars(hist, "1d")
    _save_bars(pair, "1d", daily, SOURCE_DUKA, cfg)
    print(f"AFTER 1d bars={len(daily)} {daily.index.min()} -> {daily.index.max()}", flush=True)
    print("DONE_USDJPY", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
