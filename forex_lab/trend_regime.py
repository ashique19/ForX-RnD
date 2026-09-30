"""Trend vs chop regime gate (measured-intel strike 2).

Skip new opens when the bar is in a chop regime (weak trend). Replay-only
wiring mirrors vol_regime / news_blackout: resolve metadata at run start,
force pred->HOLD in the predict window.

Default metric is causal ADX (Wilder, normalized 0..1 via ta_pack native).
Alternate: Kaufman efficiency ratio (ER) from Close only.

Policy-only columns (trend_adx / trend_er) are attached from OHLCV in
backtest._attach_policy_columns — they are NOT added to the model feature
matrix, so the challenger is a pure entry filter.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RESOLVED_OFF = "off"
RESOLVED_SKIP_CHOP = "skip_chop"
RESOLVED_EMPTY = "empty_metric"

_MODE_OFF = frozenset({"", "off", "none", "false", "0", "null"})
_MODE_SKIP_CHOP = frozenset({"skip_chop", "chop", "skip", "trend_only", "require_trend"})
_METRIC_ADX = frozenset({"adx", "ta_adx", "trend_adx"})
_METRIC_ER = frozenset({"er", "efficiency", "efficiency_ratio", "kaufman", "trend_er"})


def trend_regime_block(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("replay") or {}).get("trend_regime")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def trend_regime_enabled(cfg: dict[str, Any] | None) -> bool:
    block = trend_regime_block(cfg)
    if not block:
        return False
    if "enabled" in block:
        return bool(block.get("enabled"))
    return normalize_trend_regime_mode(block.get("mode")) != RESOLVED_OFF


def normalize_trend_regime_mode(raw: object) -> str:
    if raw is None:
        return RESOLVED_SKIP_CHOP
    key = str(raw).strip().lower().replace(" ", "_")
    if key in _MODE_OFF:
        return RESOLVED_OFF
    if key in _MODE_SKIP_CHOP:
        return RESOLVED_SKIP_CHOP
    return RESOLVED_OFF


def normalize_trend_regime_metric(raw: object) -> str:
    if raw is None:
        return "adx"
    key = str(raw).strip().lower().replace(" ", "_")
    if key in _METRIC_ER:
        return "er"
    if key in _METRIC_ADX:
        return "adx"
    return "adx"


def metric_column(metric: str) -> str:
    return "trend_er" if metric == "er" else "trend_adx"


def trend_regime_period(cfg: dict[str, Any] | None) -> int:
    block = trend_regime_block(cfg)
    try:
        period = int(block.get("period", 14) or 14)
    except (TypeError, ValueError):
        period = 14
    return max(2, period)


def trend_regime_min_strength(cfg: dict[str, Any] | None) -> float:
    """Min ADX (0..1) or ER (0..1) to allow opens. Below = chop."""
    block = trend_regime_block(cfg)
    metric = normalize_trend_regime_metric(block.get("metric"))
    default = 0.20 if metric == "adx" else 0.25
    key = "min_adx" if metric == "adx" else "min_er"
    alt = "min_strength"
    raw = block.get(key, block.get(alt, default))
    try:
        val = float(raw if raw is not None else default)
    except (TypeError, ValueError):
        val = default
    return min(1.0, max(0.0, val))


def apply_replay_trend_regime(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve replay.trend_regime metadata. Does not rewrite default.yaml."""
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    block = trend_regime_block(cfg)
    if not trend_regime_enabled(cfg):
        rc["trend_regime_resolved"] = RESOLVED_OFF
        rc["trend_regime_metric"] = ""
        rc["trend_regime_period"] = None
        rc["trend_regime_min"] = None
        rc["trend_regime_note"] = "trend_regime disabled"
        cfg["replay"] = rc
        return cfg
    mode = normalize_trend_regime_mode(block.get("mode", "skip_chop"))
    if mode == RESOLVED_OFF:
        rc["trend_regime_resolved"] = RESOLVED_OFF
        rc["trend_regime_metric"] = ""
        rc["trend_regime_period"] = None
        rc["trend_regime_min"] = None
        rc["trend_regime_note"] = "trend_regime mode off"
        cfg["replay"] = rc
        return cfg
    metric = normalize_trend_regime_metric(block.get("metric"))
    period = trend_regime_period(cfg)
    min_s = trend_regime_min_strength(cfg)
    col = metric_column(metric)
    rc["trend_regime_resolved"] = mode
    rc["trend_regime_metric"] = metric
    rc["trend_regime_period"] = period
    rc["trend_regime_min"] = min_s
    rc["trend_regime_note"] = f"skip chop when {col}<{min_s:g} (period={period})"
    cfg["replay"] = rc
    return cfg


