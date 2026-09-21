"""Daily digest builders — yesterday/today research snapshot.

Summarizes data freshness, signal flips, paper RIGHT/WRONG counts, calendar
events ahead, and Awareness FAIL/STALE sources. Display times are
``ui.timezone`` (default **Asia/Dhaka**). Fail-soft: missing caches become
empty sections, never a crash. Not a live edge. Does not call BrokerPort.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

from forex_lab.calendar import (
    CalendarBundle,
    CalendarEvent,
    cache_path as calendar_cache_path,
    countdown_label,
)
from forex_lab.clock import fmt_display, parse_ts, timezone_name, timezone_tag, zoneinfo_for
from forex_lab.config_loader import load_config
from forex_lab.freshness import assess_ohlcv
from forex_lab.paths import resolve_under_root
from forex_lab.score import normalize_outcome
from forex_lab.ui.alerts import KIND_FLIP, Alert, load_state as load_alert_state
from forex_lab.ui.health import (
    awareness_summary,
    build_health_rows,
    health_unhealthy,
    model_status_map,
    status_token,
)

DEFAULT_WHEN = "both"
DEFAULT_LOOKAHEAD_H = 24.0
DEFAULT_PERSIST = "data/digest_latest.json"
HONEST_NOTE = (
    "Research digest only — not a live edge, not broker truth, not a trade instruction. "
    "Paper RIGHT/WRONG is a local lookback vs cached bars. yfinance is not executable quotes."
)


def digest_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("digest") or {})
    raw.setdefault("enabled", True)
    raw.setdefault("when", DEFAULT_WHEN)
    raw.setdefault("persist_file", DEFAULT_PERSIST)
    raw.setdefault("calendar_lookahead_hours", DEFAULT_LOOKAHEAD_H)
    raw.setdefault("allow_network", False)
    raw.setdefault("fail_soft", True)
    return raw


@dataclass(frozen=True)
class DayWindow:
    """One Asia/Dhaka (or ui.timezone) calendar day, stored as UTC bounds."""

    label: str
    local_date: str
    start: datetime
    end: datetime
    end_inclusive: bool
    timezone: str


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def clock_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return _as_utc(now)


def day_window(
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    which: str = "today",
) -> DayWindow:
    """Yesterday or today in the display timezone, returned as UTC bounds."""
    clock = clock_now(now)
    zone = zoneinfo_for(cfg)
    local = clock.astimezone(zone)
    label = str(which or "today").strip().lower()
    if label not in {"today", "yesterday"}:
        label = "today"
    day = local.date() if label == "today" else (local.date() - timedelta(days=1))
    start_local = datetime(day.year, day.month, day.day, tzinfo=zone)
    next_midnight = start_local + timedelta(days=1)
    start = start_local.astimezone(timezone.utc)
    if label == "today":
        return DayWindow(
            label="today",
            local_date=day.isoformat(),
            start=start,
            end=clock,
            end_inclusive=True,
            timezone=timezone_name(cfg),
        )
    return DayWindow(
        label="yesterday",
        local_date=day.isoformat(),
        start=start,
        end=next_midnight.astimezone(timezone.utc),
        end_inclusive=False,
        timezone=timezone_name(cfg),
    )


def windows_for(
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    which: str | None = None,
) -> list[DayWindow]:
    choice = str(which or digest_cfg(cfg).get("when") or DEFAULT_WHEN).strip().lower()
    if choice == "today":
        return [day_window(now, cfg, which="today")]
    if choice == "yesterday":
        return [day_window(now, cfg, which="yesterday")]
    return [
        day_window(now, cfg, which="yesterday"),
        day_window(now, cfg, which="today"),
    ]


def in_window(value: object, window: DayWindow) -> bool:
    ts = parse_ts(value)
    if ts is None:
        return False
    ts = _as_utc(ts)
    if ts < window.start:
        return False
    if window.end_inclusive:
        return ts <= window.end
    return ts < window.end


def window_to_dict(window: DayWindow, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "label": window.label,
        "local_date": window.local_date,
        "timezone": window.timezone,
        "start": fmt_display(window.start, cfg, seconds=True),
        "end": fmt_display(window.end, cfg, seconds=True),
        "tag": timezone_tag(cfg),
    }


def _alert_kind(item: object) -> str:
    if isinstance(item, Alert):
        return str(item.kind or "")
    if isinstance(item, Mapping):
        return str(item.get("kind") or "")
    return str(getattr(item, "kind", "") or "")


def _alert_when(item: object) -> str:
    if isinstance(item, Alert):
        return str(item.created_at or "")
    if isinstance(item, Mapping):
        return str(item.get("created_at") or "")
    return str(getattr(item, "created_at", "") or "")


def summarize_flips(
    alerts: Iterable[object] | None,
    windows: Sequence[DayWindow],
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """BUY/SELL/HOLD flips whose created_at falls in yesterday/today."""
    out: list[dict[str, Any]] = []
    for item in alerts or []:
        if _alert_kind(item) != KIND_FLIP:
            continue
        when = _alert_when(item)
        matched = next((w for w in windows if in_window(when, w)), None)
        if matched is None:
            continue
        if isinstance(item, Alert):
            pair, frm, to, tf, msg = item.pair, item.from_value, item.to_value, item.timeframe, item.message
        elif isinstance(item, Mapping):
            pair = str(item.get("pair") or "")
            frm = str(item.get("from_value") or "")
            to = str(item.get("to_value") or "")
            tf = str(item.get("timeframe") or "")
            msg = str(item.get("message") or "")
        else:
            pair = str(getattr(item, "pair", "") or "")
            frm = str(getattr(item, "from_value", "") or "")
            to = str(getattr(item, "to_value", "") or "")
            tf = str(getattr(item, "timeframe", "") or "")
            msg = str(getattr(item, "message", "") or "")
        out.append(
            {
                "pair": str(pair).upper(),
                "timeframe": tf,
                "from": frm,
                "to": to,
                "message": msg or f"{pair} {frm} -> {to}",
                "when": fmt_display(when, cfg, seconds=True),
                "window": matched.label,
            }
        )
    out.sort(key=lambda r: (r.get("when") or "", r.get("pair") or ""))
    return out


def _row_ts(row: Mapping[str, Any]) -> object:
    for key in ("exit_time", "entry_time", "time"):
        if row.get(key):
            return row.get(key)
    return None


def paper_counts_for_window(
    closed: Iterable[Mapping[str, Any]] | None,
    window: DayWindow,
    open_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """RIGHT/WRONG (and pending/flat) whose timestamp falls in ``window``."""
    right = wrong = flat = other = 0
    scored_rows: list[dict[str, Any]] = []
    for raw in closed or []:
        row = dict(raw)
        if not in_window(_row_ts(row), window):
            continue
        outcome = normalize_outcome(row)
        rec = {**row, "outcome": outcome}
        if outcome == "RIGHT":
            right += 1
            scored_rows.append(rec)
        elif outcome == "WRONG":
            wrong += 1
            scored_rows.append(rec)
        elif outcome == "FLAT":
            flat += 1
        else:
            other += 1
    pending = 0
    for raw in open_rows or []:
        row = dict(raw)
        if in_window(_row_ts(row) or row.get("entry_time"), window):
            pending += 1
    scored = right + wrong
    return {
        "window": window.label,
        "local_date": window.local_date,
        "right": right,
        "wrong": wrong,
        "flat": flat,
        "pending": pending,
        "other": other,
        "n_scored": scored,
        "hit_rate": None if scored == 0 else right / scored,
        "note": "Paper lookback only — not a live edge.",
    }


def summarize_paper(
    closed: Iterable[Mapping[str, Any]] | None,
    windows: Sequence[DayWindow],
    open_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    by_window = [paper_counts_for_window(closed, w, open_rows) for w in windows]
    right = sum(int(b["right"]) for b in by_window)
    wrong = sum(int(b["wrong"]) for b in by_window)
    scored = right + wrong
    return {
        "by_window": by_window,
        "right": right,
        "wrong": wrong,
        "n_scored": scored,
        "hit_rate": None if scored == 0 else right / scored,
        "note": "Paper RIGHT/WRONG is a local lookback vs cached bars (TP/SL or horizon) — not a live edge.",
    }


def summarize_freshness(
    board_rows: Iterable[Any] | None,
    cfg: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in board_rows or []:
        pair = str(getattr(row, "pair", "") or (row.get("pair") if isinstance(row, Mapping) else "") or "")
        tf = str(
            getattr(row, "timeframe", "")
            or (row.get("timeframe") if isinstance(row, Mapping) else "")
            or ""
        )
        validity = str(
            getattr(row, "validity", "")
            or (row.get("validity") if isinstance(row, Mapping) else "")
            or "MISSING"
        ).upper()
        reason = getattr(row, "validity_reason", None)
        if reason is None and isinstance(row, Mapping):
            reason = row.get("validity_reason") or row.get("reason")
        last_bar = getattr(row, "last_bar_at", None)
        if last_bar is None and isinstance(row, Mapping):
            last_bar = row.get("last_bar_at") or row.get("last_bar")
        last_fetch = getattr(row, "last_fetch_at", None)
        if last_fetch is None and isinstance(row, Mapping):
            last_fetch = row.get("last_fetch_at")
        out.append(
            {
                "pair": pair.upper(),
                "timeframe": tf,
                "validity": validity,
                "reason": str(reason or ""),
                "last_bar": fmt_display(last_bar, cfg, seconds=True) if last_bar else "n/a",
                "last_fetch": fmt_display(last_fetch, cfg, seconds=True) if last_fetch else "n/a",
            }
        )
    return out


def summarize_calendar(
    events: Iterable[CalendarEvent] | None,
    *,
    now: datetime | None = None,
    cfg: dict[str, Any] | None = None,
    lookahead_hours: float | None = None,
) -> list[dict[str, Any]]:
    clock = clock_now(now)
    hours = lookahead_hours
    if hours is None:
        hours = float(digest_cfg(cfg).get("calendar_lookahead_hours") or DEFAULT_LOOKAHEAD_H)
    hi = clock + timedelta(hours=max(0.0, float(hours)))
    out: list[dict[str, Any]] = []
    for event in events or []:
        ts = event.when_dt() if hasattr(event, "when_dt") else parse_ts(getattr(event, "when", None))
        if ts is None:
            continue
        ts = _as_utc(ts)
        if ts < clock or ts > hi:
            continue
        out.append(
            {
                "title": str(getattr(event, "title", "") or ""),
                "currency": str(getattr(event, "currency", "") or "").upper(),
                "impact": str(getattr(event, "impact", "") or ""),
                "when": fmt_display(ts, cfg, seconds=True),
                "countdown": countdown_label(ts, clock),
                "highlight": bool(getattr(event, "highlight", False)),
            }
        )
    out.sort(key=lambda r: r.get("when") or "")
    return out


def summarize_awareness(health_rows: list[dict[str, str]] | None) -> dict[str, Any]:
    rows = list(health_rows or [])
    issues = []
    for row in health_unhealthy(rows):
        issues.append(
            {
                "source": str(row.get("Source") or row.get("Feed") or "?"),
                "status": str(row.get("Status") or ""),
                "token": status_token(row),
                "observing": str(row.get("Observing") or ""),
                "last_ok": str(row.get("Last OK") or "n/a"),
            }
        )
    return {
        "summary": awareness_summary(rows),
        "issues": issues,
        "n_unhealthy": len(issues),
    }


def build_digest(
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    which: str | None = None,
    health_rows: list[dict[str, str]] | None = None,
    board_rows: Iterable[Any] | None = None,
    calendar: CalendarBundle | None = None,
    alerts: Iterable[object] | None = None,
    closed_paper: Iterable[Mapping[str, Any]] | None = None,
    open_paper: Iterable[Mapping[str, Any]] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Pure assembler. Callers pass already-loaded rows (UI or CLI)."""
    cfg = cfg if cfg is not None else {}
    clock = clock_now(now)
    windows = windows_for(clock, cfg, which=which)
    events = list(getattr(calendar, "events", None) or []) if calendar is not None else []
    cal_error = getattr(calendar, "error", None) if calendar is not None else None
    notes = [HONEST_NOTE]
    if calendar is not None:
        notes.extend(str(n) for n in (getattr(calendar, "notes", None) or []) if n)
    payload = {
        "generated_at": fmt_display(clock, cfg, seconds=True),
        "timezone": timezone_name(cfg),
        "timezone_tag": timezone_tag(cfg),
        "windows": [window_to_dict(w, cfg) for w in windows],
        "freshness": summarize_freshness(board_rows, cfg),
        "flips": summarize_flips(alerts, windows, cfg),
        "paper": summarize_paper(closed_paper, windows, open_paper),
        "calendar_ahead": summarize_calendar(events, now=clock, cfg=cfg),
        "awareness": summarize_awareness(health_rows),
        "calendar_error": str(cal_error) if cal_error else None,
        "notes": notes,
        "errors": list(errors or []),
        "honest": True,
        "live_edge": False,
    }
    return payload


