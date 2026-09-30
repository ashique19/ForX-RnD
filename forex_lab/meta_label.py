"""Meta-label / secondary filter (Replay policy gate).

Vetoes primary BUY/SELL -> HOLD when a pre-registered secondary rule fires.
Does not change the Buy/Sell model. Default OFF = bit-identical to pin path.

Config under `replay.meta_label`:

    meta_label:
      enabled: false
      mode: rule
      rule: quiet_weak | asia_quiet | conf_atr | asia_loud_weak
      atr_max: 0.15          # quiet_weak / asia_quiet
      atr_min: 0.60          # asia_loud_weak (HIGH atr)
      conf_max: 0.65         # quiet_weak / asia_loud_weak
      product_max: 0.10      # conf_atr: confidence * atr_pctile < product_max
      atr_window: 100        # causal atr_pctile when feature_extras.vol_percentile_window=0

Fail-soft when required columns missing on pred (after attach attempt).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RESOLVED_OFF = "off"
RESOLVED_RULE = "rule"

_MODE_OFF = frozenset({"", "off", "none", "false", "0", "null"})
_MODE_RULE = frozenset({"rule", "quiet_weak", "asia_quiet", "conf_atr", "asia_loud_weak", "filter", "on", "true", "1"})

_RULE_QUIET_WEAK = "quiet_weak"
_RULE_ASIA_QUIET = "asia_quiet"
_RULE_CONF_ATR = "conf_atr"
_RULE_ASIA_LOUD_WEAK = "asia_loud_weak"
_KNOWN_RULES = frozenset({_RULE_QUIET_WEAK, _RULE_ASIA_QUIET, _RULE_CONF_ATR, _RULE_ASIA_LOUD_WEAK})


def meta_label_block(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("replay") or {}).get("meta_label")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def meta_label_enabled(cfg: dict[str, Any] | None) -> bool:
    block = meta_label_block(cfg)
    if not block:
        return False
    if "enabled" in block:
        return bool(block.get("enabled"))
    return normalize_meta_label_mode(block.get("mode")) != RESOLVED_OFF


def normalize_meta_label_mode(raw: object) -> str:
    if raw is None:
        return RESOLVED_RULE
    s = str(raw).strip().lower()
    if s in _MODE_OFF:
        return RESOLVED_OFF
    if s in _MODE_RULE:
        return RESOLVED_RULE
    return RESOLVED_OFF


def normalize_meta_label_rule(raw: object) -> str:
    s = str(raw or _RULE_QUIET_WEAK).strip().lower() or _RULE_QUIET_WEAK
    if s in _KNOWN_RULES:
        return s
    return _RULE_QUIET_WEAK


def apply_replay_meta_label(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve replay.meta_label metadata. Does not rewrite default.yaml."""
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    block = meta_label_block(cfg)
    if not meta_label_enabled(cfg):
        rc["meta_label_resolved"] = RESOLVED_OFF
        rc["meta_label_rule"] = ""
        rc["meta_label_atr_max"] = None
        rc["meta_label_atr_min"] = None
        rc["meta_label_conf_max"] = None
        rc["meta_label_product_max"] = None
        rc["meta_label_atr_window"] = None
        rc["meta_label_note"] = "meta_label disabled"
        cfg["replay"] = rc
        return cfg
    mode = normalize_meta_label_mode(block.get("mode", "rule"))
    if mode == RESOLVED_OFF:
        rc["meta_label_resolved"] = RESOLVED_OFF
        rc["meta_label_rule"] = ""
        rc["meta_label_atr_max"] = None
        rc["meta_label_atr_min"] = None
        rc["meta_label_conf_max"] = None
        rc["meta_label_product_max"] = None
        rc["meta_label_atr_window"] = None
        rc["meta_label_note"] = "meta_label mode off"
        cfg["replay"] = rc
        return cfg
    rule = normalize_meta_label_rule(block.get("rule"))
    atr_max = float(block.get("atr_max") if block.get("atr_max") is not None else 0.15)
    atr_min = float(block.get("atr_min") if block.get("atr_min") is not None else 0.60)
    conf_max = float(block.get("conf_max") if block.get("conf_max") is not None else 0.65)
    product_max = float(
        block.get("product_max") if block.get("product_max") is not None else 0.10
    )
    atr_window = int(block.get("atr_window") if block.get("atr_window") is not None else 100)
    rc["meta_label_resolved"] = RESOLVED_RULE
    rc["meta_label_rule"] = rule
    rc["meta_label_atr_max"] = atr_max
    rc["meta_label_atr_min"] = atr_min
    rc["meta_label_conf_max"] = conf_max
    rc["meta_label_product_max"] = product_max
    rc["meta_label_atr_window"] = atr_window
    if rule == _RULE_ASIA_QUIET:
        note = (
            f"HOLD when Asia-only AND atr_pctile<={atr_max:g} "
            f"(rule={rule}; attach atr_window={atr_window})"
        )
    elif rule == _RULE_ASIA_LOUD_WEAK:
        note = (
            f"HOLD when Asia-only AND atr_pctile>={atr_min:g} AND confidence<{conf_max:g} "
            f"(rule={rule}; attach atr_window={atr_window})"
        )
    elif rule == _RULE_CONF_ATR:
        note = (
            f"HOLD when confidence*atr_pctile<{product_max:g} "
            f"(rule={rule}; attach atr_window={atr_window})"
        )
    else:
        note = (
            f"HOLD when atr_pctile<={atr_max:g} AND confidence<{conf_max:g} "
            f"(rule={rule}; attach atr_window={atr_window})"
        )
    rc["meta_label_note"] = note
    cfg["replay"] = rc
    return cfg


