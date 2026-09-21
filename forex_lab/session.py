"""Clock-based FX session classification for the trader board.

Research UI only — not a broker session clock. Windows are UTC and
configurable under ``board.sessions``. Overlap is intentional (London∩NY).

Default windows (end exclusive; Asia wraps midnight so Sunday 21:00 UTC
open is not shown as OFF while the 24/5 market is open):

    asia    21:00 – 07:00 UTC  (Sydney/Tokyo)
    london  07:00 – 16:00 UTC
    ny      13:00 – 21:00 UTC

Feature-flag hours in ``forex_lab.features`` (00–07 / 07–16 / 13–21) stay
as trained. Do not reuse this module to rewrite model columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from forex_lab.freshness import fx_session_open, now_utc

SESSION_ASIA = "asia"
SESSION_LONDON = "london"
SESSION_NY = "ny"
SESSION_CLOSED = "closed"
SESSION_OFF = "off"

DISPLAY_ORDER = (SESSION_ASIA, SESSION_LONDON, SESSION_NY)

# (start_hour, end_hour) in UTC. start > end means wrap past midnight.
DEFAULT_WINDOWS: dict[str, tuple[float, float]] = {
    SESSION_ASIA: (21.0, 7.0),
    SESSION_LONDON: (7.0, 16.0),
    SESSION_NY: (13.0, 21.0),
}


@dataclass
class SessionState:
    """Active FX session(s) at a clock instant."""

    name: str
    labels: list[str] = field(default_factory=list)
    hour_utc: float = 0.0
    weekday: int = 0
    market_open: bool = True
    note: str = ""

    def badge(self) -> str:
        if self.name == SESSION_CLOSED:
            return "CLOSED"
        if self.name == SESSION_OFF:
            return "OFF"
        if not self.labels:
            return self.name.upper()
        return "+".join(lab.upper() for lab in self.labels)

    def as_label(self) -> str:
        return self.badge()


def _hour_frac(t: datetime) -> float:
    return t.hour + t.minute / 60.0 + t.second / 3600.0


def _parse_window(raw: object) -> tuple[float, float] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        try:
            start, end = float(raw[0]), float(raw[1])
        except (TypeError, ValueError):
            return None
        if not (0.0 <= start < 24.0 and 0.0 <= end <= 24.0):
            return None
        return start, end
    return None


def session_windows(cfg: dict[str, Any] | None = None) -> dict[str, tuple[float, float]]:
    """UTC (start, end) per named session. End exclusive; wrap if start > end."""
    out = dict(DEFAULT_WINDOWS)
    board = dict((cfg or {}).get("board") or {})
    block = board.get("sessions")
    if not isinstance(block, dict):
        block = (cfg or {}).get("sessions")
    if not isinstance(block, dict):
        return out
    for key in DISPLAY_ORDER:
        parsed = _parse_window(block.get(key))
        if parsed is not None:
            out[key] = parsed
    return out


def hour_in_window(hour: float, window: tuple[float, float]) -> bool:
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def active_sessions(hour: float, windows: dict[str, tuple[float, float]] | None = None) -> list[str]:
    wins = windows or DEFAULT_WINDOWS
    return [name for name in DISPLAY_ORDER if hour_in_window(hour, wins[name])]


def classify_session(
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    respect_market_hours: bool = True,
) -> SessionState:
    """Name the session from the clock. Weekend / Friday 21:00 UTC → closed."""
    t = now_utc(now)
    hour = _hour_frac(t)
    windows = session_windows(cfg)
    open_now = fx_session_open(t)
    if respect_market_hours and not open_now:
        return SessionState(
            name=SESSION_CLOSED,
            labels=[],
            hour_utc=hour,
            weekday=t.weekday(),
            market_open=False,
            note="spot FX typically closed (weekend / Friday after ~21:00 UTC) — not a broker calendar",
        )
    labels = active_sessions(hour, windows)
    if not labels:
        return SessionState(
            name=SESSION_OFF,
            labels=[],
            hour_utc=hour,
            weekday=t.weekday(),
            market_open=open_now,
            note="no configured Asia/London/NY window at this UTC hour",
        )
    name = "+".join(labels)
    if len(labels) == 1:
        note = f"{labels[0]} session (UTC windows from board.sessions)"
    else:
        note = f"overlap {'+'.join(labels)} (UTC windows from board.sessions)"
    return SessionState(
        name=name,
        labels=list(labels),
        hour_utc=hour,
        weekday=t.weekday(),
        market_open=open_now,
        note=note,
    )
