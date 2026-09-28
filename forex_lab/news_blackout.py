"""Replay / live news blackout gate (SERIAL step 6).

Blocks new directional opens inside a high-impact event window when (and only
when) event data covering the decision clock is available.

Honesty constraint
------------------
The desk calendar (`data/calendar_cache.json`) is the unofficial Forex Factory
**this-week** dump via nfs.faireconomy.media — not a point-in-time history back
to 2015 (or even to a short-window start like 2024-09-24). Replay therefore
must **not** pretend a full-history news gate ran unless a causal historic
calendar file is supplied via ``replay.news_blackout.events_file``.

When coverage is missing, the gate resolves to ``skipped_no_pit_calendar`` and
leaves predictions unchanged (fail-soft, no fake before/after).

Live desk already has ``gates.no_new_opens_in_event_window`` (default off with
``gates.enabled``). This module shares the same before/during/after minutes and
is the Replay-side knob + reusable mask builder.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from forex_lab.calendar import (
    IMPACT_RANK,
    CalendarEvent,
    event_affects_pair,
    event_window,
    impact_rank,
    parse_event_time,
    parse_events,
)
from forex_lab.paths import resolve_under_root

WINDOW_BEFORE = "before"
WINDOW_DURING = "during"
WINDOW_AFTER = "after"
DEFAULT_WINDOWS = (WINDOW_BEFORE, WINDOW_DURING, WINDOW_AFTER)

# Canonical resolved modes written to replay.news_blackout_resolved
RESOLVED_OFF = "off"
RESOLVED_ACTIVE = "active"
RESOLVED_SKIPPED = "skipped_no_pit_calendar"
RESOLVED_EMPTY = "skipped_no_events"


def _nb_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    rc = dict((cfg or {}).get("replay") or {})
    raw = rc.get("news_blackout")
    if isinstance(raw, dict):
        return dict(raw)
    if raw is None:
        return {}
    return {"enabled": raw}


def _truthy(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off", "none", "no"}
    return bool(value)


def news_blackout_enabled(cfg: dict[str, Any] | None) -> bool:
    block = _nb_cfg(cfg)
    if "enabled" in block:
        return _truthy(block.get("enabled"), default=False)
    return False


def news_blackout_minutes(cfg: dict[str, Any] | None) -> tuple[int, int, int]:
    """before, during, after minutes — prefer replay.news_blackout, else calendar/advice."""
    block = _nb_cfg(cfg)
    cal = dict((cfg or {}).get("calendar") or {})
    advice = dict((cfg or {}).get("advice") or {})
    before = int(
        block.get("before_minutes")
        if block.get("before_minutes") is not None
        else advice.get("before_minutes")
        if advice.get("before_minutes") is not None
        else cal.get("before_minutes")
        or 60
    )
    during = int(
        block.get("during_minutes")
        if block.get("during_minutes") is not None
        else advice.get("during_minutes")
        if advice.get("during_minutes") is not None
        else cal.get("during_minutes")
        or 15
    )
    after = int(
        block.get("after_minutes")
        if block.get("after_minutes") is not None
        else advice.get("after_minutes")
        if advice.get("after_minutes") is not None
        else cal.get("after_minutes")
        or 30
    )
    return max(0, before), max(0, during), max(0, after)


def news_blackout_windows(cfg: dict[str, Any] | None) -> set[str]:
    block = _nb_cfg(cfg)
    raw = block.get("event_windows") or block.get("windows")
    if not raw:
        return set(DEFAULT_WINDOWS)
    out = {str(x).strip().lower() for x in raw if str(x).strip()}
    return out or set(DEFAULT_WINDOWS)


def news_blackout_min_impact(cfg: dict[str, Any] | None) -> int:
    block = _nb_cfg(cfg)
    cal = dict((cfg or {}).get("calendar") or {})
    raw = str(block.get("min_impact") or cal.get("min_impact") or "High").strip().lower()
    return IMPACT_RANK.get(raw, IMPACT_RANK["high"])


def news_blackout_currencies(
    cfg: dict[str, Any] | None, pair: str = ""
) -> set[str] | None:
    """Explicit currency allow-list, or None to use pair legs (events_affecting)."""
    block = _nb_cfg(cfg)
    raw = block.get("currencies")
    if raw is None or raw == "" or raw == []:
        return None
    if isinstance(raw, str):
        parts = [p.strip().upper() for p in raw.replace(";", ",").split(",") if p.strip()]
        return set(parts) if parts else None
    out = {str(x).strip().upper() for x in raw if str(x).strip()}
    return out or None


def require_historic_coverage(cfg: dict[str, Any] | None) -> bool:
    block = _nb_cfg(cfg)
    if "require_historic_coverage" in block:
        return _truthy(block.get("require_historic_coverage"), default=True)
    return True


def events_file_path(cfg: dict[str, Any] | None) -> Path | None:
    block = _nb_cfg(cfg)
    raw = block.get("events_file") or block.get("historic_file")
    if not raw:
        return None
    try:
        return resolve_under_root(str(raw))
    except Exception:
        p = Path(str(raw))
        return p if p.is_file() else None


def _as_utc(ts: datetime | pd.Timestamp | None) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, float) and pd.isna(ts):
        return None
    if isinstance(ts, pd.Timestamp):
        if pd.isna(ts):
            return None
        if ts.tzinfo is None:
            return ts.tz_localize("UTC").to_pydatetime()
        return ts.tz_convert("UTC").to_pydatetime()
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    try:
        return parse_event_time(ts)
    except Exception:
        return None


def filter_events_for_blackout(
    events: Iterable[CalendarEvent] | None,
    *,
    pair: str,
    cfg: dict[str, Any] | None = None,
) -> list[CalendarEvent]:
    """High-impact events that hit the pair (or explicit currency list)."""
    items = list(events or [])
    min_rank = news_blackout_min_impact(cfg)
    ccys = news_blackout_currencies(cfg, pair)
    out: list[CalendarEvent] = []
    for e in items:
        if impact_rank(e.impact) < min_rank:
            continue
        if ccys is not None:
            if str(e.currency or "").upper() not in ccys:
                continue
            out.append(e)
        elif event_affects_pair(e, pair):
            out.append(e)
    return out


def is_in_news_blackout(
    ts: datetime | pd.Timestamp | None,
    events: Iterable[CalendarEvent] | None,
    *,
    pair: str,
    cfg: dict[str, Any] | None = None,
) -> tuple[bool, CalendarEvent | None, str]:
    """Return (blocked, event, window) for one decision clock."""
    clock = _as_utc(ts)
    if clock is None:
        return False, None, "none"
    before, during, after = news_blackout_minutes(cfg)
    windows = news_blackout_windows(cfg)
    ranked: list[tuple[int, float, CalendarEvent, str]] = []
    rank = {WINDOW_DURING: 0, WINDOW_BEFORE: 1, WINDOW_AFTER: 2}
    for e in filter_events_for_blackout(events, pair=pair, cfg=cfg):
        try:
            win = event_window(
                e, clock, before_minutes=before, during_minutes=during, after_minutes=after
            )
        except Exception:
            continue
        if win == "none" or win not in windows:
            continue
        when = e.when_dt()
        dist = abs((when - clock).total_seconds()) if when is not None else 1e18
        ranked.append((rank.get(win, 9), dist, e, win))
    if not ranked:
        return False, None, "none"
    ranked.sort(key=lambda row: (row[0], row[1]))
    ev, win = ranked[0][2], ranked[0][3]
    return True, ev, win


def build_blackout_mask(
    index: pd.DatetimeIndex | Iterable,
    events: Iterable[CalendarEvent] | None,
    *,
    pair: str,
    cfg: dict[str, Any] | None = None,
) -> pd.Series:
    """Boolean Series True where new opens should be blocked."""
    idx = pd.DatetimeIndex(index)
    flags: list[bool] = []
    for ts in idx:
        blocked, _ev, _win = is_in_news_blackout(ts, events, pair=pair, cfg=cfg)
        flags.append(blocked)
    return pd.Series(flags, index=idx, dtype=bool, name="news_blackout")


def events_span(
    events: Iterable[CalendarEvent] | None,
) -> tuple[datetime | None, datetime | None]:
    lo: datetime | None = None
    hi: datetime | None = None
    for e in events or []:
        ts = e.when_dt()
        if ts is None:
            continue
        if lo is None or ts < lo:
            lo = ts
        if hi is None or ts > hi:
            hi = ts
    return lo, hi


def events_cover_range(
    events: Iterable[CalendarEvent] | None,
    start: datetime | pd.Timestamp | None,
    end: datetime | pd.Timestamp | None,
    *,
    min_coverage_frac: float = 0.85,
    max_gap_days: float = 14.0,
) -> bool:
    """True only when events look like causal history for [start, end].

    A single this-week dump (span ~7d) never covers a multi-month Replay window.
    """
    lo, hi = events_span(events)
    a = _as_utc(start)
    b = _as_utc(end)
    if lo is None or hi is None or a is None or b is None:
        return False
    if b <= a:
        return False
    if lo > a + timedelta(days=max_gap_days):
        return False
    if hi < b - timedelta(days=max_gap_days):
        return False
    span_ev = (hi - lo).total_seconds()
    span_need = (b - a).total_seconds()
    if span_need <= 0:
        return False
    return (span_ev / span_need) >= float(min_coverage_frac)


def load_events_from_file(path: Path | str | None) -> list[CalendarEvent]:
    """Load a local historic calendar JSON (list or {events:[...]})."""
    if path is None:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        raw = payload.get("events") or payload.get("data") or []
    elif isinstance(payload, list):
        raw = payload
    else:
        return []
    return parse_events(list(raw), min_rank=0)


def load_calendar_cache_events(cfg: dict[str, Any] | None = None) -> list[CalendarEvent]:
    """Disk cache only (no network). May be this-week-only."""
    from forex_lab.calendar import cache_path

    path = cache_path(cfg)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    return parse_events(raw, min_rank=0)


def load_replay_news_events(cfg: dict[str, Any] | None) -> tuple[list[CalendarEvent], str]:
    """Prefer explicit historic file; else calendar cache. Returns (events, source_note)."""
    hist = events_file_path(cfg)
    if hist is not None and hist.is_file():
        return load_events_from_file(hist), f"historic_file:{hist.as_posix()}"
    cached = load_calendar_cache_events(cfg)
    if cached:
        return cached, "calendar_cache_this_week"
    return [], "none"


def apply_replay_news_blackout(
    cfg: dict[str, Any],
    *,
    pair: str = "",
    index: pd.DatetimeIndex | None = None,
) -> dict[str, Any]:
    """Resolve Replay news blackout; attach events + mask metadata onto cfg.

    Does not rewrite default.yaml. Mutates ``cfg["replay"]`` metadata keys.
    Stores usable events under ``cfg["_news_blackout_events"]`` only when active.
    """
    if not isinstance(cfg, dict):
        return cfg
    rc = dict(cfg.get("replay") or {})
    if not news_blackout_enabled(cfg):
        rc["news_blackout_resolved"] = RESOLVED_OFF
        rc["news_blackout_source"] = ""
        rc["news_blackout_n_events"] = 0
        rc["news_blackout_note"] = "news_blackout disabled"
        cfg["replay"] = rc
        cfg.pop("_news_blackout_events", None)
        return cfg

    events, source = load_replay_news_events(cfg)
    before, during, after = news_blackout_minutes(cfg)
    note_mins = f"before={before}m during={during}m after={after}m"

    if not events:
        rc["news_blackout_resolved"] = RESOLVED_EMPTY
        rc["news_blackout_source"] = source
        rc["news_blackout_n_events"] = 0
        rc["news_blackout_note"] = f"no events loaded; gate skipped ({note_mins})"
        cfg["replay"] = rc
        cfg.pop("_news_blackout_events", None)
        return cfg

        # Coverage honesty: desk this-week cache must not gate multi-month Replay.
    # An explicit events_file is treated as caller-supplied PIT — activate when
    # any events load (still filtered to pair / impact below).
    hist = events_file_path(cfg)
    if require_historic_coverage(cfg) and index is not None and len(index) > 0 and hist is None:
        start = index[0]
        end = index[-1]
        if not events_cover_range(events, start, end):
            lo, hi = events_span(events)
            rc["news_blackout_resolved"] = RESOLVED_SKIPPED
            rc["news_blackout_source"] = source
            rc["news_blackout_n_events"] = len(events)
            rc["news_blackout_note"] = (
                f"events span {lo}->{hi} do not cover Replay {start}->{end}; "
                f"refuse non-causal gate ({note_mins}). "
                f"Supply replay.news_blackout.events_file for PIT history."
            )
            cfg["replay"] = rc
            cfg.pop("_news_blackout_events", None)
            return cfg

    pair_u = str(pair or "").upper()
    usable = filter_events_for_blackout(events, pair=pair_u, cfg=cfg) if pair_u else list(events)
    rc["news_blackout_resolved"] = RESOLVED_ACTIVE
    rc["news_blackout_source"] = source
    rc["news_blackout_n_events"] = len(usable)
    rc["news_blackout_note"] = (
        f"active on {len(usable)} events for {pair_u or '?'} ({note_mins}; source={source})"
    )
    cfg["replay"] = rc
    cfg["_news_blackout_events"] = usable
    return cfg


def apply_news_blackout_to_pred(
    pred: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    pair: str,
    hold_code: int | None = None,
) -> pd.DataFrame:
    """Force pred→HOLD on bars inside an active blackout. No-op if not active."""
    if pred is None or pred.empty:
        return pred
    rc = dict((cfg or {}).get("replay") or {})
    if rc.get("news_blackout_resolved") != RESOLVED_ACTIVE:
        return pred
    events = list((cfg or {}).get("_news_blackout_events") or [])
    if not events:
        return pred
    from forex_lab.features import LABEL_MAP

    hold = int(hold_code if hold_code is not None else LABEL_MAP["HOLD"])
    mask = build_blackout_mask(pred.index, events, pair=pair, cfg=cfg)
    aligned = mask.reindex(pred.index).fillna(False).astype(bool)
    if not bool(aligned.any()):
        return pred
    out = pred.copy()
    if "pred" in out.columns:
        out.loc[aligned, "pred"] = hold
    if "side" in out.columns:
        out.loc[aligned, "side"] = "HOLD"
    out["news_blackout"] = aligned.astype(float)
    return out


def news_blackout_report_line(cfg: dict[str, Any] | None) -> str:
    rc = dict((cfg or {}).get("replay") or {})
    resolved = rc.get("news_blackout_resolved") or (
        "on" if news_blackout_enabled(cfg) else "off"
    )
    before, during, after = news_blackout_minutes(cfg)
    src = rc.get("news_blackout_source") or ""
    n = rc.get("news_blackout_n_events")
    note = rc.get("news_blackout_note") or ""
    return (
        f"news_blackout: {resolved} | before/during/after={before}/{during}/{after}m"
        f" | n_events={n if n is not None else '-'} | source={src or '-'} | {note}"
    )
