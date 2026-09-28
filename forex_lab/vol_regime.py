"""ATR/vol regime gate (SERIAL step 7).

Skip new opens when realized vol/ATR is in an extreme percentile band
(and optionally when dead-quiet). Replay-only wiring mirrors news_blackout:
resolve metadata at run start, force pred->HOLD in the predict window.

Prefer `mode: skip_extremes` for clean before/after (no continuous sizing).
Metric defaults to causal `atr_pctile` (rolling min-max of atr_pct); `vol_pct`
is the existing short-vol percentile. Fail-soft when the metric column is missing.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

RESOLVED_OFF = "off"
RESOLVED_SKIP_HIGH = "skip_high"
RESOLVED_SKIP_EXTREMES = "skip_extremes"
RESOLVED_EMPTY = "empty_metric"

_MODE_OFF = frozenset({"", "off", "none", "false", "0", "null"})
_MODE_SKIP_HIGH = frozenset({"skip_high", "high", "extreme_high", "high_only"})
_MODE_SKIP_EXTREMES = frozenset(
    {"skip_extremes", "extremes", "skip", "both", "high_low", "band"}
)
_METRIC_ATR = frozenset({"atr_pctile", "atr", "atr_pct", "atr%ile", "atr_percentile"})
_METRIC_VOL = frozenset({"vol_pct", "vol", "vol_percentile", "volatility"})


def vol_regime_block(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("replay") or {}).get("vol_regime")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def vol_regime_enabled(cfg: dict[str, Any] | None) -> bool:
    block = vol_regime_block(cfg)
    if not block:
        return False
    if "enabled" in block:
        return bool(block.get("enabled"))
    # enabled omitted: treat presence of a non-off mode as on
    return normalize_vol_regime_mode(block.get("mode")) != RESOLVED_OFF


def normalize_vol_regime_mode(raw: object) -> str:
    if raw is None:
        return RESOLVED_SKIP_EXTREMES  # default when block is enabled
    key = str(raw).strip().lower().replace(" ", "_")
    if key in _MODE_OFF:
        return RESOLVED_OFF
    if key in _MODE_SKIP_HIGH:
        return RESOLVED_SKIP_HIGH
    if key in _MODE_SKIP_EXTREMES:
        return RESOLVED_SKIP_EXTREMES
    return RESOLVED_OFF


def normalize_vol_regime_metric(raw: object) -> str:
    if raw is None:
        return "atr_pctile"
    key = str(raw).strip().lower().replace(" ", "_")
    if key in _METRIC_VOL:
        return "vol_pct"
    if key in _METRIC_ATR:
        return "atr_pctile"
    return "atr_pctile"


def vol_regime_thresholds(cfg: dict[str, Any] | None) -> tuple[float, float]:
    """Return (low, high) in [0,1]. low=0 means do not skip quiet."""
    block = vol_regime_block(cfg)
    try:
        high = float(block.get("high", block.get("high_pctile", 0.90)) or 0.90)
    except (TypeError, ValueError):
        high = 0.90
    try:
        low = float(block.get("low", block.get("low_pctile", 0.10)) or 0.0)
    except (TypeError, ValueError):
        low = 0.10
    high = min(1.0, max(0.0, high))
    low = min(1.0, max(0.0, low))
    if low >= high:
        # degenerate band — keep high skip only
        low = 0.0
    return low, high


def metric_column(metric: str) -> str:
    return "vol_pct" if metric == "vol_pct" else "atr_pctile"


def apply_replay_vol_regime(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve replay.vol_regime metadata. Does not rewrite default.yaml."""
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    block = vol_regime_block(cfg)
    if not vol_regime_enabled(cfg):
        rc["vol_regime_resolved"] = RESOLVED_OFF
        rc["vol_regime_metric"] = ""
        rc["vol_regime_high"] = None
        rc["vol_regime_low"] = None
        rc["vol_regime_note"] = "vol_regime disabled"
        cfg["replay"] = rc
        return cfg
    mode = normalize_vol_regime_mode(block.get("mode", "skip_extremes"))
    if mode == RESOLVED_OFF:
        rc["vol_regime_resolved"] = RESOLVED_OFF
        rc["vol_regime_metric"] = ""
        rc["vol_regime_high"] = None
        rc["vol_regime_low"] = None
        rc["vol_regime_note"] = "vol_regime mode off"
        cfg["replay"] = rc
        return cfg
    metric = normalize_vol_regime_metric(block.get("metric"))
    low, high = vol_regime_thresholds(cfg)
    if mode == RESOLVED_SKIP_HIGH:
        low = 0.0
    rc["vol_regime_resolved"] = mode
    rc["vol_regime_metric"] = metric
    rc["vol_regime_high"] = high
    rc["vol_regime_low"] = low
    col = metric_column(metric)
    if mode == RESOLVED_SKIP_HIGH:
        note = f"skip when {col}>={high:g} (extreme vol)"
    else:
        note = f"skip when {col}>={high:g} or {col}<={low:g} (extreme/quiet)"
    rc["vol_regime_note"] = note
    cfg["replay"] = rc
    return cfg


