"""Weekday open gate (Mon/Thu paper-driven knob).

Blocks new opens on configured UTC weekdays. Replay bar timestamps and
paper entry_time are UTC; Asia/Dhaka matches the same calendar day for the
overlap session (13-16 UTC = 19-22 Dhaka), so UTC is the documented tz.

Config under `replay.weekday_gate` (mirrors vol_regime / session_gate):

    weekday_gate:
      enabled: false
      block: [mon, thu]   # names or 0=Mon..6=Sun
      tz: UTC             # only UTC supported (bar index tz)

Fail-soft: missing/naive index -> no block. Live desk can reuse the same
resolved block list via `weekday_gate_blocks_now`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

RESOLVED_OFF = "off"
RESOLVED_ON = "on"

_DAY_ALIASES: dict[str, int] = {
    "mon": 0, "monday": 0, "0": 0,
    "tue": 1, "tues": 1, "tuesday": 1, "1": 1,
    "wed": 2, "wednesday": 2, "2": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "3": 3,
    "fri": 4, "friday": 4, "4": 4,
    "sat": 5, "saturday": 5, "5": 5,
    "sun": 6, "sunday": 6, "6": 6,
}
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def weekday_gate_block(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("replay") or {}).get("weekday_gate")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def weekday_gate_enabled(cfg: dict[str, Any] | None) -> bool:
    block = weekday_gate_block(cfg)
    if not block:
        return False
    if "enabled" in block:
        return bool(block.get("enabled"))
    # enabled omitted: treat non-empty block list as on
    return bool(parse_block_weekdays(block.get("block")))


def parse_block_weekdays(raw: object) -> list[int]:
    """Return sorted unique weekday ints (Mon=0 .. Sun=6)."""
    if raw is None:
        return []
    if isinstance(raw, (str, int, float)):
        items = [raw]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []
    out: set[int] = set()
    for item in items:
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            n = int(item)
            if 0 <= n <= 6:
                out.add(n)
            continue
        key = str(item).strip().lower()
        if key in _DAY_ALIASES:
            out.add(_DAY_ALIASES[key])
    return sorted(out)


def normalize_weekday_tz(raw: object) -> str:
    """Only UTC is supported (Replay bars / entry_time)."""
    if raw is None:
        return "UTC"
    key = str(raw).strip()
    if not key or key.upper() in {"UTC", "Z", "GMT", "ETC/UTC"}:
        return "UTC"
    # Accept Asia/Dhaka as alias for documentation, but resolve to UTC calendar
    # only when hours stay on the same day (overlap). We still stamp tz=UTC.
    if key in {"Asia/Dhaka", "Asia/Dhaka".lower(), "dhaka"}:
        return "UTC"
    return "UTC"


def day_names(days: list[int]) -> list[str]:
    return [_DAY_NAMES[d] for d in days if 0 <= d <= 6]


def apply_replay_weekday_gate(cfg: dict[str, Any]) -> dict[str, Any]:
    """Resolve replay.weekday_gate metadata. Does not rewrite default.yaml."""
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    block = weekday_gate_block(cfg)
    if not weekday_gate_enabled(cfg):
        rc["weekday_gate_resolved"] = RESOLVED_OFF
        rc["weekday_gate_block_days"] = []
        rc["weekday_gate_tz"] = "UTC"
        rc["weekday_gate_note"] = "weekday_gate disabled"
        cfg["replay"] = rc
        return cfg
    days = parse_block_weekdays(block.get("block") or block.get("days") or block.get("weekdays"))
    tz = normalize_weekday_tz(block.get("tz") or block.get("timezone"))
    if not days:
        rc["weekday_gate_resolved"] = RESOLVED_OFF
        rc["weekday_gate_block_days"] = []
        rc["weekday_gate_tz"] = tz
        rc["weekday_gate_note"] = "weekday_gate enabled but block list empty"
        cfg["replay"] = rc
        return cfg
    names = ",".join(day_names(days))
    note = f"block new opens on {names} ({tz} weekday of bar)"
    rc["weekday_gate_resolved"] = RESOLVED_ON
    rc["weekday_gate_block_days"] = list(days)
    rc["weekday_gate_tz"] = tz
    rc["weekday_gate_note"] = note
    cfg["replay"] = rc
    return cfg


def _index_weekdays(index: pd.Index) -> pd.Series:
    """Weekday series (Mon=0) in UTC for a DatetimeIndex; empty -> all -1."""
    if index is None or len(index) == 0:
        return pd.Series(dtype="int64")
    try:
        idx = pd.DatetimeIndex(index)
    except (TypeError, ValueError):
        return pd.Series([-1] * len(index), index=index, dtype="int64")
    if idx.tz is None:
        # Replay history is stored as UTC-naive or UTC; treat naive as UTC.
        try:
            idx = idx.tz_localize("UTC")
        except (TypeError, ValueError):
            return pd.Series([-1] * len(index), index=index, dtype="int64")
    else:
        idx = idx.tz_convert("UTC")
    return pd.Series(idx.weekday, index=index, dtype="int64")


def build_weekday_gate_mask(
    frame: pd.DataFrame,
    cfg: dict[str, Any] | None,
) -> pd.Series:
    """True = block new open (bar weekday in block list)."""
    idx = frame.index if frame is not None else pd.Index([])
    empty = pd.Series(False, index=idx, dtype=bool, name="weekday_gate_block")
    if frame is None or frame.empty:
        return empty
    rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("weekday_gate_resolved") or "") != RESOLVED_ON:
        return empty
    days = [int(x) for x in (rc.get("weekday_gate_block_days") or [])]
    if not days:
        return empty
    wd = _index_weekdays(frame.index)
    if wd.empty or (wd < 0).all():
        return empty
    block = wd.isin(days)
    block.name = "weekday_gate_block"
    return block.astype(bool)


def apply_weekday_gate_to_pred(
    pred: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    hold_code: int | None = None,
) -> pd.DataFrame:
    """Force pred->HOLD on blocked weekdays. No-op if gate off."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    if str(rc.get("weekday_gate_resolved") or "") != RESOLVED_ON:
        return pred
    from forex_lab.features import LABEL_MAP

    hold = int(hold_code if hold_code is not None else LABEL_MAP["HOLD"])
    mask = build_weekday_gate_mask(pred, cfg)
    out = pred.copy()
    out["weekday_gate_block"] = mask.astype(float)
    if not bool(mask.any()):
        return out
    if "pred" in out.columns:
        out.loc[mask, "pred"] = hold
    if "side" in out.columns:
        out.loc[mask, "side"] = "HOLD"
    return out


