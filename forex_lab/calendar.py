"""High-impact FX event calendar for decision-support context.

Free source (no API key): unofficial weekly JSON dump of the Forex Factory
calendar, served by NewForexService / faireconomy:

    https://nfs.faireconomy.media/ff_calendar_thisweek.json

This is **not** an official Forex Factory API and has no SLA. We cache locally
(``data/calendar_cache.json``) and fail soft: on network errors reuse a stale
cache if one exists, otherwise return an empty list so the math board still
renders.

Not a trade instruction. Event times can be revised; confirm on an official
source (Fed, BLS, ECB, …) before acting.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from forex_lab.paths import resolve_under_root

DEFAULT_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DEFAULT_TIMEOUT_S = 8.0
DEFAULT_TTL_S = 1800
DEFAULT_MAX_EVENTS = 12
USER_AGENT = "ForX-RnD-research-lab/0.3 (decision-support; not a commercial scraper)"
SOURCE_NAME = "faireconomy_ff_json"

IMPACT_RANK = {
    "holiday": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}

# Names treated as extra-sensitive for flatten / no-new-open advice.
DEFAULT_HIGHLIGHT = (
    "non-farm",
    "nfp",
    "fomc",
    "cpi",
    "core pce",
    "pce price",
    "interest rate",
    "policy rate",
    "cash rate",
    "unemployment",
    "employment change",
    "gdp",
    "nonfarm",
)


@dataclass
class CalendarEvent:
    title: str
    currency: str
    when: str  # ISO-8601 UTC
    impact: str
    forecast: str = ""
    previous: str = ""
    country: str = ""
    highlight: bool = False

    def when_dt(self) -> datetime | None:
        return parse_event_time(self.when)


@dataclass
class CalendarBundle:
    events: list[CalendarEvent] = field(default_factory=list)
    source: str = SOURCE_NAME
    source_url: str = DEFAULT_URL
    fetched_at: str | None = None
    error: str | None = None
    stale_cache: bool = False
    notes: list[str] = field(default_factory=list)


def _cal_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("calendar") or {})


def cache_path(cfg: dict[str, Any] | None = None):
    rel = _cal_cfg(cfg).get("cache_file") or "data/calendar_cache.json"
    return resolve_under_root(rel)


def parse_event_time(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        ts = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                ts = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def pair_currencies(pair: str) -> tuple[str, str]:
    p = str(pair).upper().replace("/", "").replace("-", "").replace("=", "")
    if p.endswith("X") and len(p) == 7:
        p = p[:-1]
    if len(p) >= 6 and p[:6].isalpha():
        return p[:3], p[3:6]
    return p[:3] if p else "", ""


def event_affects_pair(event: CalendarEvent, pair: str) -> bool:
    ccy = str(event.currency or "").upper()
    if not ccy:
        return False
    a, b = pair_currencies(pair)
    return ccy in {a, b}


def events_affecting(events: list[CalendarEvent], pair: str) -> list[CalendarEvent]:
    return [e for e in events if event_affects_pair(e, pair)]


SHORT_EVENT_TITLES = (
    ("non-farm", "NFP"),
    ("nonfarm", "NFP"),
    ("nfp", "NFP"),
    ("fomc", "FOMC"),
    ("core pce", "PCE"),
    ("pce price", "PCE"),
    ("cpi", "CPI"),
    ("interest rate", "Rate"),
    ("policy rate", "Rate"),
    ("cash rate", "Rate"),
    ("unemployment", "Unemp"),
    ("employment change", "Emp"),
    ("gdp", "GDP"),
)


def short_event_title(title: str) -> str:
    """Compact name for the dense Next-event cell (NFP / FOMC / …)."""
    t = str(title or "").lower()
    for needle, short in SHORT_EVENT_TITLES:
        if needle in t:
            return short
    cleaned = str(title or "").strip()
    if not cleaned:
        return "event"
    return cleaned.split()[0][:10]


def next_event_for_pair(
    events: list[CalendarEvent] | None,
    pair: str,
    now: datetime | None = None,
    *,
    grace_minutes: int = 15,
) -> CalendarEvent | None:
    """Soonest upcoming (or just-released) high-impact event that hits this pair."""
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)
    grace = timedelta(minutes=max(0, int(grace_minutes)))
    best: CalendarEvent | None = None
    best_key: tuple[int, float, int] | None = None
    for e in events_affecting(list(events or []), pair):
        ts = e.when_dt()
        if ts is None:
            continue
        delta = ts - clock
        if delta < -grace:
            continue
        upcoming = delta.total_seconds() >= 0
        key = (0 if upcoming else 1, abs(delta.total_seconds()), 0 if e.highlight else 1)
        if best_key is None or key < best_key:
            best, best_key = e, key
    return best


def next_event_label(
    event: CalendarEvent | None,
    now: datetime | None = None,
    *,
    warn: bool = False,
) -> str:
    """Scan cell: ``USD NFP in 42m``. Prefix ⚠ when the pre-event window is live."""
    if event is None:
        return "—"
    cd = countdown_label(event.when_dt(), now)
    ccy = str(event.currency or "").upper()
    short = short_event_title(event.title)
    core = " ".join(p for p in (ccy, short, cd) if p)
    return f"⚠ {core}" if warn else (core or "—")


def is_highlight(title: str, keywords: list[str] | tuple[str, ...] | None = None) -> bool:
    t = str(title or "").lower()
    words = keywords if keywords is not None else DEFAULT_HIGHLIGHT
    return any(str(w).lower() in t for w in words if w)


def impact_rank(impact: str) -> int:
    return IMPACT_RANK.get(str(impact or "").strip().lower(), 0)


def min_impact_rank(cfg: dict[str, Any] | None) -> int:
    raw = str(_cal_cfg(cfg).get("min_impact") or "High").strip().lower()
    return IMPACT_RANK.get(raw, IMPACT_RANK["high"])


def countdown_label(when: datetime | None, now: datetime | None = None) -> str:
    if when is None:
        return "n/a"
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    delta = (when - clock.astimezone(timezone.utc)).total_seconds()
    past = delta < 0
    sec = abs(delta)
    if sec < 45:
        return "now" if not past else "just released"
    if sec < 3600:
        m = max(1, int(round(sec / 60.0)))
        return f"{m}m ago" if past else f"in {m}m"
    if sec < 86400:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        core = f"{h}h {m}m" if m else f"{h}h"
        return f"{core} ago" if past else f"in {core}"
    d = int(sec // 86400)
    h = int((sec % 86400) // 3600)
    core = f"{d}d {h}h" if h else f"{d}d"
    return f"{core} ago" if past else f"in {core}"


def event_window(
    event: CalendarEvent,
    now: datetime | None = None,
    *,
    before_minutes: int = 60,
    during_minutes: int = 15,
    after_minutes: int = 30,
) -> str:
    """Return before | during | after | none relative to the release time."""
    when = event.when_dt()
    if when is None:
        return "none"
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone(timezone.utc)
    before = timedelta(minutes=max(0, int(before_minutes)))
    during = timedelta(minutes=max(0, int(during_minutes)))
    after = timedelta(minutes=max(0, int(after_minutes)))
    if when - before <= clock < when:
        return "before"
    if when <= clock < when + during:
        return "during"
    if when + during <= clock < when + after:
        return "after"
    return "none"


def parse_events(
    raw: list[Any],
    *,
    min_rank: int = IMPACT_RANK["high"],
    highlight_words: list[str] | tuple[str, ...] | None = None,
) -> list[CalendarEvent]:
    out: list[CalendarEvent] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        impact = str(item.get("impact") or "Low").strip() or "Low"
        if impact_rank(impact) < min_rank:
            continue
        ccy = str(item.get("country") or item.get("currency") or "").strip().upper()
        if len(ccy) != 3 or not ccy.isalpha():
            continue
        when = parse_event_time(item.get("date") or item.get("when"))
        if when is None:
            continue
        out.append(
            CalendarEvent(
                title=title,
                currency=ccy,
                when=when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                impact=impact,
                forecast=str(item.get("forecast") or ""),
                previous=str(item.get("previous") or ""),
                country=str(item.get("country") or ccy),
                highlight=is_highlight(title, highlight_words),
            )
        )
    out.sort(key=lambda e: (e.when, -impact_rank(e.impact), e.title))
    return out


def _load_disk_cache(path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_disk_cache(path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        return


def _http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _bundle_from_cache(
    cached: dict[str, Any],
    *,
    error: str | None,
    stale: bool,
    url: str,
) -> CalendarBundle:
    events = []
    for item in cached.get("events") or []:
        try:
            events.append(CalendarEvent(**{k: item[k] for k in item if k in CalendarEvent.__dataclass_fields__}))
        except TypeError:
            continue
    notes = list(cached.get("notes") or [])
    if stale:
        notes = ["Using stale local cache — live calendar fetch failed."] + notes
    return CalendarBundle(
        events=events,
        source=str(cached.get("source") or SOURCE_NAME),
        source_url=url,
        fetched_at=cached.get("fetched_at"),
        error=error,
        stale_cache=stale,
        notes=notes,
    )


def _select_window(
    events: list[CalendarEvent],
    *,
    now: datetime,
    lookback_hours: float,
    lookahead_hours: float,
    limit: int,
) -> list[CalendarEvent]:
    lo = now - timedelta(hours=max(0.0, float(lookback_hours)))
    hi = now + timedelta(hours=max(0.0, float(lookahead_hours)))
    picked: list[CalendarEvent] = []
    for e in events:
        ts = e.when_dt()
        if ts is None or ts < lo or ts > hi:
            continue
        picked.append(e)
        if len(picked) >= limit:
            break
    return picked


def fetch_calendar(
    cfg: dict[str, Any] | None = None,
    *,
    force: bool = False,
    raw_json: str | list | None = None,
    now: datetime | None = None,
) -> CalendarBundle:
    """Fetch or reuse the weekly calendar. ``raw_json`` is for tests (no network)."""
    ccfg = _cal_cfg(cfg)
    url = str(ccfg.get("url") or DEFAULT_URL)
    if ccfg.get("enabled") is False:
        return CalendarBundle(
            source=SOURCE_NAME,
            source_url=url,
            error="disabled",
            notes=["Event calendar disabled in config."],
        )
    timeout = float(ccfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
    ttl = int(ccfg.get("cache_ttl_s") or DEFAULT_TTL_S)
    limit = int(ccfg.get("max_events") or DEFAULT_MAX_EVENTS)
    lookback = float(ccfg.get("lookback_hours") or 6)
    lookahead = float(ccfg.get("lookahead_hours") or 168)
    highlight = ccfg.get("highlight") or list(DEFAULT_HIGHLIGHT)
    min_rank = min_impact_rank(cfg)
    path = cache_path(cfg)
    disk = _load_disk_cache(path)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    now_ts = clock.timestamp() if now is None else clock.timestamp()
    cached = disk if isinstance(disk, dict) and disk.get("events") is not None else None

    if cached and not force and raw_json is None:
        age = now_ts - float(cached.get("ts") or 0)
        if age < ttl:
            bundle = _bundle_from_cache(cached, error=cached.get("error"), stale=False, url=url)
            bundle.events = _select_window(
                bundle.events,
                now=clock,
                lookback_hours=lookback,
                lookahead_hours=lookahead,
                limit=limit,
            )
            return bundle

    error = None
    payload: list[Any] = []
    if raw_json is not None:
        if isinstance(raw_json, str):
            try:
                payload = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                error = f"calendar parse failed ({exc})"
                payload = []
        elif isinstance(raw_json, list):
            payload = raw_json
        else:
            error = "calendar payload is not a list"
    else:
        try:
            text = _http_get(url, timeout)
            payload = json.loads(text)
            if not isinstance(payload, list):
                error = "calendar payload is not a list"
                payload = []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            error = f"calendar fetch failed ({exc})"
            payload = []

    events = parse_events(payload, min_rank=min_rank, highlight_words=highlight)
    fetched_at = clock.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not events and error is None:
        error = "calendar empty after impact filter" if payload else "calendar feed empty or unparseable"

    if error and not events and cached:
        bundle = _bundle_from_cache(cached, error=error, stale=True, url=url)
        bundle.events = _select_window(
            bundle.events,
            now=clock,
            lookback_hours=lookback,
            lookahead_hours=lookahead,
            limit=limit,
        )
        return bundle

    notes = [
        "Unofficial Forex Factory weekly JSON via nfs.faireconomy.media — free, no API key.",
        "Not an official schedule. Fail-soft if offline. Research context only.",
    ]
    bundle = CalendarBundle(
        events=_select_window(
            events,
            now=clock,
            lookback_hours=lookback,
            lookahead_hours=lookahead,
            limit=limit,
        ),
        source=SOURCE_NAME,
        source_url=url,
        fetched_at=fetched_at,
        error=error,
        stale_cache=False,
        notes=notes,
    )
    disk_out = {
        "ts": now_ts,
        "source": SOURCE_NAME,
        "source_url": url,
        "fetched_at": fetched_at,
        "error": error,
        "notes": notes,
        "events": [asdict(e) for e in events],
    }
    _save_disk_cache(path, disk_out)
    return bundle
