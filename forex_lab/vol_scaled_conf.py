"""Volatility-scaled confidence threshold (NEW family).

Raises/lowers the effective min_confidence by a clipped vol multiplier.
Distinct from size_by_conf (qty scaler) and vol_regime (skip extremes).

    effective_min_conf = base_min_conf * clip(metric / vol_ref, lo, hi)

Config under ``signals.vol_scaled_conf`` (default OFF = bit-identical):

    vol_scaled_conf:
      enabled: false
      metric: atr_pctile   # atr_pctile | vol_regime
      vol_ref: 0.5         # atr_pctile ~0.5 median -> multiplier ~1.0
      lo: 0.90
      hi: 1.15
      atr_window: 100      # policy-only atr_pctile when feature_extras.vol_percentile_window=0

With base 0.60 and clip [0.90, 1.15] -> effective ~0.54..0.69.
Fail-soft: missing metric -> static min_confidence (no change).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RESOLVED_OFF = "off"
RESOLVED_ON = "on"


def vol_scaled_conf_block(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("signals") or {}).get("vol_scaled_conf")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def vol_scaled_conf_enabled(cfg: dict[str, Any] | None) -> bool:
    block = vol_scaled_conf_block(cfg)
    if not block:
        return False
    return bool(block.get("enabled"))


def apply_replay_vol_scaled_conf(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve signals.vol_scaled_conf metadata onto cfg[\"replay\"] notes. No yaml rewrite."""
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    block = vol_scaled_conf_block(cfg)
    if not vol_scaled_conf_enabled(cfg):
        rc["vol_scaled_conf_resolved"] = RESOLVED_OFF
        rc["vol_scaled_conf_note"] = "vol_scaled_conf disabled"
        cfg["replay"] = rc
        return cfg
    metric = str(block.get("metric") or "atr_pctile").strip().lower()
    if metric not in ("atr_pctile", "vol_regime", "vol_pct"):
        metric = "atr_pctile"
    try:
        vol_ref = float(block.get("vol_ref") if block.get("vol_ref") is not None else 0.5)
    except (TypeError, ValueError):
        vol_ref = 0.5
    if vol_ref <= 0:
        vol_ref = 0.5
    try:
        lo = float(block.get("lo") if block.get("lo") is not None else 0.90)
    except (TypeError, ValueError):
        lo = 0.90
    try:
        hi = float(block.get("hi") if block.get("hi") is not None else 1.15)
    except (TypeError, ValueError):
        hi = 1.15
    if lo > hi:
        lo, hi = hi, lo
    try:
        atr_window = int(block.get("atr_window") if block.get("atr_window") is not None else 100)
    except (TypeError, ValueError):
        atr_window = 100
    atr_window = max(20, atr_window)
    base = float((cfg.get("signals") or {}).get("min_confidence") or 0.60)
    rc["vol_scaled_conf_resolved"] = RESOLVED_ON
    rc["vol_scaled_conf_metric"] = metric
    rc["vol_scaled_conf_vol_ref"] = vol_ref
    rc["vol_scaled_conf_lo"] = lo
    rc["vol_scaled_conf_hi"] = hi
    rc["vol_scaled_conf_atr_window"] = atr_window
    rc["vol_scaled_conf_base"] = base
    rc["vol_scaled_conf_note"] = (
        f"effective_min_conf={base:g}*clip({metric}/{vol_ref:g},{lo:g},{hi:g}) "
        f"-> ~{base * lo:.3f}..{base * hi:.3f}"
    )
    cfg["replay"] = rc
    return cfg


def attach_metric_for_vol_scaled(
    pred: pd.DataFrame,
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any] | None,
) -> pd.DataFrame:
    """Ensure atr_pctile on pred for vol_scaled_conf (policy-only; no model feature change)."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    metric = str(rc.get("vol_scaled_conf_metric") or "atr_pctile")
    if metric == "vol_regime" and "vol_regime" in pred.columns:
        return pred
    if metric == "vol_pct" and "vol_pct" in pred.columns:
        return pred
    if "atr_pctile" in pred.columns and pred["atr_pctile"].notna().any():
        return pred
    # Reuse meta-label attach (same causal ATR% rolling min-max).
    from forex_lab.meta_label import attach_atr_pctile_for_meta

    # Temporarily stamp atr_window so meta attach uses our window.
    block = vol_scaled_conf_block(cfg)
    window = int(rc.get("vol_scaled_conf_atr_window") or block.get("atr_window") or 100)
    cfg2 = dict(cfg or {})
    rc2 = dict(cfg2.get("replay") or {})
    rc2["meta_label_atr_window"] = max(20, window)
    cfg2["replay"] = rc2
    return attach_atr_pctile_for_meta(pred, ohlcv, cfg2)


def effective_min_confidence_series(
    pred_frame: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    base_min_conf: float | None = None,
) -> pd.Series | None:
    """Per-row effective min_confidence, or None when disabled / fail-soft."""
    if not vol_scaled_conf_enabled(cfg):
        return None
    rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("vol_scaled_conf_resolved") or "") == RESOLVED_OFF:
        # Runner may enable without calling apply_replay_*; resolve on the fly.
        apply_replay_vol_scaled_conf(cfg)  # type: ignore[arg-type]
        rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("vol_scaled_conf_resolved") or "") != RESOLVED_ON:
        apply_replay_vol_scaled_conf(cfg)  # type: ignore[arg-type]
        rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("vol_scaled_conf_resolved") or "") != RESOLVED_ON:
        return None
    metric = str(rc.get("vol_scaled_conf_metric") or "atr_pctile")
    col = metric if metric in pred_frame.columns else (
        "atr_pctile" if "atr_pctile" in pred_frame.columns else None
    )
    if col is None:
        return None
    vol_ref = float(rc.get("vol_scaled_conf_vol_ref") or 0.5)
    lo = float(rc.get("vol_scaled_conf_lo") if rc.get("vol_scaled_conf_lo") is not None else 0.90)
    hi = float(rc.get("vol_scaled_conf_hi") if rc.get("vol_scaled_conf_hi") is not None else 1.15)
    if base_min_conf is None:
        base_min_conf = float(rc.get("vol_scaled_conf_base") or (
            (cfg or {}).get("signals") or {}
        ).get("min_confidence") or 0.60)
    vals = pd.to_numeric(pred_frame[col], errors="coerce")
    ratio = vals / vol_ref
    mult = ratio.clip(lower=lo, upper=hi)
    # NaN metric -> multiplier 1.0 (static base)
    mult = mult.fillna(1.0)
    out = (float(base_min_conf) * mult).astype(float)
    out.name = "effective_min_conf"
    return out


def vol_scaled_conf_report_line(cfg: dict[str, Any] | None) -> str:
    rc = dict((cfg or {}).get("replay") or {})
    resolved = rc.get("vol_scaled_conf_resolved") or (
        "on" if vol_scaled_conf_enabled(cfg) else "off"
    )
    note = rc.get("vol_scaled_conf_note") or ""
    return f"vol_scaled_conf: {resolved} | {note}"
