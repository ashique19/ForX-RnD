"""Frozen MT5 tick-volume -> cache-volume scale factors (one per pair).

Why: the MT5 tip refresh used to re-derive ``median(cache Volume / MT5 tick_volume)`` from the
cache on every write. Once MT5 itself had written the cache, that ratio is self-referential; and a
refresh whose overlap fell below ``MT5_MIN_OVERLAP`` silently wrote raw tick volume (factor 1.0),
which every later refresh then "confirmed" (2026-10-13 21:00 UTC seam, 9 of 10 pairs).

Fix: the factor is frozen ONCE from the pre-MT5 (Dukascopy/HistData/yfinance) version of each
cache file versus MT5 history, stored in ``data/mt5_volume_anchor.json`` (data, not committed), and
never re-derived from MT5-written bars. Only Volume is ever rescaled; OHLC is never touched.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ANCHOR_FILE = "mt5_volume_anchor.json"
ANCHOR_SCHEMA = "mt5_volume_anchor_v1"
# Same window the legacy self-calibration used at the first MT5 bootstrap, so the anchor equals the
# factor already applied to the MT5-written bars before the drift (no new volume step).
ANCHOR_WINDOW = 500
ANCHOR_MIN_OVERLAP = 200
OHLC = ("Open", "High", "Low", "Close")


def anchor_path(data_dir: Path) -> Path:
    return Path(data_dir) / ANCHOR_FILE


def load_doc(data_dir: Path) -> dict[str, Any]:
    try:
        doc = json.loads(anchor_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def load_anchor(pair: str, data_dir: Path) -> dict[str, Any] | None:
    """Return the frozen anchor record for ``pair`` (with a valid positive ``factor``) or None."""
    rec = (load_doc(data_dir).get("pairs") or {}).get(str(pair).upper())
    if not isinstance(rec, dict):
        return None
    try:
        f = float(rec.get("factor"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or not (0.0 < f < 1000.0):
        return None
    return rec


def ohlc_equal_mask(a: pd.DataFrame, b: pd.DataFrame, tol: float = 1e-9) -> pd.Series:
    """Per-bar exact OHLC equality on the shared index."""
    j = a[list(OHLC)].join(b[list(OHLC)], rsuffix="_m", how="inner")
    d = pd.concat([(j[c] - j[f"{c}_m"]).abs() for c in OHLC], axis=1).max(axis=1)
    return d <= tol


def compute_anchor(pre_mt5: pd.DataFrame, mt5_bars: pd.DataFrame, *, window: int = ANCHOR_WINDOW,
                   min_overlap: int = ANCHOR_MIN_OVERLAP) -> dict[str, Any]:
    """Median(pre-MT5 cache Volume / MT5 tick_volume) over the last ``window`` overlapping bars.

    ``pre_mt5`` must be a cache file written BEFORE MT5 ever wrote it; any bar whose OHLC equals
    MT5 exactly means MT5 already touched it and the snapshot is refused.
    """
    j = pre_mt5[["Volume", *OHLC]].join(mt5_bars[["Volume", *OHLC]], rsuffix="_m", how="inner")
    if j.empty:
        raise ValueError("no overlap between pre-MT5 snapshot and MT5 history")
    eq = ohlc_equal_mask(pre_mt5, mt5_bars)
    if int(eq.sum()) > 0:
        raise ValueError(f"snapshot is not pre-MT5: {int(eq.sum())} bars equal MT5 OHLC exactly")
    v = pd.to_numeric(j["Volume"], errors="coerce")
    m = pd.to_numeric(j["Volume_m"], errors="coerce")
    ok = (v > 0) & (m > 0)
    r = (v[ok] / m[ok]).dropna()
    if len(r) < min_overlap:
        raise ValueError(f"overlap {len(r)} < {min_overlap}")
    w = r.tail(window)
    return {"factor": round(float(w.median()), 6), "overlap_bars": int(len(w)),
            "window_first_utc": str(w.index.min()), "window_last_utc": str(w.index.max()),
            "all_overlap_median": round(float(r.median()), 6), "all_overlap_bars": int(len(r)),
            "p10": round(float(r.quantile(0.1)), 4), "p90": round(float(r.quantile(0.9)), 4)}


def write_anchors(data_dir: Path, records: dict[str, dict[str, Any]], *, force: bool = False,
                  meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge new pair records into the anchor file. Existing pairs are kept unless ``force``."""
    doc = load_doc(data_dir) or {}
    pairs = dict(doc.get("pairs") or {})
    for pair, rec in records.items():
        if pair.upper() in pairs and not force:
            raise ValueError(f"anchor for {pair.upper()} already frozen; refusing to overwrite without --force")
        pairs[pair.upper()] = {**rec, "frozen_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    doc = {"schema": ANCHOR_SCHEMA, **(meta or {}), **{k: v for k, v in doc.items() if k not in {"schema", "pairs"}},
           "pairs": dict(sorted(pairs.items()))}
    p = anchor_path(data_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(p)
    return doc


def rewrite_volume(cache: pd.DataFrame, mt5_bars: pd.DataFrame, factor: float,
                   since_utc: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Volume = round(MT5 tick_volume * factor) for cache bars >= ``since_utc``; OHLC untouched.

    Every bar in range must exist in ``mt5_bars`` with exactly equal OHLC, otherwise ValueError
    (no partial rewrite, nothing synthesized).
    """
    since = pd.Timestamp(since_utc)
    if since.tzinfo is not None:
        since = since.tz_convert("UTC").tz_localize(None)
    idx = cache.index[cache.index >= since]
    if len(idx) == 0:
        return cache.copy(), {"bars": 0, "changed": 0}
    missing = idx.difference(mt5_bars.index)
    if len(missing):
        raise ValueError(f"{len(missing)} cache bars >= {since} missing from MT5 (first {missing[0]})")
    eq = ohlc_equal_mask(cache.loc[idx], mt5_bars.loc[idx])
    if not bool(eq.all()):
        bad = eq[~eq].index
        raise ValueError(f"{len(bad)} bars >= {since} differ from MT5 OHLC (first {bad[0]}); refusing")
    mv = pd.to_numeric(mt5_bars.loc[idx, "Volume"], errors="coerce")
    if bool((mv.isna() | (mv <= 0)).any()):
        raise ValueError("MT5 tick volume missing/zero in range; refusing")
    out = cache.copy()
    old = pd.to_numeric(out.loc[idx, "Volume"], errors="coerce")
    new = (mv * float(factor)).round()
    out.loc[idx, "Volume"] = new.astype(out["Volume"].dtype) if out["Volume"].dtype.kind in "iuf" else new
    for c in OHLC:
        if not out[c].equals(cache[c]):
            raise AssertionError(f"{c} changed during volume rewrite")
    rep = {"bars": int(len(idx)), "changed": int(((old - new).abs() > 0.5).sum()),
           "first_utc": str(idx.min()), "last_utc": str(idx.max()),
           "implied_factor_before": round(float((old / mv).median()), 4),
           "implied_factor_after": round(float((new / mv).median()), 4)}
    return out, rep


def seam_medians(df: pd.DataFrame, seam_utc: pd.Timestamp, days: int = 14) -> dict[str, Any]:
    """Median Volume over ``days`` before vs from the seam (closed bars with Volume > 0)."""
    seam = pd.Timestamp(seam_utc)
    v = pd.to_numeric(df["Volume"], errors="coerce")
    v = v[v > 0]
    before = v[(v.index >= seam - pd.Timedelta(days=days)) & (v.index < seam)]
    after = v[(v.index >= seam) & (v.index < seam + pd.Timedelta(days=days))]
    mb = float(before.median()) if len(before) else float("nan")
    ma = float(after.median()) if len(after) else float("nan")
    return {"median_before": round(mb, 1), "median_after": round(ma, 1), "n_before": int(len(before)),
            "n_after": int(len(after)), "after_over_before": round(ma / mb, 3) if mb and mb == mb and ma == ma else None}