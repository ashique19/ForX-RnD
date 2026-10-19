"""Freeze per-pair MT5 volume anchors and repair drifted MT5 bar volumes (Volume column only).

  freeze  --snapshot DIR      anchor = median(pre-MT5 cache Volume / MT5 tick_volume), last 500 overlapping
                              bars, from a cache snapshot taken BEFORE MT5 ever wrote it. Refuses to
                              overwrite a frozen pair without --force.
  rewrite --since UTC [--apply]
                              cache bars >= since: Volume = round(MT5 tick_volume * anchor). Every bar must
                              match MT5 OHLC exactly (else the pair is refused). OHLC never touched.
                              Dry-run unless --apply. Back up data/ first.

Read-only towards MT5 (copy_rates only); never calls the desk pipeline/train endpoints.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab import data as D  # noqa: E402
from forex_lab import mt5_volume_anchor as VA  # noqa: E402

PAIRS = ["AUDUSD", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]
SEAM_UTC = "2026-10-13 21:00"


def _read(path: Path) -> pd.DataFrame:
    return D._normalize_ohlcv(pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip"))


def _ohlc_text(path: Path) -> pd.DataFrame:
    """OHLC columns exactly as written in the CSV (strings), for a byte-level no-touch check."""
    t = pd.read_csv(path, dtype=str)
    return t[[c for c in t.columns if c.strip().title() in {"Open", "High", "Low", "Close"}] + [t.columns[0]]]


def _mt5(pair: str, count: int, fetch=None) -> pd.DataFrame:
    bars = (fetch or D.fetch_mt5_1h_utc)(pair, count)
    bars, _ = D.drop_forming_bars(bars, "1h", None)
    return bars


def _history_median(pair: str, mt5: pd.DataFrame) -> float | None:
    p = ROOT / "data" / "history" / f"{pair}_1h.csv"
    if not p.exists():
        return None
    try:
        h = _read(p)
        j = h[["Volume"]].join(mt5[["Volume"]], rsuffix="_m", how="inner")
        j = j[(j.Volume > 0) & (j.Volume_m > 0)]
        return round(float((j.Volume / j.Volume_m).median()), 6) if len(j) >= 50 else None
    except Exception:  # noqa: BLE001 - transparency only
        return None


def freeze(pairs, snapshot: Path, data_dir: Path, *, force=False, count=2000, fetch=None, apply=True) -> dict:
    recs, out = {}, {}
    for pair in pairs:
        f = snapshot / f"{pair}_1h.csv"
        if not f.exists():
            out[pair] = {"skipped": "snapshot csv missing"}
            continue
        mt5 = _mt5(pair, count, fetch)
        try:
            rec = VA.compute_anchor(_read(f), mt5)
        except ValueError as exc:
            out[pair] = {"refused": str(exc)}
            continue
        src = snapshot / f"{pair}_1h.source"
        rec.update({"method": f"median(pre-MT5 cache Volume / MT5 tick_volume), last {VA.ANCHOR_WINDOW} overlapping bars",
                    "snapshot": str(f), "snapshot_source": src.read_text(encoding="utf-8").strip() if src.exists() else None,
                    "history_file_median": _history_median(pair, mt5)})
        recs[pair] = rec
        out[pair] = rec
    if apply and recs:
        VA.write_anchors(data_dir, recs, force=force)
    return out


def rewrite(pairs, data_dir: Path, since: str, *, apply=False, count=2000, fetch=None) -> dict:
    out = {}
    for pair in pairs:
        path = data_dir / f"{pair}_1h.csv"
        if not path.exists():
            out[pair] = {"skipped": "cache csv missing"}
            continue
        rec = VA.load_anchor(pair, data_dir)
        if rec is None:
            out[pair] = {"refused": "no frozen anchor"}
            continue
        cache = _read(path)
        mt5 = _mt5(pair, count, fetch)
        before = VA.seam_medians(cache, pd.Timestamp(since))
        try:
            new, rep = VA.rewrite_volume(cache, mt5, float(rec["factor"]), pd.Timestamp(since))
        except ValueError as exc:
            out[pair] = {"refused": str(exc)}
            continue
        rep.update({"factor": float(rec["factor"]), "seam_before_fix": before,
                    "seam_after_fix": VA.seam_medians(new, pd.Timestamp(since)), "applied": bool(apply)})
        if apply and rep["changed"]:
            txt_before = _ohlc_text(path)
            new.to_csv(path)
            rep["ohlc_text_unchanged"] = bool(_ohlc_text(path).equals(txt_before))
            chk = _read(path)
            j = chk.loc[chk.index >= pd.Timestamp(since)]
            rep["ohlc_matches_mt5_after_write"] = bool(VA.ohlc_equal_mask(j, mt5).all())
        out[pair] = rep
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--snapshot", required=True)
    f.add_argument("--force", action="store_true")
    f.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("rewrite")
    r.add_argument("--since", default=SEAM_UTC)
    r.add_argument("--apply", action="store_true")
    for p in (f, r):
        p.add_argument("--pair", action="append")
        p.add_argument("--data-dir", default=str(ROOT / "data"))
    a = ap.parse_args(argv)
    pairs = [x.upper() for x in (a.pair or PAIRS)]
    if a.cmd == "freeze":
        res = freeze(pairs, Path(a.snapshot), Path(a.data_dir), force=a.force, apply=not a.dry_run)
    else:
        res = rewrite(pairs, Path(a.data_dir), a.since, apply=a.apply)
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())