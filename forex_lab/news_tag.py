"""Observe-only high-impact news tags for paper entries (never a filter, never a gate).

Source: the public Forex Factory weekly JSON served by faireconomy
(https://nfs.faireconomy.media/ff_calendar_thisweek.json; free, no signup, no
SLA, current week only). ``scripts/fetch_news_calendar.py`` caches one raw
snapshot per week under data/calendar/ (gitignored), so history accumulates
from the first fetch onward. For earlier dates the point-in-time USD/EUR file
data/calendars/pit_high_impact_usd_eur_rich_cpi_pce_gdp.json (official release
schedules) is used; it covers only USD and EUR, so tags for other currencies
before the first FF snapshot are marked partial/none coverage.

Nothing here invents an event: every tag points at an event row from one of
those files, and coverage says which currencies could have been seen.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forex_lab.paths import resolve_under_root

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TAG_VERSION = "news_tag_v1"
WINDOW_MIN = 60
MAX_LOOK_DAYS = 7
_PIT_REL = Path("data") / "calendars" / "pit_high_impact_usd_eur_rich_cpi_pce_gdp.json"
_CACHE: dict[str, Any] = {"key": None, "events": [], "coverage": []}


def calendar_dir() -> Path:
    raw = os.environ.get("FORX_CALENDAR_DIR")
    return Path(raw) if raw else resolve_under_root(Path("data") / "calendar")


def pit_path() -> Path:
    raw = os.environ.get("FORX_NEWS_PIT")
    return Path(raw) if raw else resolve_under_root(_PIT_REL)


def _utc(value: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_snapshot(raw_text: str, *, fetched_at: datetime | None = None, cal_dir: Path | None = None) -> Path:
    """Store one FF weekly JSON verbatim (plus fetch metadata). Raises ValueError on a non-list payload."""
    data = json.loads(raw_text)
    if not isinstance(data, list):
        raise ValueError("calendar payload is not a list")
    times = [t for t in (_utc(e.get("date")) for e in data if isinstance(e, dict)) if t]
    if not times:
        raise ValueError("calendar payload has no dated events")
    start = min(times).replace(hour=0, minute=0, second=0, microsecond=0)
    end = max(times).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    d = cal_dir or calendar_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"ff_week_{start.strftime('%Y-%m-%d')}.json"
    payload = {"source": "forexfactory_weekly_json_via_faireconomy", "url": FF_URL,
               "fetched_at_utc": _iso(fetched_at or datetime.now(timezone.utc)),
               "coverage_start_utc": _iso(start), "coverage_end_utc": _iso(end), "events": data}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def _sources(cal_dir: Path, pit: Path) -> list[Path]:
    out = sorted(cal_dir.glob("ff_week_*.json")) if cal_dir.is_dir() else []
    return out + ([pit] if pit.is_file() else [])


def load_events(cal_dir: Path | None = None, pit: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(high-impact events, coverage segments). Cached on file mtimes."""
    cd, pp = cal_dir or calendar_dir(), pit or pit_path()
    files = _sources(cd, pp)
    key = tuple((str(f), f.stat().st_mtime) for f in files)
    if _CACHE["key"] == key:
        return _CACHE["events"], _CACHE["coverage"]
    events: list[dict] = []
    coverage: list[dict] = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if f.name.startswith("ff_week_"):
            src = "forexfactory_weekly"
            rows = d.get("events") or []
            seg = {"start": _utc(d.get("coverage_start_utc")), "end": _utc(d.get("coverage_end_utc")), "currencies": None,
                   "source": src, "file": f.name}
            for e in rows:
                if not isinstance(e, dict) or str(e.get("impact") or "").lower() != "high":
                    continue
                t, ccy = _utc(e.get("date")), str(e.get("country") or "").upper()
                if t and len(ccy) == 3 and ccy != "ALL":
                    events.append({"when": t, "currency": ccy, "title": str(e.get("title") or ""), "source": src})
        else:
            src = "pit_usd_eur"
            ccys = {str(c).upper() for c in (d.get("currencies") or ["USD", "EUR"])}
            seg = {"start": _utc(d.get("coverage_start")), "end": _utc(d.get("coverage_end")), "currencies": ccys,
                   "source": src, "file": f.name}
            for e in d.get("events") or []:
                if str(e.get("impact") or "").lower() != "high":
                    continue
                t, ccy = _utc(e.get("when")), str(e.get("currency") or "").upper()
                if t and ccy:
                    events.append({"when": t, "currency": ccy, "title": str(e.get("title") or ""), "source": src})
        if seg["start"] and seg["end"]:
            coverage.append(seg)
    events.sort(key=lambda e: e["when"])
    _CACHE.update({"key": key, "events": events, "coverage": coverage})
    return events, coverage


