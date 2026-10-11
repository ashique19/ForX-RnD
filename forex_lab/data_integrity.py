"""Refuse Replay and backtest when OHLC looks like the corrupted Jetta decode.

The bad decoder rebuilt Open/High/Low from the previous close. Stored Jetta-era
bars are about 60% wickless, with a median |Open - previous Close| of 3-9 pips.
Clean Dukascopy bars are about 0% wickless and under 0.1 pip.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from forex_lab.config_loader import pip_size_for_pair
from forex_lab.history import HistoryError

DEFAULT_MAX_WICKLESS_SHARE = 0.10
DEFAULT_MAX_MEDIAN_OPEN_GAP_PIPS = 1.0
_WICK_EPS = 1e-12


class DataIntegrityError(HistoryError):
    """Bar set failed the wickless / open-gap gate."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason="integrity")


def bar_integrity_stats(
    df: pd.DataFrame,
    pair: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wickless share and median |Open - previous Close| in pips.

    A bar is wickless when High equals max(Open, Close) and Low equals
    min(Open, Close), within 1e-12. The first bar has no previous close, so
    it is left out of the gap.
    """
    pip = float(pip_size_for_pair(pair, cfg or {}))
    if df is None or len(df) == 0:
        return {
            "n": 0,
            "n_valid": 0,
            "wickless_share": 0.0,
            "median_open_gap_pips": None,
            "pip": pip,
        }
    if not isinstance(df.index, pd.RangeIndex):
        ordered = df.sort_index()
        df = ordered[~ordered.index.duplicated(keep="last")]
    missing = [col for col in ("Open", "High", "Low", "Close") if col not in df.columns]
    if missing:
        raise DataIntegrityError(
            f"Refusing bars for {str(pair).upper()}: history is missing {', '.join(missing)}."
        )
    o = pd.to_numeric(df["Open"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(df["High"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(df["Low"], errors="coerce").to_numpy(dtype=float)
    c = pd.to_numeric(df["Close"], errors="coerce").to_numpy(dtype=float)
    body_hi = np.maximum(o, c)
    body_lo = np.minimum(o, c)
    valid = np.isfinite(o) & np.isfinite(h) & np.isfinite(low) & np.isfinite(c)
    wickless = valid & (np.abs(h - body_hi) < _WICK_EPS) & (np.abs(low - body_lo) < _WICK_EPS)
    n_valid = int(valid.sum())
    share = float(wickless.sum() / n_valid) if n_valid else 0.0
    gap_px = np.abs(o[1:] - c[:-1])
    finite = np.isfinite(gap_px)
    median = float(np.median(gap_px[finite] / pip)) if finite.any() else None
    return {
        "n": int(len(df)),
        "n_valid": n_valid,
        "wickless_share": share,
        "median_open_gap_pips": median,
        "pip": pip,
    }


def assert_bar_integrity(
    df: pd.DataFrame,
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    max_wickless_share: float = DEFAULT_MAX_WICKLESS_SHARE,
    max_median_open_gap_pips: float = DEFAULT_MAX_MEDIAN_OPEN_GAP_PIPS,
    context: str = "backtest",
) -> dict[str, Any]:
    """Raise DataIntegrityError when wickless share or the open gap is too high.

    Defaults: wickless share > 10%, or median |Open - previous Close| > 1 pip.
    Equal to the limit is allowed.
    """
    stats = bar_integrity_stats(df, pair, cfg)
    breaches: list[str] = []
    share = float(stats["wickless_share"])
    if stats["n_valid"] and share > float(max_wickless_share):
        breaches.append(f"wickless share {share:.1%} exceeds {float(max_wickless_share):.0%}")
    median = stats["median_open_gap_pips"]
    if median is not None and float(median) > float(max_median_open_gap_pips):
        breaches.append(
            f"median |Open - previous Close| {float(median):.2f} pips "
            f"exceeds {float(max_median_open_gap_pips):.1f}"
        )
    if breaches:
        raise DataIntegrityError(
            f"Refusing {context} on {str(pair).upper()}: {'; '.join(breaches)}. "
            "These bars look like the corrupted Jetta decode "
            "(Open/High/Low rebuilt from the previous close). "
            "Re-pull history before running Replay or backtest."
        )
    return stats
