"""Held-out exam (Roadmap revision 2026-10-09, item 1).

The most recent window (``research/holdout.json`` -> ``cutoff_utc``) is untouched
OOS. Training / challenger fits / Replay model fits must not use any row whose
bar time OR label-settle time is at or after the cutoff. Results on bars at or
after the cutoff are reported separately as ``holdout``.

``FORX_HOLDOUT_FILE`` overrides the file path; ``FORX_HOLDOUT_FILE=off``
disables enforcement (unit tests with synthetic, recent-dated bars only).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILE = ROOT / "research" / "holdout.json"
ENV = "FORX_HOLDOUT_FILE"


class HoldoutViolation(RuntimeError):
    """Raised when a fit would include holdout rows."""


def holdout_file() -> Path | None:
    raw = os.environ.get(ENV)
    if raw is not None and raw.strip().lower() in {"", "off", "none", "0", "false"}:
        return None
    return Path(raw) if raw else DEFAULT_FILE


def load_cutoff() -> pd.Timestamp | None:
    """Cutoff as a naive UTC timestamp, or None when disabled / file missing."""
    p = holdout_file()
    if p is None or not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    ts = pd.Timestamp(data["cutoff_utc"])
    return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts


def _naive(idx: Iterable[Any]) -> pd.DatetimeIndex:
    di = pd.DatetimeIndex(idx)
    return di.tz_convert("UTC").tz_localize(None) if di.tz is not None else di


def train_mask(
    index: Iterable[Any],
    *,
    cutoff: pd.Timestamp | None = None,
    horizon: int = 0,
    bar_index: Iterable[Any] | None = None,
) -> pd.Series:
    """True for rows that may be used for training.

    A row is trainable only if its bar time is before the cutoff AND its label
    has settled before the cutoff (``horizon`` bars later on ``bar_index``; the
    row's own index is used as the mesh when ``bar_index`` is None).
    """
    idx = _naive(index)
    cut = load_cutoff() if cutoff is None else cutoff
    if cut is None:
        return pd.Series(True, index=pd.DatetimeIndex(index))
    mesh = _naive(bar_index) if bar_index is not None else idx
    ok = np.asarray(idx < cut)
    h = int(horizon or 0)
    if h > 0 and len(mesh):
        locs = mesh.get_indexer(idx)
        ready = locs + h
        settle_ok = (locs >= 0) & (ready < len(mesh))
        good = np.flatnonzero(settle_ok)
        settled_before = np.zeros(len(idx), dtype=bool)
        if len(good):
            settled_before[good] = mesh.values[ready[good]] < np.datetime64(cut.to_datetime64())
        ok = ok & settled_before
    return pd.Series(ok, index=pd.DatetimeIndex(index))


def assert_no_holdout(index: Iterable[Any], *, cutoff: pd.Timestamp | None = None, what: str = "training rows") -> None:
    cut = load_cutoff() if cutoff is None else cutoff
    if cut is None:
        return
    idx = _naive(index)
    if len(idx) and idx.max() >= cut:
        raise HoldoutViolation(f"{what} reach {idx.max()} >= holdout cutoff {cut}; refusing to fit on the holdout")


def _pf(r: pd.Series) -> float | None:
    g, l = float(r[r > 0].sum()), float(-r[r < 0].sum())
    if l > 0:
        return g / l
    return None


def _dd(r: pd.Series) -> float:
    eq = r.fillna(0).cumsum().to_numpy()
    if not len(eq):
        return 0.0
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    return float((eq - peak).min())


def split_report(
    trades: pd.DataFrame | None,
    *,
    time_col: str,
    ret_col: str,
    cutoff: pd.Timestamp | None = None,
) -> dict[str, Any] | None:
    """PF / max DD (additive, unit size) / n before vs at-or-after the cutoff."""
    cut = load_cutoff() if cutoff is None else cutoff
    if cut is None:
        return None
    out: dict[str, Any] = {"cutoff_utc": str(cut)}
    if trades is None or len(trades) == 0 or time_col not in trades or ret_col not in trades:
        out.update({"in_sample": {"n": 0}, "holdout": {"n": 0}})
        return out
    ts = _naive(pd.to_datetime(trades[time_col].astype(str).str.replace(" UTC", "", regex=False)))
    r = pd.to_numeric(trades[ret_col], errors="coerce")
    for name, m in (("in_sample", ts < cut), ("holdout", ts >= cut)):
        rr = r[np.asarray(m)].dropna()
        out[name] = {"n": int(len(rr)), "profit_factor": _pf(rr), "max_drawdown_additive": _dd(rr),
                     "expectancy": float(rr.mean()) if len(rr) else None}
    return out
