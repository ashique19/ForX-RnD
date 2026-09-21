"""Watchlist alert strip: signal flips, STALE/MISSING, optional event window.

Compares the current board snapshot to the last persisted one. Unchanged polls
emit nothing. Repeats of the same transition are rate-limited. Never submits
via BrokerPort.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import wave
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from forex_lab.calendar import (
    CalendarBundle,
    CalendarEvent,
    countdown_label,
    event_affects_pair,
    event_window,
)
from forex_lab.clock import fmt_display
from forex_lab.freshness import (
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_STALE,
    now_utc,
)
from forex_lab.paths import resolve_under_root

SIGNAL_CLASSES = frozenset({"BUY", "SELL", "HOLD"})
KIND_FLIP = "flip"
KIND_STALE = "stale"
KIND_MISSING = "missing"
KIND_EVENT = "event"

DEFAULT_PERSIST = "data/alert_state.json"
DEFAULT_COOLDOWN_S = 300
DEFAULT_MAX_VISIBLE = 6
DEFAULT_MAX_STORED = 40
DEFAULT_EVENT_MINUTES = 60
DEFAULT_SOUND = False


@dataclass
class PairSnapshot:
    pair: str
    timeframe: str
    signal: str  # BUY / SELL / HOLD / "" if unknown
    validity: str

    def key(self) -> str:
        return snapshot_key(self.pair, self.timeframe)


@dataclass
class Alert:
    id: str
    kind: str
    pair: str
    timeframe: str
    message: str
    created_at: str  # ISO-8601 UTC
    from_value: str = ""
    to_value: str = ""
    event_key: str = ""

    def rate_key(self) -> str:
        if self.kind == KIND_EVENT:
            return f"event:{self.event_key or self.id}"
        return f"{self.kind}:{self.pair}:{self.timeframe}:{self.from_value}->{self.to_value}"


@dataclass
class AlertState:
    snapshots: dict[str, PairSnapshot] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)
    dismissed: list[str] = field(default_factory=list)
    last_fired: dict[str, float] = field(default_factory=dict)
    warned_events: list[str] = field(default_factory=list)
    sound: bool = DEFAULT_SOUND


def snapshot_key(pair: str, timeframe: str) -> str:
    return f"{str(pair).upper()}|{str(timeframe or '').strip() or '1h'}"


def alerts_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    board = dict((cfg or {}).get("board") or {})
    raw = dict(board.get("alerts") or {})
    raw.setdefault("enabled", True)
    raw.setdefault("sound", DEFAULT_SOUND)
    raw.setdefault("cooldown_s", DEFAULT_COOLDOWN_S)
    raw.setdefault("max_visible", DEFAULT_MAX_VISIBLE)
    raw.setdefault("max_stored", DEFAULT_MAX_STORED)
    raw.setdefault("event_warning", True)
    raw.setdefault("event_minutes", DEFAULT_EVENT_MINUTES)
    raw.setdefault("persist_file", DEFAULT_PERSIST)
    return raw


def persist_path(cfg: dict[str, Any] | None = None) -> Path:
    rel = alerts_cfg(cfg).get("persist_file") or DEFAULT_PERSIST
    return resolve_under_root(rel)


def live_signal_class(row: Any) -> str:
    """BUY/SELL/HOLD the trader sees, or last model class while the flash is —."""
    flashed = str(getattr(row, "buy_sell", "") or "").upper()
    if flashed in SIGNAL_CLASSES:
        return flashed
    raw = str(getattr(row, "raw_signal", "") or "").upper()
    if raw in SIGNAL_CLASSES:
        return raw
    return ""


def snapshot_from_row(row: Any) -> PairSnapshot:
    pair = str(getattr(row, "pair", "") or "").upper()
    tf = str(getattr(row, "timeframe", "") or "1h")
    validity = str(getattr(row, "validity", "") or VALIDITY_MISSING).upper()
    return PairSnapshot(pair=pair, timeframe=tf, signal=live_signal_class(row), validity=validity)


def _iso(now: datetime) -> str:
    clock = now
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_id(*parts: object) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _flip_message(pair: str, before: str, after: str) -> str:
    return f"{pair} {before} → {after}"


def _validity_message(pair: str, validity: str) -> str:
    if validity == VALIDITY_STALE:
        return f"{pair} data STALE"
    if validity == VALIDITY_MISSING:
        return f"{pair} data MISSING"
    if validity == VALIDITY_ERROR:
        return f"{pair} data ERROR"
    return f"{pair} {validity}"


def collect_signal_alerts(
    previous: dict[str, PairSnapshot],
    current: Iterable[PairSnapshot],
    *,
    now: datetime,
) -> list[Alert]:
    """Flip + STALE/MISSING vs last snapshot. First sighting of a pair is silent."""
    created = _iso(now)
    out: list[Alert] = []
    for snap in current:
        if not snap.pair:
            continue
        key = snap.key()
        prev = previous.get(key)
        if prev is None:
            continue
        if (
            snap.signal
            and prev.signal
            and snap.signal != prev.signal
            and snap.signal in SIGNAL_CLASSES
            and prev.signal in SIGNAL_CLASSES
        ):
            aid = _make_id(KIND_FLIP, snap.pair, snap.timeframe, prev.signal, snap.signal, created)
            out.append(
                Alert(
                    id=aid,
                    kind=KIND_FLIP,
                    pair=snap.pair,
                    timeframe=snap.timeframe,
                    message=_flip_message(snap.pair, prev.signal, snap.signal),
                    created_at=created,
                    from_value=prev.signal,
                    to_value=snap.signal,
                )
            )
        if snap.validity != prev.validity and snap.validity in {
            VALIDITY_STALE,
            VALIDITY_MISSING,
            VALIDITY_ERROR,
        }:
            kind = KIND_STALE if snap.validity == VALIDITY_STALE else KIND_MISSING
            aid = _make_id(kind, snap.pair, snap.timeframe, prev.validity, snap.validity, created)
            out.append(
                Alert(
                    id=aid,
                    kind=kind,
                    pair=snap.pair,
                    timeframe=snap.timeframe,
                    message=_validity_message(snap.pair, snap.validity),
                    created_at=created,
                    from_value=prev.validity,
                    to_value=snap.validity,
                )
            )
    return out


def event_identity(event: CalendarEvent) -> str:
    return f"{event.currency}|{event.title}|{event.when}"


def collect_event_alerts(
    events: Iterable[CalendarEvent],
    pairs: Iterable[str],
    *,
    now: datetime,
    warned: Iterable[str] | None = None,
    minutes: int = DEFAULT_EVENT_MINUTES,
    during_minutes: int = 15,
) -> list[Alert]:
    """High-impact events in the before/during window. Once per event identity."""
    wanted = [str(p).upper() for p in pairs if p]
    already = set(warned or [])
    created = _iso(now)
    out: list[Alert] = []
    seen: set[str] = set()
    for event in events:
        window = event_window(
            event,
            now,
            before_minutes=int(minutes),
            during_minutes=int(during_minutes),
            after_minutes=0,
        )
        if window not in {"before", "during"}:
            continue
        hit = [p for p in wanted if event_affects_pair(event, p)]
        if not hit:
            continue
        ident = event_identity(event)
        if ident in already or ident in seen:
            continue
        seen.add(ident)
        when = event.when_dt()
        cd = countdown_label(when, now)
        pairs_txt = ",".join(hit)
        msg = f"{event.currency} {event.title} {cd} · {pairs_txt}"
        aid = _make_id(KIND_EVENT, ident, created)
        out.append(
            Alert(
                id=aid,
                kind=KIND_EVENT,
                pair=hit[0],
                timeframe="",
                message=msg,
                created_at=created,
                from_value=window,
                to_value=event.when,
                event_key=ident,
            )
        )
    return out


def filter_rate_limited(
    candidates: Iterable[Alert],
    last_fired: dict[str, float],
    *,
    now_ts: float,
    cooldown_s: int,
) -> list[Alert]:
    cool = max(0, int(cooldown_s))
    kept: list[Alert] = []
    pending: dict[str, float] = {}
    for alert in candidates:
        key = alert.rate_key()
        prev = last_fired.get(key)
        recent = pending.get(key)
        stamp = recent if recent is not None else prev
        if stamp is not None and (now_ts - float(stamp)) < cool:
            continue
        pending[key] = now_ts
        kept.append(alert)
    return kept


def apply_new_alerts(
    state: AlertState,
    new_alerts: Iterable[Alert],
    *,
    now_ts: float,
    max_stored: int = DEFAULT_MAX_STORED,
) -> AlertState:
    dismissed = set(state.dismissed)
    added: list[Alert] = []
    for alert in new_alerts:
        if alert.id in dismissed:
            continue
        state.last_fired[alert.rate_key()] = now_ts
        if alert.kind == KIND_EVENT and alert.event_key:
            if alert.event_key not in state.warned_events:
                state.warned_events.append(alert.event_key)
        added.append(alert)
    state.alerts = added + [a for a in state.alerts if a.id not in {x.id for x in added}]
    cap = max(1, int(max_stored))
    if len(state.alerts) > cap:
        state.alerts = state.alerts[:cap]
    if len(state.warned_events) > 80:
        state.warned_events = state.warned_events[-80:]
    if len(state.dismissed) > 200:
        state.dismissed = state.dismissed[-200:]
    if len(state.last_fired) > 200:
        # keep the most recently fired keys
        ranked = sorted(state.last_fired.items(), key=lambda kv: kv[1], reverse=True)
        state.last_fired = dict(ranked[:200])
    return state


def visible_alerts(state: AlertState, *, max_visible: int = DEFAULT_MAX_VISIBLE) -> list[Alert]:
    dismissed = set(state.dismissed)
    open_alerts = [a for a in state.alerts if a.id not in dismissed]
    cap = max(0, int(max_visible))
    return open_alerts[:cap]


def dismiss_alert(state: AlertState, alert_id: str) -> AlertState:
    aid = str(alert_id)
    if aid not in state.dismissed:
        state.dismissed.append(aid)
    state.alerts = [a for a in state.alerts if a.id != aid]
    return state


def _ts(now: datetime | None) -> tuple[datetime, float]:
    clock = now or now_utc()
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    else:
        clock = clock.astimezone(timezone.utc)
    return clock, clock.timestamp()


def ingest_watch(
    rows: Iterable[Any],
    *,
    calendar: CalendarBundle | None = None,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    state: AlertState | None = None,
    sound: bool | None = None,
) -> tuple[AlertState, list[Alert]]:
    """Diff rows (+ optional calendar) against persisted snapshots.

    First observation of a pair is the baseline (no flip/STALE spam).
    Nearby calendar events warn once per event identity.
    """
    acfg = alerts_cfg(cfg)
    clock, now_ts = _ts(now)
    current_state = state if state is not None else AlertState()
    if sound is not None:
        current_state.sound = bool(sound)

    snaps = [snapshot_from_row(r) for r in rows]
    snap_map = {s.key(): s for s in snaps if s.pair}

    if not acfg.get("enabled", True):
        current_state.snapshots = snap_map
        return current_state, []

    candidates = collect_signal_alerts(current_state.snapshots, snaps, now=clock)
    if acfg.get("event_warning", True) and calendar is not None:
        minutes = int(acfg.get("event_minutes") or DEFAULT_EVENT_MINUTES)
        cal_cfg = dict((cfg or {}).get("calendar") or {})
        during = int(cal_cfg.get("during_minutes") or 15)
        pairs = [s.pair for s in snaps]
        candidates.extend(
            collect_event_alerts(
                list(calendar.events or []),
                pairs,
                now=clock,
                warned=current_state.warned_events,
                minutes=minutes,
                during_minutes=during,
            )
        )

    cooldown = int(acfg.get("cooldown_s") or DEFAULT_COOLDOWN_S)
    fresh = filter_rate_limited(
        candidates,
        current_state.last_fired,
        now_ts=now_ts,
        cooldown_s=cooldown,
    )
    apply_new_alerts(
        current_state,
        fresh,
        now_ts=now_ts,
        max_stored=int(acfg.get("max_stored") or DEFAULT_MAX_STORED),
    )
    current_state.snapshots = snap_map
    return current_state, fresh


def _snap_from_dict(raw: dict[str, Any]) -> PairSnapshot:
    return PairSnapshot(
        pair=str(raw.get("pair") or "").upper(),
        timeframe=str(raw.get("timeframe") or "1h"),
        signal=str(raw.get("signal") or ""),
        validity=str(raw.get("validity") or ""),
    )


def _alert_from_dict(raw: dict[str, Any]) -> Alert:
    return Alert(
        id=str(raw.get("id") or ""),
        kind=str(raw.get("kind") or ""),
        pair=str(raw.get("pair") or ""),
        timeframe=str(raw.get("timeframe") or ""),
        message=str(raw.get("message") or ""),
        created_at=str(raw.get("created_at") or ""),
        from_value=str(raw.get("from_value") or ""),
        to_value=str(raw.get("to_value") or ""),
        event_key=str(raw.get("event_key") or ""),
    )


def state_to_dict(state: AlertState) -> dict[str, Any]:
    return {
        "snapshots": {k: asdict(v) for k, v in state.snapshots.items()},
        "alerts": [asdict(a) for a in state.alerts],
        "dismissed": list(state.dismissed),
        "last_fired": {k: float(v) for k, v in state.last_fired.items()},
        "warned_events": list(state.warned_events),
        "sound": bool(state.sound),
    }


def state_from_dict(raw: dict[str, Any] | None, *, default_sound: bool = DEFAULT_SOUND) -> AlertState:
    data = raw or {}
    snaps_raw = data.get("snapshots") or {}
    snapshots = {}
    if isinstance(snaps_raw, dict):
        for key, val in snaps_raw.items():
            if isinstance(val, dict):
                snapshots[str(key)] = _snap_from_dict(val)
    alerts = [_alert_from_dict(a) for a in (data.get("alerts") or []) if isinstance(a, dict) and a.get("id")]
    last_fired = {}
    for key, val in dict(data.get("last_fired") or {}).items():
        try:
            last_fired[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    sound = data.get("sound")
    if sound is None:
        sound = default_sound
    return AlertState(
        snapshots=snapshots,
        alerts=alerts,
        dismissed=[str(x) for x in (data.get("dismissed") or [])],
        last_fired=last_fired,
        warned_events=[str(x) for x in (data.get("warned_events") or [])],
        sound=bool(sound),
    )


def load_state(cfg: dict[str, Any] | None = None, path: str | Path | None = None) -> AlertState:
    acfg = alerts_cfg(cfg)
    p = Path(path) if path is not None else persist_path(cfg)
    default_sound = bool(acfg.get("sound", DEFAULT_SOUND))
    try:
        if not p.exists():
            return AlertState(sound=default_sound)
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AlertState(sound=default_sound)
        return state_from_dict(raw, default_sound=default_sound)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return AlertState(sound=default_sound)


def save_state(state: AlertState, cfg: dict[str, Any] | None = None, path: str | Path | None = None) -> Path | None:
    p = Path(path) if path is not None else persist_path(cfg)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state_to_dict(state), indent=2), encoding="utf-8")
        return p
    except OSError:
        return None


def process_watch(
    rows: Iterable[Any],
    *,
    calendar: CalendarBundle | None = None,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    sound: bool | None = None,
    persist: bool = True,
    path: str | Path | None = None,
    state: AlertState | None = None,
) -> tuple[AlertState, list[Alert]]:
    current = state if state is not None else (load_state(cfg, path) if persist else AlertState())
    new_state, fresh = ingest_watch(
        rows,
        calendar=calendar,
        cfg=cfg,
        now=now,
        state=current,
        sound=sound,
    )
    if persist:
        save_state(new_state, cfg, path)
    return new_state, fresh


def format_alert_time(alert: Alert, cfg: dict[str, Any] | None = None) -> str:
    return fmt_display(alert.created_at, cfg, seconds=True)


def kind_tone(kind: str) -> str:
    return {
        KIND_FLIP: "#1d4ed8",
        KIND_STALE: "#b45309",
        KIND_MISSING: "#64748b",
        KIND_EVENT: "#6d28d9",
    }.get(kind, "#334155")


def beep_wav(*, freq: float = 880.0, ms: int = 140, volume: float = 0.22, rate: int = 22050) -> bytes:
    """Tiny mono WAV for optional alert sound. No extra dependency."""
    n = max(1, int(rate * max(1, int(ms)) / 1000.0))
    fade = min(400, n // 4)
    frames = bytearray()
    for i in range(n):
        env = 1.0
        if fade:
            env = min(i / fade, (n - 1 - i) / fade, 1.0)
        sample = int(volume * env * 32767.0 * math.sin(2.0 * math.pi * freq * (i / rate)))
        frames += struct.pack("<h", max(-32767, min(32767, sample)))
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return buf.getvalue()
