"""Daily runner for the forward paper shadow (FROZEN_RULES_20261022.md). Paper only; NO MT5 orders.

Reads data/history + the desk tip cache (only bars after history's end), builds closed 21:00 UTC daily bars,
snapshots MT5 demo swaps read-only (symbol_info), refreshes BIS policy rates (free bulk zip) when stale,
and appends to data/forward_shadow/forward_journal.jsonl. Idempotent; catches up missed days.
Usage: python scripts/forward_shadow_lowfreq.py [--status] [--no-mt5]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forex_lab import forward_shadow as fs  # noqa: E402

OUTDIR = ROOT / "data" / "forward_shadow"
RATES_DIR = OUTDIR / "rates"
BIS_URL = "https://data.bis.org/static/bulk/WS_CBPOL_csv_flat.zip"
AREAS = {"US": "USD", "XM": "EUR", "JP": "JPY", "GB": "GBP", "AU": "AUD", "NZ": "NZD", "CA": "CAD", "CH": "CHF"}
FALLBACK_RATES = ROOT / "data" / "research" / "bis" / "cbpol_daily_8ccy.csv"


def log(msg: str) -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    with (OUTDIR / "runs.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}\n")


def hourly(pair: str) -> tuple[pd.DataFrame, dict]:
    h = pd.read_csv(ROOT / "data" / "history" / f"{pair}_1h.csv", index_col=0, parse_dates=True)
    h = h[~h.index.duplicated(keep="last")].sort_index()[["Open", "High", "Low", "Close"]].astype(float)
    info = {"history_last": str(h.index[-1]), "tip_rows_used": 0}
    tip_p = ROOT / "data" / f"{pair}_1h.csv"
    if tip_p.exists():
        t = pd.read_csv(tip_p, index_col=0, parse_dates=True)
        t = t[~t.index.duplicated(keep="last")].sort_index()
        t = t[t.index > h.index[-1]][["Open", "High", "Low", "Close"]].astype(float).dropna()
        info["tip_rows_used"] = int(len(t)); info["tip_last"] = str(t.index[-1]) if len(t) else None
        h = pd.concat([h, t])
    return h, info


def refresh_rates(max_age_h: float = 72.0) -> tuple[pd.DataFrame | None, str]:
    RATES_DIR.mkdir(parents=True, exist_ok=True)
    csv = RATES_DIR / "cbpol_daily_8ccy.csv"
    src = "cached"
    if not csv.exists() or (time.time() - csv.stat().st_mtime) > max_age_h * 3600:
        try:
            raw = urllib.request.urlopen(BIS_URL, timeout=120).read()
            zf = zipfile.ZipFile(io.BytesIO(raw)); name = zf.namelist()[0]
            keep = []
            with zf.open(name) as fh:
                for ch in pd.read_csv(fh, usecols=[3, 4, 5, 6], chunksize=500000, dtype=str):
                    ch.columns = ["freq", "area", "t", "v"]
                    keep.append(ch[ch["freq"].str.startswith("D") & ch["area"].str[:2].isin(AREAS.keys())])
            d = pd.concat(keep); d["ccy"] = d["area"].str[:2].map(AREAS); d["v"] = pd.to_numeric(d["v"], errors="coerce")
            w = d.pivot_table(index="t", columns="ccy", values="v", aggfunc="last")
            w.index = pd.to_datetime(w.index); w = w.sort_index(); w = w[w.index >= "2013-01-01"]
            w.to_csv(csv); src = "bis_download"
        except Exception as exc:  # noqa: BLE001
            log(f"BIS refresh failed: {type(exc).__name__}: {exc}")
            src = "stale_cache" if csv.exists() else "fallback_research"
    path = csv if csv.exists() else (FALLBACK_RATES if FALLBACK_RATES.exists() else None)
    if path is None:
        return None, "none"
    r = pd.read_csv(path, index_col=0, parse_dates=True).sort_index()
    full = pd.date_range(r.index.min(), max(r.index.max(), pd.Timestamp.now("UTC").tz_localize(None).normalize()) + pd.Timedelta(days=7), freq="D")
    return r.reindex(full).ffill(), f"{src}:{path.name}:last={r.index.max().date()}"


def read_swaps() -> dict:
    """Read-only MT5 symbol_info swap fields (DEMO asserted). Never sends anything."""
    from forex_lab import mt5_feed
    mt5_feed.connect(require_demo=True)
    mt5 = mt5_feed._mt5()
    try:
        acc = mt5.account_info()
        if acc is None or int(acc.trade_mode) != int(mt5.ACCOUNT_TRADE_MODE_DEMO):
            raise RuntimeError("not a DEMO account; refusing")
        syms = mt5_feed.resolve_symbols(fs.PAIRS)
        out = {}
        for p in fs.PAIRS:
            i = mt5.symbol_info(syms[p])
            out[p] = {"symbol": syms[p], "swap_long": float(i.swap_long), "swap_short": float(i.swap_short),
                      "swap_mode": int(i.swap_mode), "swap_rollover3days": int(i.swap_rollover3days)}
        return {"server": acc.server, "swaps": out}
    finally:
        mt5.shutdown()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true"); ap.add_argument("--no-mt5", action="store_true")
    a = ap.parse_args(argv)
    journal = fs.load()
    if a.status:
        print(json.dumps(fs.status(journal), indent=1, default=str)); return 0
    lock = OUTDIR / "run.lock"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime < 1800:
        log("another run holds the lock; exiting"); return 0
    lock.write_text(str(os.getpid()))
    try:
        now = datetime.now(timezone.utc)
        daily, info = {}, {}
        for p in fs.PAIRS:
            h, info[p] = hourly(p)
            daily[p] = fs.lf.resample(h, "D")
        rates, rates_src = refresh_rates()
        snap_rows, swaps, swap_src = [], None, "none"
        if not a.no_mt5:
            try:
                s = read_swaps(); swaps = s["swaps"]; swap_src = f"mt5_snapshot:{now:%Y-%m-%d}"
                snap_rows.append({"event": "swap_snapshot", "key": f"swap|{now:%Y-%m-%d}", "read_utc": fs._iso(now),
                                  "server": s["server"], "swaps": swaps})
            except Exception as exc:  # noqa: BLE001
                log(f"MT5 swap read failed: {type(exc).__name__}: {exc}")
        if swaps is None:
            prev = [r for r in journal if r.get("event") == "swap_snapshot"]
            if prev:
                swaps = prev[-1]["swaps"]; swap_src = f"last_snapshot:{prev[-1]['read_utc']}"
        rows = snap_rows + fs.step(journal + snap_rows, daily, rates, swaps, swap_src, now)
        if rows and rows[-1].get("event") != "init":
            rows.append({"event": "run", "key": f"run|{now:%Y-%m-%d}", "run_utc": fs._iso(now), "rates_source": rates_src,
                         "swap_source": swap_src, "data": info})
        elif rows and rows[-1].get("event") == "init":
            rows[-1].update({"rates_source": rates_src, "swap_source": swap_src, "data": info})
        n = fs.append(rows)
        log(f"ok appended={n} rates={rates_src} swaps={swap_src} last_bars={ {p: str(d.index[-1]) for p, d in daily.items()} }")
        print(json.dumps({"appended": n, "rates": rates_src, "swaps": swap_src, "status": fs.status(fs.load())}, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"FAILED {type(exc).__name__}: {exc}"); raise
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
