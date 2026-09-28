"""DXY / US yields cross-asset features (SERIAL step 10).

As-of only: FRED daily prints are lagged by ``lag_days`` (default 1) then
forward-filled onto hourly bars — same publication-lag rule as ``fred.py``.
No same-day leak; no look-ahead into future DXY/yield prints.

Sources (first success wins):
1. Local cache under ``feature_extras.dxy_yields.cache_dir`` (default ``data/fred_cache``)
2. Public FRED CSV (DTWEXBGS = broad USD goods index as DXY proxy; DGS10 = 10y Treasury)
3. Optional fredapi when ``FRED_API_KEY`` is set

DTWEXBGS is the free durable DXY proxy (true ICE DXY needs paid/Yahoo which 401'd).
Fail-soft: when enabled but series missing, emit stub columns filled with
``fail_soft_fill`` (default 0.0) and set status.source=missing — Replay must not crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.fred import (
    DEFAULT_LAG_DAYS,
    FredStatus,
    align_fred_asof,
    load_fred_series,
)

FEATURE_COLS = (
    "dxy_ret",
    "dxy_sma_ratio",
    "dxy_sma_slope",
    "yield_10y",
    "yield_10y_chg",
    "yield_10y_chg5",
    "eurusd_dxy_div",
)


@dataclass
class DxyYieldsStatus:
    """UI / CLI snapshot. Missing data is a status, not an exception."""

    enabled: bool
    source: str = "disabled"  # disabled | cache | fredapi | csv | missing | mixed | stub
    error: str | None = None
    dxy_series: str | None = None
    yield_series: str | None = None
    columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stubbed: bool = False


def dxy_yields_cfg(extra: dict[str, Any]) -> dict[str, Any]:
    raw = extra.get("dxy_yields")
    if raw is True:
        return {"enabled": True}
    if not raw:
        return {"enabled": False}
    if isinstance(raw, dict):
        return dict(raw)
    return {"enabled": False}


def _naive_index(idx) -> pd.DatetimeIndex:
    out = pd.DatetimeIndex(pd.to_datetime(idx))
    tz = getattr(out, "tz", None)
    if tz is not None:
        out = out.tz_convert("UTC").tz_localize(None)
    return out


def _emit_stubs(out: pd.DataFrame, fill: float) -> None:
    n = len(out)
    for col in FEATURE_COLS:
        out[col] = np.full(n, float(fill), dtype=float)


def _daily_eur_ret(df: pd.DataFrame) -> pd.Series:
    """Completed-day EURUSD close-to-close return, indexed by calendar day."""
    close = pd.to_numeric(df["Close"], errors="coerce")
    idx = _naive_index(df.index)
    daily = close.copy()
    daily.index = idx
    day_close = daily.resample("1D").last().dropna()
    # shift so day D only sees D-1→D completed return after D is done;
    # align_fred_asof will also apply lag_days — here we keep raw daily ret
    # dated on the day the return *completes* (day D close vs D-1).
    ret = day_close.pct_change(1)
    ret.name = "eur_ret"
    return ret


def _build_daily_features(
    dxy: pd.Series,
    y10: pd.Series,
    eur_ret: pd.Series,
    *,
    sma_window: int,
    slope_span: int,
) -> pd.DataFrame:
    """Causal transforms on *daily* series before hourly as-of ffill."""
    dxy = pd.to_numeric(dxy, errors="coerce").astype(float).sort_index()
    dxy = dxy[~dxy.index.duplicated(keep="last")]
    y10 = pd.to_numeric(y10, errors="coerce").astype(float).sort_index()
    y10 = y10[~y10.index.duplicated(keep="last")]

    # Union index so both series share calendar; ffill within daily only after lag.
    idx = dxy.index.union(y10.index).sort_values()
    dxy = dxy.reindex(idx)
    y10 = y10.reindex(idx)
    # Do NOT ffill across missing observation days before transforms —
    # pct_change/diff on sparse FRED days is correct; SMA uses min_periods.

    sma_window = max(2, int(sma_window))
    slope_span = max(1, int(slope_span))
    sma = dxy.rolling(sma_window, min_periods=sma_window).mean()
    dxy_ret = dxy.pct_change(1)
    block = pd.DataFrame(
        {
            "dxy_ret": dxy_ret,
            "dxy_sma_ratio": dxy / sma.replace(0, np.nan) - 1.0,
            "dxy_sma_slope": sma / sma.shift(slope_span).replace(0, np.nan) - 1.0,
            "yield_10y": y10,
            "yield_10y_chg": y10.diff(1),
            "yield_10y_chg5": y10.diff(5),
        },
        index=idx,
    )
    # EURUSD vs DXY divergence: under a normal inverse link, eur_ret + dxy_ret ~ 0.
    # Large |sum| = both legs moved the same way (divergence from inverse).
    eur = eur_ret.reindex(idx)
    block["eurusd_dxy_div"] = eur + dxy_ret
    return block


def add_dxy_yield_features(
    out: pd.DataFrame,
    df: pd.DataFrame,
    extra: dict[str, Any],
    lab_cfg: dict[str, Any] | None = None,
) -> DxyYieldsStatus | None:
    """Append DXY/yield columns. No-op when disabled. Fail-soft stubs when missing."""
    cfg = dxy_yields_cfg(extra)
    if not bool(cfg.get("enabled", False)):
        return None

    status = DxyYieldsStatus(enabled=True)
    dxy_sid = str(cfg.get("dxy_series") or "DTWEXBGS").strip().upper()
    yld_sid = str(cfg.get("yield_series") or "DGS10").strip().upper()
    status.dxy_series = dxy_sid
    status.yield_series = yld_sid
    allow_network = bool(cfg.get("allow_network", True))
    fill = float(cfg.get("fail_soft_fill", 0.0) or 0.0)
    emit_stubs = bool(cfg.get("emit_stubs_when_missing", True))

    # Reuse FRED loader/cache paths (same CSV files as feature_extras.fred).
    fred_like = {
        "cache_dir": cfg.get("cache_dir") or "data/fred_cache",
        "cache_ttl_s": cfg.get("cache_ttl_s", 86400),
        "timeout_s": cfg.get("timeout_s", 12),
        "api_key_env": cfg.get("api_key_env") or "FRED_API_KEY",
        "allow_network": allow_network,
    }

    dxy_s, dxy_src, dxy_err = load_fred_series(
        dxy_sid, fred_like, lab_cfg, allow_network=allow_network
    )
    yld_s, yld_src, yld_err = load_fred_series(
        yld_sid, fred_like, lab_cfg, allow_network=allow_network
    )
    errors = [e for e in (dxy_err, yld_err) if e]
    if errors:
        status.error = "; ".join(errors[:4])
        status.notes.extend(errors)

    if dxy_s is None or dxy_s.empty or yld_s is None or yld_s.empty:
        status.source = "missing"
        if not status.error:
            status.error = (
                f"DXY/yields unavailable ({dxy_sid}/{yld_sid}). "
                "Need local cache or FRED CSV download."
            )
        if emit_stubs:
            _emit_stubs(out, fill)
            status.stubbed = True
            status.source = "stub"
            status.columns = list(FEATURE_COLS)
            status.notes.append("emitted zero stubs; no real cross-asset edge")
        return status

    sources = {dxy_src, yld_src}
    status.source = sources.pop() if len(sources) == 1 else "mixed"

    sma_window = int(cfg.get("dxy_sma_window", 20) or 20)
    slope_span = int(cfg.get("dxy_slope_span", 5) or 5)
    lag = int(cfg.get("lag_days", DEFAULT_LAG_DAYS) or 0)

    eur_ret = _daily_eur_ret(df)
    daily = _build_daily_features(
        dxy_s, yld_s, eur_ret, sma_window=sma_window, slope_span=slope_span
    )
    aligned = align_fred_asof(daily, df.index, lag_days=lag)
    for col in FEATURE_COLS:
        if col not in aligned.columns:
            out[col] = fill
            continue
        col_s = aligned[col]
        # Keep NaN early history (warmup); model/imputer handles it. Do not
        # silently zero real missing early bars into a fake flat series.
        out[col] = col_s.to_numpy()
    status.columns = list(FEATURE_COLS)
    # Coverage note for Replay compare docs
    nn = int(pd.Series(out[FEATURE_COLS[0]]).notna().sum()) if FEATURE_COLS[0] in out.columns else 0
    status.notes.append(f"non_null_dxy_ret_bars={nn}/{len(out)}")
    return status
