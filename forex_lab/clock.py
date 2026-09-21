"""Display-time formatting for the trader UI.

Underlying bars, events, and paper fills stay UTC (or naive-UTC). The Streamlit
desk shows clocks in ``ui.timezone`` (default **Asia/Dhaka**, UTC+6). Session
windows (Asia/London/NY) remain UTC definitions — only the readout converts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

DEFAULT_TIMEZONE = "Asia/Dhaka"
DEFAULT_TZ_TAG = "Asia/Dhaka"


def timezone_name(cfg: dict[str, Any] | None = None) -> str:
    ui = dict((cfg or {}).get("ui") or {})
    raw = str(ui.get("timezone") or DEFAULT_TIMEZONE).strip()
    return raw or DEFAULT_TIMEZONE


def timezone_tag(cfg: dict[str, Any] | None = None) -> str:
    ui = dict((cfg or {}).get("ui") or {})
    tag = str(ui.get("timezone_tag") or timezone_name(cfg) or DEFAULT_TZ_TAG).strip()
    return tag or DEFAULT_TZ_TAG


def zoneinfo_for(cfg: dict[str, Any] | None = None) -> ZoneInfo:
    name = timezone_name(cfg)
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def parse_ts(value: object) -> datetime | None:
    """Parse a timestamp. Naive values and ``… UTC`` labels are treated as UTC."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        ts = value
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "none", "-"}:
        return None
    zone: Any = timezone.utc
    # Explicit trailing tags (board labels).
    for tag, zi in (
        (" UTC", timezone.utc),
        (" Asia/Dhaka", ZoneInfo(DEFAULT_TIMEZONE)),
        (" BDST", ZoneInfo(DEFAULT_TIMEZONE)),
    ):
        if text.endswith(tag):
            text = text[: -len(tag)].strip()
            zone = zi
            break
    else:
        if text.endswith("Z") or "+00:00" in text or text.endswith("+0000"):
            zone = timezone.utc
    try:
        ts = pd.to_datetime(text, errors="coerce")
    except (TypeError, ValueError):
        return None
    if ts is None or pd.isna(ts):
        return None
    py = ts.to_pydatetime()
    if py.tzinfo is not None:
        return py.astimezone(timezone.utc)
    return py.replace(tzinfo=zone).astimezone(timezone.utc)


def to_display(value: object, cfg: dict[str, Any] | None = None) -> datetime | None:
    ts = parse_ts(value)
    if ts is None:
        return None
    return ts.astimezone(zoneinfo_for(cfg))


def fmt_display(
    value: object,
    cfg: dict[str, Any] | None = None,
    *,
    seconds: bool = False,
) -> str:
    """Format a timestamp in the UI timezone. ``n/a`` if unparseable."""
    local = to_display(value, cfg)
    if local is None:
        return "n/a"
    tag = timezone_tag(cfg)
    if seconds:
        return local.strftime("%Y-%m-%d %H:%M:%S") + f" {tag}"
    return local.strftime("%Y-%m-%d %H:%M") + f" {tag}"


def relabel(value: object, cfg: dict[str, Any] | None = None, *, seconds: bool = False) -> str:
    """Convert a stored UTC label (or mixed ``time (note)``) for the UI clock."""
    if value is None:
        return "n/a"
    raw = str(value).strip()
    if not raw or raw.lower() == "n/a":
        return "n/a"
    suffix = ""
    head = raw
    if " (" in raw and raw.endswith(")"):
        maybe, rest = raw.split(" (", 1)
        if parse_ts(maybe) is not None:
            head, suffix = maybe, " (" + rest
    parsed = parse_ts(head)
    if parsed is None:
        return raw
    return fmt_display(parsed, cfg, seconds=seconds) + suffix


_UTC_STAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z| UTC)?(?! Asia/Dhaka)(?! BDST)"
)


def relabel_in_text(text: object, cfg: dict[str, Any] | None = None) -> str:
    """Replace UTC (or parseable) stamps inside a sentence; leave other words intact."""
    if text is None:
        return ""
    raw = str(text)
    if not raw:
        return raw

    def _sub(match: re.Match[str]) -> str:
        piece = match.group(0)
        converted = relabel(piece, cfg, seconds=(":" in piece[13:] and piece.count(":") >= 2))
        return converted if converted != "n/a" else piece

    return _UTC_STAMP.sub(_sub, raw)


def clock_note(cfg: dict[str, Any] | None = None) -> str:
    name = timezone_name(cfg)
    tag = timezone_tag(cfg)
    sample = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc).astimezone(zoneinfo_for(cfg))
    off = sample.utcoffset()
    hours = (off.total_seconds() / 3600.0) if off is not None else 0.0
    sign = "+" if hours >= 0 else ""
    if abs(hours - round(hours)) < 1e-9:
        off_s = f"UTC{sign}{int(round(hours))}"
    else:
        off_s = f"UTC{sign}{hours:g}"
    return f"Times shown in {tag} ({name}, {off_s}). Sessions stay on UTC windows."