def _covered(ccy: str, lo: datetime, hi: datetime, coverage: list[dict]) -> bool:
    return any(s["start"] <= lo and hi <= s["end"] and (s["currencies"] is None or ccy in s["currencies"]) for s in coverage)


def tag(pair: str, when: datetime, *, events: list[dict] | None = None, coverage: list[dict] | None = None,
        window_min: int = WINDOW_MIN) -> dict[str, Any]:
    """Nearest high-impact event for either currency of ``pair`` around ``when``.

    news_within_min is signed: event time minus entry time (negative = the
    event was before the entry). None when no event within MAX_LOOK_DAYS.
    """
    if events is None or coverage is None:
        events, coverage = load_events()
    p = str(pair or "").upper()
    ccys = (p[:3], p[3:6])
    t = when.astimezone(timezone.utc)
    best = None
    for e in events:
        if e["currency"] not in ccys:
            continue
        dm = (e["when"] - t).total_seconds() / 60.0
        if abs(dm) <= MAX_LOOK_DAYS * 1440 and (best is None or abs(dm) < abs(best[0])):
            best = (dm, e)
    lo, hi = t - timedelta(minutes=window_min), t + timedelta(minutes=window_min)
    cov = [c for c in ccys if _covered(c, lo, hi, coverage)]
    out: dict[str, Any] = {
        "news_tag_version": TAG_VERSION, "news_window_min": window_min,
        "news_within_min": None, "news_event": None, "news_event_utc": None, "news_currency": None, "news_source": None,
        "news_coverage": "full" if len(cov) == 2 else ("partial" if cov else "none"),
        "news_covered_currencies": cov,
    }
    if best:
        dm, e = best
        out.update({"news_within_min": round(dm, 1), "news_event": f"{e['currency']} {e['title']}",
                    "news_event_utc": _iso(e["when"]), "news_currency": e["currency"], "news_source": e["source"]})
    return out


def tag_fields(pair: str, when: datetime | None = None) -> dict[str, Any]:
    """Fail-soft entry-row fields; {} when disabled (FORX_NEWS_TAG=off) or on any error. Observe only."""
    if os.environ.get("FORX_NEWS_TAG", "on").lower() == "off":
        return {}
    try:
        return tag(pair, when or datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 -- a tag must never block an entry
        return {}


def bucket(fields: dict[str, Any] | None, window_min: int = WINDOW_MIN) -> str:
    """near (|news_within_min| <= window) / far (both currencies covered, nothing near) / unknown."""
    f = fields or {}
    w = f.get("news_within_min")
    if isinstance(w, (int, float)) and abs(w) <= window_min:
        return "near"
    return "far" if f.get("news_coverage") == "full" else "unknown"


TAG_KEYS = ("news_tag_version", "news_window_min", "news_within_min", "news_event", "news_event_utc", "news_currency",
            "news_source", "news_coverage", "news_covered_currencies")

__all__ = ["FF_URL", "TAG_KEYS", "TAG_VERSION", "WINDOW_MIN", "bucket", "load_events", "save_snapshot", "tag", "tag_fields"]
