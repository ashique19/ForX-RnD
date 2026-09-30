"""Meta-label / secondary filter (Replay policy gate).

Vetoes primary BUY/SELL -> HOLD when a pre-registered secondary rule fires.
Does not change the Buy/Sell model. Default OFF = bit-identical to pin path.

Config under `replay.meta_label`:

    meta_label:
      enabled: false
      mode: rule              # rule | off
      rule: quiet_weak        # atr_pctile <= atr_max AND confidence < conf_max
      atr_max: 0.15
      conf_max: 0.65

Fail-soft when atr_pctile/confidence missing on pred.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

RESOLVED_OFF = "off"
RESOLVED_RULE = "rule"

_MODE_OFF = frozenset({"", "off", "none", "false", "0", "null"})
_MODE_RULE = frozenset({"rule", "quiet_weak", "filter", "on", "true", "1"})


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
        rc["meta_label_conf_max"] = None
        rc["meta_label_note"] = "meta_label disabled"
        cfg["replay"] = rc
        return cfg
    mode = normalize_meta_label_mode(block.get("mode", "rule"))
    if mode == RESOLVED_OFF:
        rc["meta_label_resolved"] = RESOLVED_OFF
        rc["meta_label_rule"] = ""
        rc["meta_label_atr_max"] = None
        rc["meta_label_conf_max"] = None
        rc["meta_label_note"] = "meta_label mode off"
        cfg["replay"] = rc
        return cfg
    rule = str(block.get("rule") or "quiet_weak").strip().lower() or "quiet_weak"
    atr_max = float(block.get("atr_max") if block.get("atr_max") is not None else 0.15)
    conf_max = float(block.get("conf_max") if block.get("conf_max") is not None else 0.65)
    rc["meta_label_resolved"] = RESOLVED_RULE
    rc["meta_label_rule"] = rule
    rc["meta_label_atr_max"] = atr_max
    rc["meta_label_conf_max"] = conf_max
    rc["meta_label_note"] = (
        f"HOLD when atr_pctile<={atr_max:g} AND confidence<{conf_max:g} (rule={rule})"
    )
    cfg["replay"] = rc
    return cfg


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
    if "atr_pctile" not in frame.columns or "confidence" not in frame.columns:
        return empty
    atr_max = float(rc.get("meta_label_atr_max") if rc.get("meta_label_atr_max") is not None else 0.15)
    conf_max = float(rc.get("meta_label_conf_max") if rc.get("meta_label_conf_max") is not None else 0.65)
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
) -> pd.DataFrame:
    """Force pred->HOLD when meta-label rule fires. No-op if disabled."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("meta_label_resolved") or "") != RESOLVED_RULE:
        return pred
    if "atr_pctile" not in pred.columns or "confidence" not in pred.columns:
        rc["meta_label_note"] = (
            f"{rc.get('meta_label_note') or ''} | atr_pctile/confidence missing (fail-soft)"
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