def weekday_gate_blocks_now(
    cfg: dict[str, Any] | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Live helper: (blocked, reason). Uses resolved or raw config."""
    block = weekday_gate_block(cfg)
    rc = dict((cfg or {}).get("replay") or {})
    days = list(rc.get("weekday_gate_block_days") or [])
    resolved = str(rc.get("weekday_gate_resolved") or "")
    if resolved != RESOLVED_ON:
        if not weekday_gate_enabled(cfg):
            return False, ""
        days = parse_block_weekdays(block.get("block") or block.get("days"))
        if not days:
            return False, ""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    else:
        clock = clock.astimezone(timezone.utc)
    wd = clock.weekday()
    if wd not in days:
        return False, ""
    names = ",".join(day_names(days))
    return True, f"weekday_gate blocks {names} (UTC); today={_DAY_NAMES[wd]}"


def weekday_gate_report_line(cfg: dict[str, Any] | None) -> str:
    rc = dict((cfg or {}).get("replay") or {})
    resolved = rc.get("weekday_gate_resolved") or (
        "on" if weekday_gate_enabled(cfg) else "off"
    )
    days = rc.get("weekday_gate_block_days") or []
    tz = rc.get("weekday_gate_tz") or "UTC"
    note = rc.get("weekday_gate_note") or ""
    names = ",".join(day_names([int(x) for x in days])) if days else "-"
    return f"weekday_gate: {resolved} | block={names} | tz={tz} | {note}"
