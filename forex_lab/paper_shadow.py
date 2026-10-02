"""Paper-live shadow journal (Stage-2 live-edge roadmap).

Append-only JSONL at data/paper_shadow/journal.jsonl.
Fail-soft: never raise into the desk; never block paper open/close.
Not a Replay. Not a promote/gate flip.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from forex_lab.paths import resolve_under_root

logger = logging.getLogger(__name__)

SCHEMA = "paper_shadow_v1"
DHAKA = ZoneInfo("Asia/Dhaka")
_LOCK = threading.Lock()
_DEFAULT_REL = Path("data") / "paper_shadow" / "journal.jsonl"


def journal_path() -> Path:
    """Runtime JSONL path. FORX_SHADOW_JOURNAL overrides for tests."""
    raw = os.environ.get("FORX_SHADOW_JOURNAL")
    if raw:
        return Path(raw)
    return resolve_under_root(_DEFAULT_REL)


def _ts_pair(now: datetime | None = None) -> tuple[str, str]:
    utc = now or datetime.now(timezone.utc)
    if utc.tzinfo is None:
        utc = utc.replace(tzinfo=timezone.utc)
    else:
        utc = utc.astimezone(timezone.utc)
    dhaka = utc.astimezone(DHAKA)
    return (
        utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        dhaka.strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka"),
    )


def _f(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _b(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _source_tag(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in {"board", "manual", "cta", "journal"}:
        return "board"
    if s in {"desk", "auto"}:
        return "desk"
    return "board" if s else "desk"


def build_event(
    event: str,
    *,
    pair: str,
    side: str | None = None,
    conf: float | None = None,
    muted: bool = False,
    below_min: bool = False,
    entry_mid: float | None = None,
    exit_mid: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "desk",
    position_id: str | None = None,
    fill_id: str | None = None,
    exit_reason: str | None = None,
    advisory_id: str | None = None,
    notes: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    ts_utc, ts_dhaka = _ts_pair(now)
    return {
        "schema": SCHEMA,
        "event": str(event or "").strip().lower() or "open",
        "ts_utc": ts_utc,
        "ts_dhaka": ts_dhaka,
        "pair": str(pair or "").upper(),
        "side": str(side or "").upper() or None,
        "conf": _f(conf),
        "muted": _b(muted),
        "below_min": _b(below_min),
        "entry_mid": _f(entry_mid),
        "exit_mid": _f(exit_mid),
        "bid": _f(bid),
        "ask": _f(ask),
        "sl": _f(sl),
        "tp": _f(tp),
        "source": _source_tag(source),
        "position_id": str(position_id) if position_id else None,
        "fill_id": str(fill_id) if fill_id else None,
        "exit_reason": str(exit_reason) if exit_reason else None,
        "advisory_id": str(advisory_id) if advisory_id else None,
        "notes": str(notes or "")[:240],
    }


def append_event(row: dict[str, Any] | None) -> bool:
    """Append one JSONL row. Returns True on success. Never raises."""
    if not row:
        return False
    try:
        path = journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except Exception as exc:  # noqa: BLE001 — fail-soft by design
        logger.debug("paper_shadow append failed: %s", exc)
        return False


def record_open(
    *,
    pair: str,
    side: str,
    conf: float | None = None,
    muted: bool = False,
    below_min: bool = False,
    entry_mid: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "desk",
    position_id: str | None = None,
    fill_id: str | None = None,
    advisory_id: str | None = None,
    notes: str = "",
    now: datetime | None = None,
) -> bool:
    return append_event(
        build_event(
            "open",
            pair=pair,
            side=side,
            conf=conf,
            muted=muted,
            below_min=below_min,
            entry_mid=entry_mid,
            bid=bid,
            ask=ask,
            sl=sl,
            tp=tp,
            source=source,
            position_id=position_id,
            fill_id=fill_id,
            advisory_id=advisory_id,
            notes=notes,
            now=now,
        )
    )


def record_close(
    *,
    pair: str,
    side: str | None = None,
    conf: float | None = None,
    muted: bool = False,
    below_min: bool = False,
    entry_mid: float | None = None,
    exit_mid: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "desk",
    position_id: str | None = None,
    fill_id: str | None = None,
    exit_reason: str | None = None,
    advisory_id: str | None = None,
    notes: str = "",
    now: datetime | None = None,
) -> bool:
    return append_event(
        build_event(
            "close",
            pair=pair,
            side=side or "CLOSE",
            conf=conf,
            muted=muted,
            below_min=below_min,
            entry_mid=entry_mid,
            exit_mid=exit_mid,
            bid=bid,
            ask=ask,
            sl=sl,
            tp=tp,
            source=source,
            position_id=position_id,
            fill_id=fill_id,
            exit_reason=exit_reason,
            advisory_id=advisory_id,
            notes=notes,
            now=now,
        )
    )


def record_desk_call(
    *,
    pair: str,
    side: str | None = None,
    conf: float | None = None,
    muted: bool = False,
    below_min: bool = False,
    entry_mid: float | None = None,
    bid: float | None = None,
    ask: float | None = None,
    sl: float | None = None,
    tp: float | None = None,
    source: str = "board",
    advisory_id: str | None = None,
    notes: str = "",
    now: datetime | None = None,
) -> bool:
    """Optional advisory stamp. Schema-ready; callers may use sparingly."""
    return append_event(
        build_event(
            "desk_call",
            pair=pair,
            side=side,
            conf=conf,
            muted=muted,
            below_min=below_min,
            entry_mid=entry_mid,
            bid=bid,
            ask=ask,
            sl=sl,
            tp=tp,
            source=source,
            advisory_id=advisory_id,
            notes=notes,
            now=now,
        )
    )


def flags_from_gate_text(gate: object) -> tuple[bool, bool]:
    """Best-effort muted / below_min from gate or note text."""
    g = str(gate or "").lower()
    muted = "muted " in g or "weekday" in g or "weekday mute" in g
    below = (
        "below min_conf" in g
        or "below min confidence" in g
        or ("conf" in g and "<" in g and "min" in g)
    )
    return muted, below


def bid_ask_from_row(row: Any) -> tuple[float | None, float | None]:
    """Pull Bid/Ask only when present; never invent."""
    bid = _f(getattr(row, "bid", None))
    ask = _f(getattr(row, "ask", None))
    if bid is None and isinstance(row, dict):
        bid = _f(row.get("bid"))
        ask = _f(row.get("ask"))
    if bid is not None and ask is not None and ask >= bid:
        return bid, ask
    return None, None


__all__ = [
    "SCHEMA",
    "append_event",
    "bid_ask_from_row",
    "build_event",
    "flags_from_gate_text",
    "journal_path",
    "record_close",
    "record_desk_call",
    "record_open",
]
