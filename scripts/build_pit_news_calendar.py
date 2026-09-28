"""Build a durable point-in-time high-impact USD/EUR news calendar for Replay.

Sources (download-once, offline thereafter):
  1. Fed FOMC meeting calendars (official HTML) -> USD FOMC Rate Decision @ 14:00 ET
  2. Deterministic NFP schedule (first Friday each month @ 08:30 ET) -> USD NFP
  3. Curated ECB monetary-policy press-conference dates (official calendars /
     key-rate decision days) -> EUR Main Refinancing Rate @ 13:45 Europe/Berlin

Limitations (documented in output JSON):
  - Release-time blackout only (no surprise / actual-vs-forecast).
  - NFP uses the standard first-Friday rule; rare holiday shifts may be off by a day.
  - CPI / Core PCE / GDP not included (no free durable PIT timestamp feed here).
  - ECB list is monetary-policy decision days (press conference), not every GC meeting.
  - Desk this-week FF dump remains for live UI; this file is Replay PIT history.
"""
from __future__ import annotations

import json
import re
import urllib.request
from calendar import monthcalendar
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "calendars"
OUT_JSON = OUT_DIR / "pit_high_impact_usd_eur.json"
RAW_DIR = OUT_DIR / "_raw"
UA = "ForX-RnD-research-lab/0.3 (PIT calendar build; research only)"

NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

# ECB monetary-policy decision / press-conference days (EUR).
# Compiled from ECB Governing Council calendars + key-rate decision history.
# Times: rate decision typically 13:45 CET/CEST.
ECB_MPD_DATES: list[str] = [
    # 2015
    "2015-01-22", "2015-03-05", "2015-04-15", "2015-06-03",
    "2015-07-16", "2015-09-03", "2015-10-22", "2015-12-03",
    # 2016
    "2016-01-21", "2016-03-10", "2016-04-21", "2016-06-02",
    "2016-07-21", "2016-09-08", "2016-10-20", "2016-12-08",
    # 2017
    "2017-01-19", "2017-03-09", "2017-04-27", "2017-06-08",
    "2017-07-20", "2017-09-07", "2017-10-26", "2017-12-14",
    # 2018
    "2018-01-25", "2018-03-08", "2018-04-26", "2018-06-14",
    "2018-07-26", "2018-09-13", "2018-10-25", "2018-12-13",
    # 2019
    "2019-01-24", "2019-03-07", "2019-04-10", "2019-06-06",
    "2019-07-25", "2019-09-12", "2019-10-24", "2019-12-12",
    # 2020
    "2020-01-23", "2020-03-12", "2020-04-30", "2020-06-04",
    "2020-07-16", "2020-09-10", "2020-10-29", "2020-12-10",
    # 2021
    "2021-01-21", "2021-03-11", "2021-04-22", "2021-06-10",
    "2021-07-22", "2021-09-09", "2021-10-28", "2021-12-16",
    # 2022
    "2022-02-03", "2022-03-10", "2022-04-14", "2022-06-09",
    "2022-07-21", "2022-09-08", "2022-10-27", "2022-12-15",
    # 2023
    "2023-02-02", "2023-03-16", "2023-05-04", "2023-06-15",
    "2023-07-27", "2023-09-14", "2023-10-26", "2023-12-14",
    # 2024
    "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
    "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
    # 2025
    "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
    "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
    # 2026 (from live ECB calendar; Day-2 / press-conference day)
    "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-11",
    "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17",
]


def _http_get(url: str, dest: Path, timeout: float = 30.0) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 500:
        return dest.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    dest.write_text(text, encoding="utf-8")
    return text


def _iso_utc(dt_local: datetime) -> str:
    return dt_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(title: str, currency: str, when_utc: str, *, highlight: bool = True) -> dict:
    return {
        "title": title,
        "currency": currency,
        "country": currency,
        "when": when_utc,
        "impact": "High",
        "forecast": "",
        "previous": "",
        "highlight": highlight,
        "source": "pit_builder",
    }


def first_friday(year: int, month: int) -> date:
    cal = monthcalendar(year, month)
    # monthcalendar weeks start Monday; Friday is index 4
    for week in cal:
        if week[4] != 0:
            return date(year, month, week[4])
    raise RuntimeError(f"no Friday in {year}-{month}")


