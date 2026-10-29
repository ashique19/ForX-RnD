"""Replace a bad (old yfinance) segment of a desk 1h cache with validated data/history bars.

Desk-safe and atomic:
  * bars at/after ``--seam`` are kept as their ORIGINAL text lines (byte-identical);
  * bars before the seam come verbatim from data/history/<PAIR>_1h.csv (validated Dukascopy/HistData bid OHLC
    and Dukascopy tick volume) - no synthesized or interpolated prices;
  * ``--apply`` waits for the next desk refresh write (mtime change), then reads the fresh file, builds the new
    file in a temp file and swaps it with os.replace only if the source mtime did not move in between.
Without ``--apply`` it only prints the plan and checks. Backups are written before any swap.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.config_loader import load_config, pip_size_for_pair  # noqa: E402
from forex_lab.data_integrity import bar_integrity_stats  # noqa: E402


def _fmt(x: float) -> str:
    return repr(float(x))


def build_spliced_text(desk_text: str, hist: pd.DataFrame, seam: pd.Timestamp,
                       start: pd.Timestamp | None = None) -> tuple[str, dict]:
    lines = desk_text.splitlines()
    header, body = lines[0], [ln for ln in lines[1:] if ln.strip()]
    ts = pd.to_datetime([ln.split(",", 1)[0] for ln in body])
    keep = [ln for ln, t in zip(body, ts) if t >= seam]
    desk_start = ts.min()
    # optional backfill: history bars before the desk file's first bar (never a synthesized price)
    start = desk_start if start is None else min(pd.Timestamp(start), desk_start)
    h = hist.loc[(hist.index >= start) & (hist.index < seam), ["Open", "High", "Low", "Close", "Volume"]]
    if h.isna().any().any():
        raise ValueError("history segment has NaNs")
    new_rows = [f"{t:%Y-%m-%d %H:%M:%S},{_fmt(r.Open)},{_fmt(r.High)},{_fmt(r.Low)},{_fmt(r.Close)},{_fmt(r.Volume)}"
                for t, r in h.iterrows()]
    old_seg = ts[ts < seam]
    info = {"desk_start": str(desk_start), "start": str(start), "seam": str(seam), "old_rows_replaced": int(len(old_seg)), "history_rows_in": int(len(h)),
            "kept_rows": int(len(keep)), "hours_only_in_old": int(len(old_seg.difference(h.index))),
            "hours_only_in_history": int(len(h.index.difference(old_seg)))}
    return "\n".join([header] + new_rows + keep) + "\n", info


def check_frame(df: pd.DataFrame, pair: str, cfg: dict, seam: pd.Timestamp) -> dict:
    idx = df.index
    d = pd.Series(idx[1:] - idx[:-1], index=idx[1:])
    long_gaps = d[d > pd.Timedelta(hours=1)]
    # normal FX weekend: the gap starts Friday evening and ends Sunday evening UTC
    prev = long_gaps.index - long_gaps.values
    weekend = (pd.DatetimeIndex(prev).dayofweek == 4) & (long_gaps.index.dayofweek == 6) & (long_gaps.values <= np.timedelta64(52, "h"))
    odd = long_gaps[~weekend]
    pip = pip_size_for_pair(pair, cfg)
    pos = idx.get_indexer([seam])[0]
    seam_gap = float(abs(df.Open.iloc[pos] - df.Close.iloc[pos - 1]) / pip) if pos > 0 else None
    st = bar_integrity_stats(df, pair, cfg)
    return {"rows": int(len(df)), "duplicates": int(idx.duplicated().sum()), "sorted": bool(idx.is_monotonic_increasing),
            "weekend_gaps": int(weekend.sum()), "non_weekend_gaps": [(str(pd.Timestamp(e) - pd.Timedelta(g)), str(e), str(pd.Timedelta(g))) for e, g in zip(odd.index, odd.values)][:30],
            "n_non_weekend_gaps": int(len(odd)), "seam_open_gap_pips": seam_gap,
            "median_open_gap_pips": st["median_open_gap_pips"], "wickless_share": st["wickless_share"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="EURUSD")
    ap.add_argument("--seam", required=True, help="first desk bar (UTC) to keep, e.g. '2026-06-19 02:00'")
    ap.add_argument("--backup-dir", required=True)
    ap.add_argument("--start", default=None, help="optional: also backfill history bars from this UTC time when it is earlier than the desk file's first bar")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--wait-refresh-s", type=int, default=1200, help="max wait for the next desk write before giving up")
    ap.add_argument("--settle-s", type=int, default=20)
    a = ap.parse_args()
    cfg = load_config(); pair = a.pair.upper(); seam = pd.Timestamp(a.seam)
    desk = ROOT / "data" / f"{pair}_1h.csv"
    hist = pd.read_csv(ROOT / "data" / "history" / f"{pair}_1h.csv", index_col=0, parse_dates=True)
    hist = hist[~hist.index.duplicated(keep="last")].sort_index()
    if a.apply:
        m0 = desk.stat().st_mtime; t0 = time.time()
        print("waiting for the next desk write ...", flush=True)
        while desk.stat().st_mtime == m0:
            if time.time() - t0 > a.wait_refresh_s:
                print("no desk write seen - aborting without changes"); return 3
            time.sleep(5)
        time.sleep(a.settle_s)
    m_read = desk.stat().st_mtime
    text = desk.read_text(encoding="utf-8")
    new_text, info = build_spliced_text(text, hist, seam, pd.Timestamp(a.start) if a.start else None)
    tmp = desk.with_name(desk.name + ".tmp_splice")
    tmp.write_text(new_text, encoding="utf-8", newline="\n")
    new_df = pd.read_csv(tmp, index_col=0, parse_dates=True)
    old_df = pd.read_csv(desk, index_col=0, parse_dates=True)
    info["check_new"] = check_frame(new_df, pair, cfg, seam)
    info["old_median_open_gap_pips"] = bar_integrity_stats(old_df, pair, cfg)["median_open_gap_pips"]
    kept_old = text.splitlines()[1:]; kept_old = [ln for ln in kept_old if ln.strip() and pd.Timestamp(ln.split(",", 1)[0]) >= seam]
    info["kept_text_identical"] = kept_old == new_text.splitlines()[1 + info["history_rows_in"]:]
    seg = hist.loc[(hist.index >= old_df.index.min()) & (hist.index < seam)]
    vol_cmp = old_df.Volume.reindex(seg.index)
    info["volume_ratio_old_over_history_median"] = float((vol_cmp / seg.Volume).median())
    print(json.dumps(info, indent=1, default=str))
    ok = (info["check_new"]["duplicates"] == 0 and info["check_new"]["median_open_gap_pips"] <= 1.0
          and info["check_new"]["wickless_share"] <= 0.10 and info["kept_text_identical"])
    if not a.apply:
        tmp.unlink(missing_ok=True); print("dry run: no change", "OK" if ok else "CHECKS FAILED"); return 0 if ok else 2
    if not ok:
        tmp.unlink(missing_ok=True); print("checks failed - no change"); return 2
    bdir = Path(a.backup_dir); bdir.mkdir(parents=True, exist_ok=True)
    for p in desk.parent.glob(f"{pair}_1h.*"):
        if p.suffix in (".csv", ".json", ".source") and not p.name.endswith(".tmp_splice"):
            shutil.copy2(p, bdir / p.name)
    if desk.stat().st_mtime != m_read:
        tmp.unlink(missing_ok=True); print("desk wrote during the splice - aborted, no change"); return 4
    os.replace(tmp, desk)
    print("swapped atomically at", time.strftime("%Y-%m-%d %H:%M:%S"), "backup", bdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())