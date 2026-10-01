"""Immediate tip Volume repair for EURUSD. Keep OHLC; refill Volume>0 only."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forex_lab.config_loader import load_config
from forex_lab.data import (
    REQUIRED_COLS,
    SOURCE_DUKASCOPY,
    data_path,
    load_cached_ohlcv,
    write_cache_source,
    _normalize_ohlcv,
)
from forex_lab.history import fetch_dukascopy_recent_bars, history_path, load_history_csv


def coalesce_volume_only(base: pd.DataFrame, donor: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if base is None or base.empty:
        return (_normalize_ohlcv(donor) if donor is not None and not donor.empty else base), 0
    out = _normalize_ohlcv(base).copy()
    if donor is None or donor.empty:
        return out, 0
    don = _normalize_ohlcv(donor)
    prior = pd.to_numeric(out["Volume"], errors="coerce")
    newv = pd.to_numeric(don["Volume"], errors="coerce").reindex(out.index)
    n = int(((prior.fillna(0.0) <= 0) & (newv.fillna(0.0) > 0)).sum())
    out["Volume"] = prior.where(prior.fillna(0.0) > 0, newv).fillna(0.0)
    extra = don.loc[don.index > out.index.max()]
    if len(extra):
        out = pd.concat([out, extra])
        out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "Datetime"
    return out[REQUIRED_COLS], n


def repair_pair(pair: str, *, lookback_hours: int = 96) -> dict:
    cfg = load_config()
    live_path = data_path(pair, cfg, "1h")
    live = load_cached_ohlcv(pair, cfg, "1h")
    if live is None or live.empty:
        return {"pair": pair, "ok": False, "error": "no live cache"}
    live = _normalize_ohlcv(live)
    vol0 = pd.to_numeric(live["Volume"], errors="coerce").fillna(0.0)
    before_tip_zero = int((vol0.tail(48) <= 0).sum())
    print(f"[{pair}] BEFORE tip48_zero={before_tip_zero} end={live.index.max()}", flush=True)

    total_fill = 0
    merged = live
    try:
        hist = load_history_csv(history_path(pair, "1h", cfg))
        if hist is not None and not hist.empty:
            merged, n = coalesce_volume_only(merged, hist)
            total_fill += n
            print(f"[{pair}] hist filled {n}", flush=True)
    except Exception as exc:
        print(f"[{pair}] hist skip: {exc}", flush=True)

    t0 = time.time()
    try:
        duka = fetch_dukascopy_recent_bars(pair, lookback_hours=lookback_hours, grain="1h")
    except Exception as exc:
        print(f"[{pair}] duka exception: {exc}", flush=True)
        duka = None
    print(f"[{pair}] duka elapsed={time.time()-t0:.1f}s", flush=True)
    if duka is not None and not duka.empty:
        duka = _normalize_ohlcv(duka[REQUIRED_COLS])
        dvol = pd.to_numeric(duka["Volume"], errors="coerce").fillna(0.0)
        print(
            f"[{pair}] duka bars={len(duka)} {duka.index.min()}->{duka.index.max()} "
            f"pos={int((dvol > 0).sum())}",
            flush=True,
        )
        merged, n = coalesce_volume_only(merged, duka)
        total_fill += n
        print(f"[{pair}] duka filled {n}", flush=True)
        write_cache_source(pair, cfg, "1h", SOURCE_DUKASCOPY)
    else:
        print(f"[{pair}] duka empty", flush=True)

    merged.to_csv(live_path)
    vol = pd.to_numeric(merged["Volume"], errors="coerce").fillna(0.0)
    after_tip_zero = int((vol.tail(48) <= 0).sum())
    print(f"[{pair}] AFTER tip48_zero={after_tip_zero} n_fill={total_fill} end={merged.index.max()}", flush=True)
    print(merged.tail(8)[["Close", "Volume"]].to_string(), flush=True)
    return {
        "pair": pair,
        "ok": True,
        "n_fill": total_fill,
        "before_tip48_zero": before_tip_zero,
        "after_tip48_zero": after_tip_zero,
        "end": str(merged.index.max()),
        "tip_volumes": [float(x) for x in vol.tail(8).tolist()],
    }


if __name__ == "__main__":
    out = repair_pair("EURUSD", lookback_hours=72)
    Path("_TIP_VOL_REPAIR_NOW_20261001.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("DONE", out.get("ok"), "tipV", out.get("tip_volumes"), flush=True)
