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
from datetime import datetime, timezone
from typing import Any

from forex_lab.clock import timezone_tag, zoneinfo_for
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


def _hhmm(hour: float) -> str:
    h = int(hour) % 24
    minutes = int(round((hour - int(hour)) * 60.0)) % 60
    return f"{h:02d}:{minutes:02d}"


def utc_hour_as_display(hour: float, cfg: dict[str, Any] | None = None) -> str:
    """Map a UTC hour-of-day onto the UI timezone (Asia/Dhaka by default)."""
    h = int(hour) % 24
    minutes = int(round((hour - int(hour)) * 60.0)) % 60
    t = datetime(2026, 9, 21, h, minutes, tzinfo=timezone.utc)
    return t.astimezone(zoneinfo_for(cfg)).strftime("%H:%M")


def window_dual_label(
    start: float,
    end: float,
    cfg: dict[str, Any] | None = None,
) -> str:
    tag = timezone_tag(cfg)
    return (
        f"{_hhmm(start)}–{_hhmm(end)} UTC / "
        f"{utc_hour_as_display(start, cfg)}–{utc_hour_as_display(end, cfg)} {tag}"
    )


def _session_note(
    labels: list[str],
    windows: dict[str, tuple[float, float]],
    cfg: dict[str, Any] | None,
) -> str:
    bits = [f"{name} {window_dual_label(*windows[name], cfg)}" for name in labels]
    if len(labels) == 1:
        return f"{labels[0]} session ({bits[0]})"
    return f"overlap {'+'.join(labels)} ({'; '.join(bits)})"


def classify_session(
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    respect_market_hours: bool = True,
) -> SessionState:
    """Name the session from the UTC clock. Weekend / Friday 21:00 UTC → closed.

    ``now`` may be tz-aware (e.g. Asia/Dhaka); it is converted to UTC before
    matching London/NY/Asia windows. Do not pass naive Dhaka wall time.
    """
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
    return SessionState(
        name="+".join(labels),
        labels=list(labels),
        hour_utc=hour,
        weekday=t.weekday(),
        market_open=open_now,
        note=_session_note(labels, windows, cfg),
    )


# ---------------------------------------------------------------------------
# Replay / signal session gate (SERIAL step 5)
# ---------------------------------------------------------------------------
# Feature columns (UTC, end exclusive) — see forex_lab.features:
#   London  07:00–16:00 UTC  (= 13:00–22:00 Asia/Dhaka)
#   NY      13:00–21:00 UTC  (= 19:00–03:00 Asia/Dhaka)
#   Overlap London∩NY 13:00–16:00 UTC (= 19:00–22:00 Asia/Dhaka)
#
# ``replay.session_gate`` maps to ``signals.sessions`` for Replay only
# (via apply_replay_session_gate). Live paper keeps signals.sessions=[] unless set.
# Modes: off | overlap | london_ny

SESSION_GATE_OFF = frozenset({"", "off", "none", "false", "0", "all", "null"})
SESSION_GATE_OVERLAP = frozenset({"overlap", "ldn_ny", "london_ny_overlap", "ldn-ny"})
SESSION_GATE_UNION = frozenset({"london_ny", "london+ny", "union", "ldn+ny", "london_and_ny"})

SESSION_GATE_HOURS_UTC = {
    "london": (7.0, 16.0),
    "ny": (13.0, 21.0),
    "overlap": (13.0, 16.0),  # London ∩ NY
}


def normalize_session_gate(raw: object) -> str:
    """Return canonical gate: ``off`` | ``overlap`` | ``london_ny``."""
    if raw is None:
        return "off"
    key = str(raw).strip().lower().replace(" ", "_")
    if key in SESSION_GATE_OFF:
        return "off"
    if key in SESSION_GATE_OVERLAP:
        return "overlap"
    if key in SESSION_GATE_UNION:
        return "london_ny"
    return "off"


def session_gate_allow_list(gate: str) -> list[str] | None:
    """Map gate to ``signals.sessions`` names, or None when off.

    ``overlap`` → ``["ldn_ny"]`` (column ``sess_ldn_ny`` = London ∩ NY).
    ``london_ny`` → ``["london", "ny"]`` (union of session feature flags).
    """
    mode = normalize_session_gate(gate)
    if mode == "overlap":
        return ["ldn_ny"]
    if mode == "london_ny":
        return ["london", "ny"]
    return None


def session_gate_hours_note(gate: str | None = None) -> str:
    """Human-readable UTC + Asia/Dhaka windows for compare docs / reports."""
    mode = normalize_session_gate(gate)
    if mode == "off":
        return "off (all UTC hours; no session entry filter)"
    if mode == "overlap":
        return (
            "overlap London∩NY only: 13:00–16:00 UTC "
            "(19:00–22:00 Asia/Dhaka); feature sess_ldn_ny"
        )
    # london_ny union
    return (
        "London+NY union: London 07:00–16:00 UTC (13:00–22:00 Asia/Dhaka) OR "
        "NY 13:00–21:00 UTC (19:00–03:00 Asia/Dhaka); features sess_london | sess_ny"
    )


def apply_replay_session_gate(cfg: dict[str, Any]) -> dict[str, Any]:
    """If ``replay.session_gate`` is on, set ``signals.sessions`` for this run.

    Does not rewrite default.yaml. Leaves an explicit non-empty ``signals.sessions``
    alone (caller override wins). Returns the same dict (mutated) for chaining.
    """
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    mode = normalize_session_gate(rc.get("session_gate"))
    allow = session_gate_allow_list(mode)
    signals = dict(cfg.get("signals") or {})
    existing = signals.get("sessions") or []
    if allow is None:
        # Gate off: do not clear an explicit signals.sessions allow-list.
        return cfg
    if existing:
        # Explicit signals.sessions already set — leave it (and keep gate metadata).
        names = [str(x).strip().lower() for x in existing]
        if names == ["ldn_ny"] or names == ["overlap"]:
            resolved = "overlap"
        elif set(names) >= {"london", "ny"}:
            resolved = "london_ny"
        else:
            resolved = "custom"
        rc["session_gate_resolved"] = resolved
        cfg["replay"] = rc
        return cfg
    signals["sessions"] = list(allow)
    cfg["signals"] = signals
    rc["session_gate_resolved"] = mode
    cfg["replay"] = rc
    return cfg