def _fmt_rate(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{100.0 * float(value):.0f}%"
    except (TypeError, ValueError):
        return "n/a"


def format_digest_text(payload: Mapping[str, Any]) -> str:
    """ASCII CLI report. Uses -> not arrows so cp1252 consoles stay quiet."""
    from forex_lab.console import ascii_text
    lines: list[str] = []
    gen = payload.get("generated_at") or ""
    tag = payload.get("timezone_tag") or payload.get("timezone") or ""
    lines.append(f"Daily digest  {gen}")
    lines.append("Research only. Not a live edge. Paper BrokerPort unchanged.")
    windows = payload.get("windows") or []
    if windows:
        bits = [f"{w.get('label')} {w.get('local_date')}" for w in windows]
        lines.append("Window: " + ", ".join(bits) + f" ({tag})")
    aw = payload.get("awareness") or {}
    lines.append("")
    lines.append(f"Awareness: {aw.get('summary') or 'no sources'}")
    issues = list(aw.get("issues") or [])
    if issues:
        lines.append("FAIL/STALE/MISSING sources:")
        for row in issues:
            lines.append(f"  - {row.get('source')}  {row.get('status')}")
    else:
        lines.append("No FAIL/STALE/MISSING sources.")
    lines.append("")
    lines.append("Data freshness:")
    fresh = list(payload.get("freshness") or [])
    if not fresh:
        lines.append("  (no watchlist rows)")
    for row in fresh:
        extra = f"  {row.get('reason')}" if row.get("reason") else ""
        lines.append(
            f"  {row.get('pair')} {row.get('timeframe')}  {row.get('validity')}  "
            f"last bar {row.get('last_bar')}{extra}"
        )
    lines.append("")
    lines.append("Signal flips (BUY/SELL/HOLD):")
    flips = list(payload.get("flips") or [])
    if not flips:
        lines.append("  none in window")
    for row in flips:
        lines.append(
            f"  [{row.get('window')}] {row.get('pair')} {row.get('from')} -> {row.get('to')}  "
            f"{row.get('when')}"
        )
    lines.append("")
    paper = payload.get("paper") or {}
    lines.append(
        f"Paper RIGHT/WRONG: {paper.get('right', 0)} RIGHT / {paper.get('wrong', 0)} WRONG"
        f"  hit {_fmt_rate(paper.get('hit_rate'))}  (not a live edge)"
    )
    for block in paper.get("by_window") or []:
        lines.append(
            f"  {block.get('window')} {block.get('local_date')}: "
            f"{block.get('right', 0)} RIGHT / {block.get('wrong', 0)} WRONG"
            f"  pending {block.get('pending', 0)}  flat {block.get('flat', 0)}"
        )
    lines.append("")
    lines.append("Calendar events ahead:")
    ahead = list(payload.get("calendar_ahead") or [])
    if payload.get("calendar_error") and not ahead:
        lines.append(f"  (unavailable: {payload.get('calendar_error')})")
    elif not ahead:
        lines.append("  none in lookahead")
    for row in ahead:
        flag = " *" if row.get("highlight") else ""
        lines.append(
            f"  {row.get('when')}  {row.get('currency')} {row.get('title')}  "
            f"{row.get('countdown')}{flag}"
        )
    errs = list(payload.get("errors") or [])
    if errs:
        lines.append("")
        lines.append("Fail-soft notes:")
        for err in errs:
            lines.append(f"  - {err}")
    lines.append("")
    lines.append(HONEST_NOTE)
    return ascii_text("\n".join(lines) + "\n")


def format_digest_markdown(payload: Mapping[str, Any]) -> str:
    text = format_digest_text(payload)
    # Markdown-friendly: keep the ASCII body inside a fenced block so Streamlit
    # does not eat leading spaces, plus a short header.
    gen = payload.get("generated_at") or ""
    return (
        f"**Daily digest** · {gen}\n\n"
        "Research only. **Not a live edge.** Paper `BrokerPort` unchanged.\n\n"
        f"```\n{text.strip()}\n```\n"
    )


def persist_digest(payload: Mapping[str, Any], cfg: dict[str, Any] | None = None) -> Any:
    rel = digest_cfg(cfg).get("persist_file") or DEFAULT_PERSIST
    path = resolve_under_root(rel)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(payload), indent=2, default=str), encoding="utf-8")
        return path
    except OSError:
        return None