def _atr_pctile_window(cfg: dict[str, Any] | None) -> int:
    rc = dict((cfg or {}).get("replay") or {})
    if rc.get("meta_label_atr_window") is not None:
        return max(20, int(rc["meta_label_atr_window"]))
    block = meta_label_block(cfg)
    if block.get("atr_window") is not None:
        return max(20, int(block["atr_window"]))
    vp = int((dict((cfg or {}).get("feature_extras") or {}).get("vol_percentile_window") or 0))
    return max(20, vp if vp > 0 else 100)


def attach_atr_pctile_for_meta(
    pred: pd.DataFrame,
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any] | None,
) -> pd.DataFrame:
    """Ensure causal atr_pctile on pred for meta-label without changing model features.

    feature_extras.vol_percentile_window is 0 on the live pin path, so atr_pctile is
    absent from X. Policy-only attach from full OHLCV ATR% rolling min-max.
    """
    if pred is None or pred.empty:
        return pred
    if "atr_pctile" in pred.columns and pred["atr_pctile"].notna().any():
        return pred
    if ohlcv is None or ohlcv.empty or "Close" not in ohlcv.columns:
        return pred
    from forex_lab.features import true_range_atr

    window = _atr_pctile_window(cfg)
    atr_period = int((cfg or {}).get("atr_period") or 14)
    atr = true_range_atr(ohlcv, atr_period)
    close = ohlcv["Close"].astype(float).replace(0, np.nan)
    atr_pct = atr.astype(float) / close
    amin = atr_pct.rolling(window, min_periods=max(5, window // 5)).min()
    amax = atr_pct.rolling(window, min_periods=max(5, window // 5)).max()
    pctile = (atr_pct - amin) / (amax - amin).replace(0, np.nan)
    out = pred.copy()
    out["atr_pctile"] = pctile.reindex(out.index).astype(float).to_numpy()
    return out


def _asia_only_mask(frame: pd.DataFrame) -> pd.Series:
    """True when Asia session is active and London/NY are not (overlap excluded)."""
    idx = frame.index
    if {"sess_asia", "sess_london", "sess_ny"}.issubset(frame.columns):
        asia = pd.to_numeric(frame["sess_asia"], errors="coerce").fillna(0.0)
        ldn = pd.to_numeric(frame["sess_london"], errors="coerce").fillna(0.0)
        ny = pd.to_numeric(frame["sess_ny"], errors="coerce").fillna(0.0)
        return (asia > 0.5) & (ldn < 0.5) & (ny < 0.5)
    # Fallback: UTC hour windows matching forex_lab.session defaults
    try:
        hours = pd.DatetimeIndex(pd.to_datetime(idx, utc=True)).hour + (
            pd.DatetimeIndex(pd.to_datetime(idx, utc=True)).minute / 60.0
        )
    except Exception:
        return pd.Series(False, index=idx, dtype=bool)
    # Asia 21-07 UTC wrap; London 7-16; NY 13-21
    in_asia = (hours >= 21.0) | (hours < 7.0)
    in_london = (hours >= 7.0) & (hours < 16.0)
    in_ny = (hours >= 13.0) & (hours < 21.0)
    return pd.Series(in_asia & ~in_london & ~in_ny, index=idx, dtype=bool)


def build_meta_label_mask(
    frame: pd.DataFrame,
    cfg: dict[str, Any] | None,
) -> pd.Series:
    """True = veto primary signal (force HOLD). Fail-soft -> all False."""
    idx = frame.index if frame is not None else pd.Index([])
    empty = pd.Series(False, index=idx, dtype=bool, name="meta_label_block")
    if frame is None or frame.empty:
        return empty
    rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("meta_label_resolved") or "") != RESOLVED_RULE:
        return empty
    rule = normalize_meta_label_rule(rc.get("meta_label_rule") or _RULE_QUIET_WEAK)
    atr_max = float(rc.get("meta_label_atr_max") if rc.get("meta_label_atr_max") is not None else 0.15)
    conf_max = float(rc.get("meta_label_conf_max") if rc.get("meta_label_conf_max") is not None else 0.65)
    product_max = float(
        rc.get("meta_label_product_max") if rc.get("meta_label_product_max") is not None else 0.10
    )

    if rule == _RULE_ASIA_QUIET:
        if "atr_pctile" not in frame.columns:
            return empty
        atr = pd.to_numeric(frame["atr_pctile"], errors="coerce")
        asia = _asia_only_mask(frame)
        block = asia & (atr <= atr_max)
        block = block.fillna(False)
        block.name = "meta_label_block"
        return block.astype(bool)

    if rule == _RULE_ASIA_LOUD_WEAK:
        if "atr_pctile" not in frame.columns or "confidence" not in frame.columns:
            return empty
        atr_min = float(
            rc.get("meta_label_atr_min") if rc.get("meta_label_atr_min") is not None else 0.60
        )
        atr = pd.to_numeric(frame["atr_pctile"], errors="coerce")
        conf = pd.to_numeric(frame["confidence"], errors="coerce")
        asia = _asia_only_mask(frame)
        block = asia & (atr >= atr_min) & (conf < conf_max)
        block = block.fillna(False)
        block.name = "meta_label_block"
        return block.astype(bool)

    if rule == _RULE_CONF_ATR:
        if "atr_pctile" not in frame.columns or "confidence" not in frame.columns:
            return empty
        atr = pd.to_numeric(frame["atr_pctile"], errors="coerce")
        conf = pd.to_numeric(frame["confidence"], errors="coerce")
        block = (conf * atr) < product_max
        block = block.fillna(False)
        block.name = "meta_label_block"
        return block.astype(bool)

    # quiet_weak default
    if "atr_pctile" not in frame.columns or "confidence" not in frame.columns:
        return empty
    atr = pd.to_numeric(frame["atr_pctile"], errors="coerce")
    conf = pd.to_numeric(frame["confidence"], errors="coerce")
    block = (atr <= atr_max) & (conf < conf_max)
    block = block.fillna(False)
    block.name = "meta_label_block"
    return block.astype(bool)


def apply_meta_label_to_pred(
    pred: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    hold_code: int | None = None,
    ohlcv: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Force pred->HOLD when meta-label rule fires. No-op if disabled."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("meta_label_resolved") or "") != RESOLVED_RULE:
        return pred
    # Policy-only atr_pctile attach (model features stay pin-compatible).
    pred = attach_atr_pctile_for_meta(pred, ohlcv, cfg)
    rule = normalize_meta_label_rule(rc.get("meta_label_rule") or _RULE_QUIET_WEAK)
    need_atr = True
    need_conf = rule in {_RULE_QUIET_WEAK, _RULE_CONF_ATR, _RULE_ASIA_LOUD_WEAK}
    if need_atr and "atr_pctile" not in pred.columns:
        rc["meta_label_note"] = (
            f"{rc.get('meta_label_note') or ''} | atr_pctile missing (fail-soft)"
        ).strip(" |")
        if isinstance(cfg, dict):
            cfg["replay"] = rc
        return pred
    if need_conf and "confidence" not in pred.columns:
        rc["meta_label_note"] = (
            f"{rc.get('meta_label_note') or ''} | confidence missing (fail-soft)"
        ).strip(" |")
        if isinstance(cfg, dict):
            cfg["replay"] = rc
        return pred
    from forex_lab.features import LABEL_MAP

    hold = int(hold_code if hold_code is not None else LABEL_MAP["HOLD"])
    mask = build_meta_label_mask(pred, cfg)
    out = pred.copy()
    if bool(mask.any()):
        if "pred" in out.columns:
            out.loc[mask, "pred"] = hold
        if "side" in out.columns:
            out.loc[mask, "side"] = "HOLD"
    out["meta_label_block"] = mask.astype(float)
    return out


def meta_label_report_line(cfg: dict[str, Any] | None) -> str:
    rc = dict((cfg or {}).get("replay") or {})
    resolved = rc.get("meta_label_resolved") or (
        "on" if meta_label_enabled(cfg) else "off"
    )
    note = rc.get("meta_label_note") or ""
    return f"meta_label: {resolved} | {note}".strip(" |")