def _kaufman_er(close: pd.Series, period: int) -> pd.Series:
    change = (close - close.shift(period)).abs()
    path = close.diff().abs().rolling(period, min_periods=period).sum()
    return change / path.replace(0, np.nan)


def attach_trend_regime_columns(
    frame: pd.DataFrame,
    ohlcv: pd.DataFrame,
    cfg: dict[str, Any] | None,
) -> pd.DataFrame:
    """Attach trend_adx / trend_er from OHLCV for gate use (policy-only)."""
    if frame is None or frame.empty or ohlcv is None or ohlcv.empty:
        return frame
    rc = dict((cfg or {}).get("replay") or {})
    # Always compute when block present or resolved on; cheap and keeps fail-soft path ready
    if not trend_regime_enabled(cfg) and str(rc.get("trend_regime_resolved") or "") not in (
        RESOLVED_SKIP_CHOP,
    ):
        # Still attach if block exists with enabled omitted-false? Skip work when clearly off.
        block = trend_regime_block(cfg)
        if not block or block.get("enabled") is False:
            return frame
    period = int(rc.get("trend_regime_period") or trend_regime_period(cfg) or 14)
    period = max(2, period)
    out = frame.copy()
    aligned = ohlcv.reindex(out.index)
    need = {"High", "Low", "Close"}
    if not need.issubset(set(aligned.columns)):
        return out
    # ADX (normalized 0..1)
    try:
        from forex_lab.ta_pack import _native_adx

        adx_df = _native_adx(aligned[["High", "Low", "Close"]].astype(float), period)
        out["trend_adx"] = adx_df["ta_adx"].to_numpy()
    except Exception:
        out["trend_adx"] = np.nan
    # Kaufman ER
    try:
        close = aligned["Close"].astype(float)
        out["trend_er"] = _kaufman_er(close, period).to_numpy()
    except Exception:
        out["trend_er"] = np.nan
    return out


def build_trend_regime_mask(
    frame: pd.DataFrame,
    cfg: dict[str, Any] | None,
) -> pd.Series:
    """True = block new open (chop). Missing metric -> all False (fail-soft)."""
    idx = frame.index if frame is not None else pd.Index([])
    empty = pd.Series(False, index=idx, dtype=bool, name="trend_regime_block")
    if frame is None or frame.empty:
        return empty
    rc = dict((cfg or {}).get("replay") or {})
    mode = str(rc.get("trend_regime_resolved") or "")
    if mode != RESOLVED_SKIP_CHOP:
        return empty
    metric = str(rc.get("trend_regime_metric") or "adx")
    col = metric_column(metric)
    if col not in frame.columns:
        return empty
    min_s = float(rc.get("trend_regime_min") if rc.get("trend_regime_min") is not None else 0.20)
    vals = pd.to_numeric(frame[col], errors="coerce")
    # chop = weak trend; NaN fail-soft (do not block)
    block = (vals < min_s).fillna(False)
    block.name = "trend_regime_block"
    return block.astype(bool)


def apply_trend_regime_to_pred(
    pred: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    hold_code: int | None = None,
) -> pd.DataFrame:
    """Force pred->HOLD on chop bars. No-op if gate off or metric missing."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    mode = str(rc.get("trend_regime_resolved") or "")
    if mode != RESOLVED_SKIP_CHOP:
        return pred
    metric = str(rc.get("trend_regime_metric") or "adx")
    col = metric_column(metric)
    if col not in pred.columns:
        rc["trend_regime_note"] = (
            f"{rc.get('trend_regime_note') or ''} | metric column {col} missing (fail-soft)"
        ).strip(" |")
        if isinstance(cfg, dict):
            cfg["replay"] = rc
        return pred
    from forex_lab.features import LABEL_MAP

    hold = int(hold_code if hold_code is not None else LABEL_MAP["HOLD"])
    mask = build_trend_regime_mask(pred, cfg)
    out = pred.copy()
    if bool(mask.any()):
        if "pred" in out.columns:
            out.loc[mask, "pred"] = hold
        if "side" in out.columns:
            out.loc[mask, "side"] = "HOLD"
    out["trend_regime_block"] = mask.astype(float)
    return out


def trend_regime_report_line(cfg: dict[str, Any] | None) -> str:
    rc = dict((cfg or {}).get("replay") or {})
    resolved = rc.get("trend_regime_resolved") or (
        "on" if trend_regime_enabled(cfg) else "off"
    )
    metric = rc.get("trend_regime_metric") or "-"
    period = rc.get("trend_regime_period")
    min_s = rc.get("trend_regime_min")
    note = rc.get("trend_regime_note") or ""
    period_s = str(period) if period is not None else "-"
    min_s_s = f"{min_s:g}" if isinstance(min_s, (int, float)) else "-"
    return (
        f"trend_regime: {resolved} | metric={metric} | period={period_s}"
        f" | min={min_s_s} | {note}"
    )