def _load_paper_json(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Read paper journal JSON directly — does not import or call BrokerPort."""
    rel = str((cfg.get("broker") or {}).get("store") or "data/paper_broker.json")
    path = resolve_under_root(rel)
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return [], [], None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return [], [], "paper journal is not a mapping"
        closed = [r for r in (raw.get("closed") or []) if isinstance(r, dict)]
        open_rows = [r for r in (raw.get("positions") or []) if isinstance(r, dict)]
        return closed, open_rows, None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return [], [], f"paper journal unreadable ({exc})"


def load_calendar_disk(cfg: dict[str, Any] | None = None) -> CalendarBundle:
    """Local calendar cache only — no HTTP. Fail-soft if missing/stale."""
    cfg = cfg or {}
    try:
        path = calendar_cache_path(cfg)
        if not path.exists() or path.stat().st_size <= 0:
            return CalendarBundle(
                error="no calendar cache",
                notes=["Daily digest uses the local calendar cache (no network)."],
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return CalendarBundle(error="calendar cache is not a mapping")
        events: list[CalendarEvent] = []
        for item in raw.get("events") or []:
            if not isinstance(item, dict):
                continue
            try:
                keys = {k: item[k] for k in item if k in CalendarEvent.__dataclass_fields__}
                events.append(CalendarEvent(**keys))
            except TypeError:
                continue
        ttl = int((cfg.get("calendar") or {}).get("cache_ttl_s") or 1800)
        try:
            age = datetime.now(timezone.utc).timestamp() - float(raw.get("ts") or 0)
            stale = age >= ttl
        except (TypeError, ValueError):
            stale = True
        return CalendarBundle(
            events=events,
            source=str(raw.get("source") or ""),
            source_url=str(raw.get("source_url") or ""),
            fetched_at=raw.get("fetched_at"),
            error=raw.get("error") if events else (raw.get("error") or None),
            stale_cache=stale,
            notes=["Loaded from local calendar cache (no network)."],
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return CalendarBundle(error=f"calendar cache unreadable ({exc})")


def load_news_disk(pairs: Sequence[str], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Headlines from ``data/news_cache.json`` only — never hits Google News."""
    from forex_lab.news import Headline, NewsBundle, cache_path

    out: dict[str, Any] = {}
    try:
        path = cache_path(cfg)
        if not path.exists():
            return out
        disk = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(disk, dict):
            return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return out
    ttl = int((cfg or {}).get("news", {}).get("cache_ttl_s") or 300)
    now_ts = datetime.now(timezone.utc).timestamp()
    for pair in pairs:
        key = str(pair).upper()
        cached = disk.get(key)
        if not isinstance(cached, dict):
            continue
        try:
            heads = [Headline(**h) for h in cached.get("headlines") or [] if isinstance(h, dict)]
        except TypeError:
            heads = []
        try:
            age = now_ts - float(cached.get("ts") or 0)
            stale = age >= ttl
        except (TypeError, ValueError):
            stale = True
        err = cached.get("error")
        if stale and heads and not err:
            err = "stale news cache (CLI digest, no refresh)"
        out[key] = NewsBundle(
            pair=key,
            bias=str(cached.get("bias") or "unclear"),
            bullets=list(cached.get("bullets") or []),
            headlines=heads,
            source=str(cached.get("source") or "google_news_rss"),
            fetched_at=cached.get("fetched_at"),
            error=err,
        )
    return out


def _watch_pairs(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    interval = str(cfg.get("interval") or "1h")
    try:
        from forex_lab.ui.watchlist import load_watchlist

        wl = load_watchlist(cfg=cfg, create=False)
        lab_iv = wl.lab_interval(cfg)
        if wl.pairs:
            return [(it.pair, it.resolved_interval(lab_iv)) for it in wl.pairs]
    except Exception:
        pass
    pairs = [str(k).upper() for k in (cfg.get("pairs") or {"EURUSD": None})]
    return [(p, interval) for p in pairs] or [("EURUSD", interval)]


def _board_stub(
    pair: str,
    timeframe: str,
    cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> SimpleNamespace:
    from forex_lab.data import csv_mtime_utc, load_cached_ohlcv

    ohlcv = None
    last_fetch = None
    try:
        ohlcv = load_cached_ohlcv(pair, cfg, timeframe)
        last_fetch = csv_mtime_utc(pair, cfg, timeframe)
    except Exception:
        ohlcv = None
    fresh = assess_ohlcv(ohlcv, timeframe, cfg, now=now)
    last_bar = None
    if fresh.last_bar is not None:
        last_bar = fresh.last_bar.replace(tzinfo=timezone.utc)
    fetch_label = None
    if last_fetch is not None:
        fetch_label = last_fetch.replace(tzinfo=timezone.utc) if last_fetch.tzinfo is None else last_fetch
    return SimpleNamespace(
        pair=pair,
        timeframe=timeframe,
        validity=fresh.validity,
        validity_reason=fresh.reason,
        last_bar_at=last_bar,
        last_fetch_at=fetch_label,
        n_bars=0 if ohlcv is None else int(len(ohlcv)),
        data_source="cached" if ohlcv is not None else "missing",
    )


def collect_digest(
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    which: str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """CLI/script entry: load local artifacts, fail-soft, no live broker, no HTTP."""
    cfg = cfg if cfg is not None else load_config()
    errors: list[str] = []
    clock = clock_now(now)
    board_rows: list[Any] = []
    try:
        pairs = _watch_pairs(cfg)
    except Exception as exc:  # noqa: BLE001
        pairs = [("EURUSD", str(cfg.get("interval") or "1h"))]
        errors.append(f"watchlist unreadable ({exc}); defaulted to EURUSD")
    for pair, tf in pairs:
        try:
            board_rows.append(_board_stub(pair, tf, cfg, now=clock))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{pair} freshness failed ({exc})")
            board_rows.append(
                SimpleNamespace(
                    pair=pair,
                    timeframe=tf,
                    validity="ERROR",
                    validity_reason=str(exc),
                    last_bar_at=None,
                    last_fetch_at=None,
                    n_bars=0,
                    data_source="error",
                )
            )
    news_map: dict[str, Any] = {}
    try:
        news_map = load_news_disk([p for p, _ in pairs], cfg)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"news cache skipped ({exc})")
    calendar = CalendarBundle(error="calendar not loaded")
    try:
        calendar = load_calendar_disk(cfg)
    except Exception as exc:  # noqa: BLE001
        calendar = CalendarBundle(error=str(exc))
        errors.append(f"calendar skipped ({exc})")
    try:
        from forex_lab.fred import FredStatus, fred_feed_status

        fred = fred_feed_status(cfg)
        if fred is None:
            fred = FredStatus(enabled=False)
    except Exception as exc:  # noqa: BLE001
        from forex_lab.fred import FredStatus

        fred = FredStatus(enabled=False, error=str(exc))
    try:
        models = model_status_map((p for p, _ in pairs), cfg)
    except Exception as exc:  # noqa: BLE001
        models = None
        errors.append(f"model status skipped ({exc})")
    try:
        health = build_health_rows(
            board_rows,
            news_map=news_map,
            calendar=calendar,
            calendar_ttl_s=int((cfg.get("calendar") or {}).get("cache_ttl_s") or 1800),
            fred=fred,
            models=models,
            realtime=False,
        )
    except Exception as exc:  # noqa: BLE001
        health = []
        errors.append(f"awareness rows failed ({exc})")
    alerts: list[Any] = []
    try:
        alerts = list(load_alert_state(cfg).alerts)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"alert state skipped ({exc})")
    closed, open_rows, paper_err = _load_paper_json(cfg)
    if paper_err:
        errors.append(paper_err)
    payload = build_digest(
        cfg,
        now=clock,
        which=which,
        health_rows=health,
        board_rows=board_rows,
        calendar=calendar,
        alerts=alerts,
        closed_paper=closed,
        open_paper=open_rows,
        errors=errors,
    )
    if persist:
        persist_digest(payload, cfg)
    return payload


__all__ = [
    "HONEST_NOTE",
    "DayWindow",
    "build_digest",
    "collect_digest",
    "day_window",
    "digest_cfg",
    "format_digest_markdown",
    "format_digest_text",
    "in_window",
    "paper_counts_for_window",
    "persist_digest",
    "summarize_awareness",
    "summarize_calendar",
    "summarize_flips",
    "summarize_freshness",
    "summarize_paper",
    "windows_for",
]