def build_nfp_events(start: date, end: date) -> list[dict]:
    out: list[dict] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = first_friday(y, m)
        if start <= d <= end:
            local = datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY)
            out.append(_event("Non-Farm Employment Change", "USD", _iso_utc(local)))
            out.append(_event("Unemployment Rate", "USD", _iso_utc(local), highlight=True))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def parse_fomc_statement_dates(html: str) -> list[date]:
    """Extract statement dates from Fed monetaryYYYYMMDDa.htm links."""
    found: set[date] = set()
    for m in re.finditer(r"monetary(20\d{2})(\d{2})(\d{2})a\.htm", html, re.I):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.add(date(y, mo, d))
        except ValueError:
            continue
    # Historical pages sometimes use FOMC20150128 in tealbook links only —
    # also catch monetary20150128 without requiring trailing a.htm already did.
    return sorted(found)


def build_fomc_events(start: date, end: date) -> list[dict]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dates: set[date] = set()
    # Current multi-year page (covers ~2021+)
    current = _http_get(
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        RAW_DIR / "fomc_current.htm",
    )
    dates.update(parse_fomc_statement_dates(current))
    # Historical yearly pages for 2015-2020
    for y in range(2015, 2021):
        html = _http_get(
            f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm",
            RAW_DIR / f"fomc_{y}.htm",
        )
        dates.update(parse_fomc_statement_dates(html))
    out: list[dict] = []
    for d in sorted(dates):
        if d < start or d > end:
            continue
        local = datetime(d.year, d.month, d.day, 14, 0, tzinfo=NY)
        out.append(_event("FOMC Rate Decision", "USD", _iso_utc(local)))
    return out


def build_ecb_events(start: date, end: date) -> list[dict]:
    out: list[dict] = []
    for s in ECB_MPD_DATES:
        d = date.fromisoformat(s)
        if d < start or d > end:
            continue
        local = datetime(d.year, d.month, d.day, 13, 45, tzinfo=BERLIN)
        out.append(_event("Main Refinancing Rate", "EUR", _iso_utc(local)))
    return out


def main() -> int:
    start = date(2015, 1, 1)
    end = date(2026, 9, 25)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    events.extend(build_fomc_events(start, end))
    events.extend(build_nfp_events(start, end))
    events.extend(build_ecb_events(start, end))

    # Dedupe by (when, currency, title)
    uniq: dict[tuple, dict] = {}
    for e in events:
        key = (e["when"], e["currency"], e["title"])
        uniq[key] = e
    events = sorted(uniq.values(), key=lambda e: (e["when"], e["currency"], e["title"]))

    now = datetime.now(tz=UTC)
    payload = {
        "schema": "forex_lab.pit_calendar.v1",
        "built_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "built_at_dhaka": now.astimezone(ZoneInfo("Asia/Dhaka")).strftime(
            "%Y-%m-%d %H:%M Asia/Dhaka"
        ),
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "n_events": len(events),
        "currencies": sorted({e["currency"] for e in events}),
        "sources": [
            "Fed FOMC calendars (federalreserve.gov) — statement date @ 14:00 America/New_York",
            "NFP first-Friday schedule @ 08:30 America/New_York (Unemployment same stamp)",
            "ECB monetary-policy decision days @ 13:45 Europe/Berlin (curated from official calendars)",
        ],
        "limitations": [
            "Release-time blackout only — no actual/forecast/surprise fields.",
            "NFP uses first-Friday rule; rare BLS holiday shifts may differ by ~1 day.",
            "CPI / Core PCE / GDP / ADP omitted (no free durable PIT timestamp feed used).",
            "ECB list is MPD/press-conference days, not every Governing Council meeting.",
            "Not an official Forex Factory dump; not surprise-aware.",
        ],
        "events": events,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUT_JSON} n_events={len(events)}")
    by_ccy: dict[str, int] = {}
    for e in events:
        by_ccy[e["currency"]] = by_ccy.get(e["currency"], 0) + 1
    print("by_currency:", by_ccy)
    print("span:", events[0]["when"], "->", events[-1]["when"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