def build_vol_regime_mask(
    frame: pd.DataFrame,
    cfg: dict[str, Any] | None,
) -> pd.Series:
    """True = block new open (extreme/quiet). Empty/missing metric -> all False."""
    idx = frame.index if frame is not None else pd.Index([])
    empty = pd.Series(False, index=idx, dtype=bool, name="vol_regime_block")
    if frame is None or frame.empty:
        return empty
    rc = dict((cfg or {}).get("replay") or {})
    mode = str(rc.get("vol_regime_resolved") or "")
    if mode not in (RESOLVED_SKIP_HIGH, RESOLVED_SKIP_EXTREMES):
        return empty
    metric = str(rc.get("vol_regime_metric") or "atr_pctile")
    col = metric_column(metric)
    if col not in frame.columns:
        return empty
    low = float(rc.get("vol_regime_low") if rc.get("vol_regime_low") is not None else 0.0)
    high = float(rc.get("vol_regime_high") if rc.get("vol_regime_high") is not None else 0.90)
    vals = pd.to_numeric(frame[col], errors="coerce")
    high_hit = vals >= high
    if mode == RESOLVED_SKIP_HIGH or low <= 0:
        block = high_hit.fillna(False)
    else:
        block = (high_hit | (vals <= low)).fillna(False)
    block.name = "vol_regime_block"
    return block.astype(bool)


def apply_vol_regime_to_pred(
    pred: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    hold_code: int | None = None,
) -> pd.DataFrame:
    """Force pred->HOLD on extreme/quiet bars. No-op if gate off or metric missing."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    mode = str(rc.get("vol_regime_resolved") or "")
    if mode not in (RESOLVED_SKIP_HIGH, RESOLVED_SKIP_EXTREMES):
        return pred
    metric = str(rc.get("vol_regime_metric") or "atr_pctile")
    col = metric_column(metric)
    if col not in pred.columns:
        # fail-soft: leave predictions unchanged, stamp note
        rc["vol_regime_note"] = (
            f"{rc.get('vol_regime_note') or ''} | metric column {col} missing (fail-soft)"
        ).strip(" |")
        if isinstance(cfg, dict):
            cfg["replay"] = rc
        return pred
    from forex_lab.features import LABEL_MAP

    hold = int(hold_code if hold_code is not None else LABEL_MAP["HOLD"])
    mask = build_vol_regime_mask(pred, cfg)
    if not bool(mask.any()):
        out = pred.copy()
        out["vol_regime_block"] = mask.astype(float)
        return out
    out = pred.copy()
    if "pred" in out.columns:
        out.loc[mask, "pred"] = hold
    if "side" in out.columns:
        out.loc[mask, "side"] = "HOLD"
    out["vol_regime_block"] = mask.astype(float)
    return out


def vol_regime_report_line(cfg: dict[str, Any] | None) -> str:
    rc = dict((cfg or {}).get("replay") or {})
    resolved = rc.get("vol_regime_resolved") or (
        "on" if vol_regime_enabled(cfg) else "off"
    )
    metric = rc.get("vol_regime_metric") or "-"
    high = rc.get("vol_regime_high")
    low = rc.get("vol_regime_low")
    note = rc.get("vol_regime_note") or ""
    high_s = f"{high:g}" if isinstance(high, (int, float)) else "-"
    low_s = f"{low:g}" if isinstance(low, (int, float)) else "-"
    return (
        f"vol_regime: {resolved} | metric={metric} | low/high={low_s}/{high_s}"
        f" | {note}"
    )
