"""Causal multi-timeframe confirmation vs the flashed model class.

Higher-TF SMA slope is resampled from the **same** pair CSV (completed bars
only — see ``forex_lab.features._higher_tf_features``). This is a research
badge, not a live trend filter unless ``board.mtf_confirm.conflict_flash``
is set to ``hold`` or ``weaken``.

Default flash behaviour is **off** (badge only): the same HTF filter hurt
EURUSD in prior screens when used as a hard gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from forex_lab.features import _higher_tf_features, _tf_rule
from forex_lab.freshness import VALIDITY_ERROR, VALIDITY_MISSING, VALIDITY_STALE

MTF_AGREE = "agree"
MTF_CONFLICT = "conflict"
MTF_NA = "n/a"
FLASH_OFF = "off"
FLASH_WEAKEN = "weaken"
FLASH_HOLD = "hold"


@dataclass
class MtfStatus:
    status: str = MTF_NA  # agree | conflict | n/a
    timeframe: str = ""
    slope: float | None = None
    direction: str = "n/a"  # up | down | flat | n/a
    signal: str = ""
    note: str = "n/a"

    def as_label(self) -> str:
        if self.status == MTF_AGREE:
            return f"MTF agree ({self.timeframe})"
        if self.status == MTF_CONFLICT:
            return f"MTF conflict ({self.timeframe})"
        return "MTF n/a"


def _mtf_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    board = dict((cfg or {}).get("board") or {})
    block = dict(board.get("mtf_confirm") or {})
    if not block:
        # allow a top-level alias
        block = dict((cfg or {}).get("mtf_confirm") or {})
    return block


def mtf_enabled(cfg: dict[str, Any] | None) -> bool:
    block = _mtf_cfg(cfg)
    if "enabled" in block:
        return bool(block.get("enabled"))
    return True


def confirm_timeframes(cfg: dict[str, Any] | None) -> list[str]:
    block = _mtf_cfg(cfg)
    tfs = block.get("timeframes")
    if tfs:
        return [str(x) for x in tfs]
    extras = dict((cfg or {}).get("feature_extras") or {})
    htf = extras.get("higher_tf") or ["4h"]
    return [str(x) for x in htf] or ["4h"]


def conflict_flash_mode(cfg: dict[str, Any] | None) -> str:
    raw = str(_mtf_cfg(cfg).get("conflict_flash") or FLASH_OFF).strip().lower()
    if raw in {FLASH_HOLD, "force_hold", "hold"}:
        return FLASH_HOLD
    if raw in {FLASH_WEAKEN, "weaker", "weak"}:
        return FLASH_WEAKEN
    return FLASH_OFF


def last_htf_slope(
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any] | None,
    timeframe: str,
) -> float | None:
    """Last causal HTF SMA slope. None if not computable."""
    if ohlcv is None or ohlcv.empty or "Close" not in getattr(ohlcv, "columns", []):
        return None
    parsed = _tf_rule(timeframe)
    if parsed is None:
        return None
    tag, _rule = parsed
    extras = dict((cfg or {}).get("feature_extras") or {})
    probe = dict(cfg or {})
    probe["feature_extras"] = {
        **extras,
        "higher_tf": [timeframe],
        "htf_sma_window": int(extras.get("htf_sma_window", 20) or 20),
        "htf_slope_span": int(extras.get("htf_slope_span", 3) or 3),
    }
    block = _higher_tf_features(ohlcv, probe)
    col = f"tf_{tag}_sma_slope"
    if col not in block.columns:
        return None
    series = pd.to_numeric(block[col], errors="coerce").dropna()
    if series.empty:
        return None
    val = float(series.iloc[-1])
    if not pd.notna(val):
        return None
    return val


def _direction(slope: float | None, *, flat_eps: float) -> str:
    if slope is None or not pd.notna(slope):
        return "n/a"
    if abs(float(slope)) < flat_eps:
        return "flat"
    return "up" if float(slope) > 0 else "down"


def assess_mtf(
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any] | None,
    signal: str | None,
    *,
    validity: str = "OK",
) -> MtfStatus:
    """Compare the (raw) model class to HTF SMA slope. Causal only."""
    if not mtf_enabled(cfg):
        return MtfStatus(note="MTF badge disabled in config.")
    sig = str(signal or "").upper()
    if validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        return MtfStatus(signal=sig, note="n/a — no live bar for HTF slope")
    tfs = confirm_timeframes(cfg)
    if not tfs:
        return MtfStatus(signal=sig, note="n/a — no higher TF configured")
    tf = tfs[0]
    extras = dict((cfg or {}).get("feature_extras") or {})
    flat_eps = float(_mtf_cfg(cfg).get("flat_eps") or extras.get("htf_flat_eps") or 1e-8)
    slope = last_htf_slope(ohlcv, cfg, tf)
    direction = _direction(slope, flat_eps=flat_eps)
    parsed = _tf_rule(tf)
    tag = parsed[0] if parsed else tf
    if slope is None:
        return MtfStatus(
            status=MTF_NA,
            timeframe=tag,
            signal=sig,
            note=f"n/a — {tag} SMA slope not computable on this cache",
        )
    if sig in {"", "—", "-", "N/A", "HOLD"}:
        return MtfStatus(
            status=MTF_NA,
            timeframe=tag,
            slope=slope,
            direction=direction,
            signal=sig or "HOLD",
            note=f"{tag} slope {direction} unused (no directional flash)",
        )
    if direction == "flat":
        return MtfStatus(
            status=MTF_NA,
            timeframe=tag,
            slope=slope,
            direction=direction,
            signal=sig,
            note=f"{tag} SMA slope is flat — no confirm/conflict",
        )
    agree = (sig == "BUY" and direction == "up") or (sig == "SELL" and direction == "down")
    status = MTF_AGREE if agree else MTF_CONFLICT
    want = "up" if sig == "BUY" else "down"
    note = (
        f"{tag} SMA slope {direction} matches {sig}"
        if agree
        else f"{tag} SMA slope {direction} vs {sig} (wants {want})"
    )
    return MtfStatus(
        status=status,
        timeframe=tag,
        slope=slope,
        direction=direction,
        signal=sig,
        note=note,
    )
