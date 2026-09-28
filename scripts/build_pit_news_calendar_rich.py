"""Build a durable point-in-time high-impact USD/EUR news calendar for Replay.

Sources (download-once / offline thereafter where possible):
  1. Fed FOMC meeting calendars (official HTML) -> USD FOMC Rate Decision @ 14:00 ET
  2. Deterministic NFP schedule (first Friday each month @ 08:30 ET) -> USD NFP
  3. Curated ECB monetary-policy press-conference dates -> EUR Main Refinancing Rate @ 13:45 Europe/Berlin
  4. BLS CPI release dates from archived yearly schedules (data/calendars/_raw/bls_sched_YYYY.htm)
     + live 2026 schedule stamps @ 08:30 ET
  5. ALFRED (St. Louis Fed) release-date dumps for Gross Domestic Product (rid=53) and
     Personal Income and Outlays / Core PCE (rid=54) @ 08:30 ET
  6. BEA release_dates.json for remaining near-term GDP / Personal Income stamps

Limitations (documented in output JSON):
  - Release-time blackout only (no surprise / actual-vs-forecast).
  - NFP uses the standard first-Friday rule; rare holiday shifts may be off by a day.
  - ALFRED lists include some revision dates (conservative extra blackouts).
  - EUR HICP / EUR GDP flash omitted (no durable free PIT timestamp feed reachable here).
  - Surprise (actual-forecast) omitted: no free durable PIT consensus archive without look-ahead.
  - ECB list is monetary-policy decision days (press conference), not every GC meeting.
"""
from __future__ import annotations

import json
import re
import urllib.request
from calendar import monthcalendar
from datetime import date, datetime
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
DHAKA = ZoneInfo("Asia/Dhaka")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

ECB_MPD_DATES: list[str] = [
    "2015-01-22", "2015-03-05", "2015-04-15", "2015-06-03",
    "2015-07-16", "2015-09-03", "2015-10-22", "2015-12-03",
    "2016-01-21", "2016-03-10", "2016-04-21", "2016-06-02",
    "2016-07-21", "2016-09-08", "2016-10-20", "2016-12-08",
    "2017-01-19", "2017-03-09", "2017-04-27", "2017-06-08",
    "2017-07-20", "2017-09-07", "2017-10-26", "2017-12-14",
    "2018-01-25", "2018-03-08", "2018-04-26", "2018-06-14",
    "2018-07-26", "2018-09-13", "2018-10-25", "2018-12-13",
    "2019-01-24", "2019-03-07", "2019-04-10", "2019-06-06",
    "2019-07-25", "2019-09-12", "2019-10-24", "2019-12-12",
    "2020-01-23", "2020-03-12", "2020-04-30", "2020-06-04",
    "2020-07-16", "2020-09-10", "2020-10-29", "2020-12-10",
    "2021-01-21", "2021-03-11", "2021-04-22", "2021-06-10",
    "2021-07-22", "2021-09-09", "2021-10-28", "2021-12-16",
    "2022-02-03", "2022-03-10", "2022-04-14", "2022-06-09",
    "2022-07-21", "2022-09-08", "2022-10-27", "2022-12-15",
    "2023-02-02", "2023-03-16", "2023-05-04", "2023-06-15",
    "2023-07-27", "2023-09-14", "2023-10-26", "2023-12-14",
    "2024-01-25", "2024-03-07", "2024-04-11", "2024-06-06",
    "2024-07-18", "2024-09-12", "2024-10-17", "2024-12-12",
    "2025-01-30", "2025-03-06", "2025-04-17", "2025-06-05",
    "2025-07-24", "2025-09-11", "2025-10-30", "2025-12-18",
    "2026-02-05", "2026-03-19", "2026-04-30", "2026-06-11",
    "2026-07-23", "2026-09-10", "2026-10-29", "2026-12-17",
]

CPI_2026_EXTRA: list[str] = [
    "2026-01-13", "2026-02-13", "2026-03-11", "2026-04-10",
    "2026-05-12", "2026-06-10", "2026-07-14", "2026-08-12",
    "2026-09-11", "2026-10-14", "2026-11-10", "2026-12-10",
]


def _http_get(url: str, dest: Path, timeout: float = 30.0) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 500:
        return dest.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    dest.write_text(text, encoding="utf-8")
    return text


def _iso_utc(dt_local: datetime) -> str:
    return dt_local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(title: str, currency: str, when_utc: str, *, highlight: bool = True, source: str = "pit_builder") -> dict:
    return {
        "title": title,
        "currency": currency,
        "country": currency,
        "when": when_utc,
        "impact": "High",
        "forecast": "",
        "previous": "",
        "actual": "",
        "surprise": "",
        "highlight": highlight,
        "source": source,
    }


def first_friday(year: int, month: int) -> date:
    cal = monthcalendar(year, month)
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
    found: set[date] = set()
    for m in re.finditer(r"monetary(20\d{2})(\d{2})(\d{2})a\.htm", html, re.I):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            found.add(date(y, mo, d))
        except ValueError:
            continue
    return sorted(found)


def build_fomc_events(start: date, end: date) -> list[dict]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    dates: set[date] = set()
    current = _http_get(
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        RAW_DIR / "fomc_current.htm",
    )
    dates.update(parse_fomc_statement_dates(current))
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


_MONTH_DAY_RE = re.compile(
    r"(?is)((?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(20\d{2})).{0,220}?Consumer Price Index"
)


def _parse_month_day(mon: str, day: str, year: str) -> date:
    return date(int(year), MONTHS[mon.lower()], int(day))


def parse_bls_cpi_html(html: str) -> list[date]:
    found: list[date] = []
    for m in _MONTH_DAY_RE.finditer(html):
        found.append(_parse_month_day(m.group(2), m.group(3), m.group(4)))
    return found


def build_cpi_events(start: date, end: date) -> list[dict]:
    dates: set[date] = set()
    for y in range(2015, 2026):
        path = RAW_DIR / f"bls_sched_{y}.htm"
        if not path.is_file():
            continue
        html = path.read_text(encoding="utf-8", errors="replace")
        dates.update(parse_bls_cpi_html(html))
    for s in CPI_2026_EXTRA:
        dates.add(date.fromisoformat(s))
    out: list[dict] = []
    for d in sorted(dates):
        if d < start or d > end:
            continue
        local = datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY)
        out.append(_event("CPI", "USD", _iso_utc(local), source="bls_schedule_archive"))
    return out


def _parse_alfred_release_dates(text: str, start: date, end: date) -> list[date]:
    marker = "Release Dates:"
    idx = text.find(marker)
    body = text[idx + len(marker):] if idx >= 0 else text
    out: list[date] = []
    for m in re.finditer(r"\b(20\d{2}-\d{2}-\d{2})\b", body):
        d = date.fromisoformat(m.group(1))
        if start <= d <= end:
            out.append(d)
    seen: set[date] = set()
    uniq: list[date] = []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def fetch_alfred_txt(rid: int, dest: Path) -> str:
    if dest.is_file() and dest.stat().st_size > 500:
        text0 = dest.read_text(encoding="utf-8", errors="replace")
        if not text0.lstrip().startswith("<!"):
            return text0
    url = f"https://alfred.stlouisfed.org/release/downloaddates?rid={rid}&ff=txt"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return text


def build_alfred_events(
    rid: int,
    title: str,
    start: date,
    end: date,
    *,
    cache_name: str,
) -> list[dict]:
    text = fetch_alfred_txt(rid, RAW_DIR / cache_name)
    out: list[dict] = []
    for d in _parse_alfred_release_dates(text, start, end):
        local = datetime(d.year, d.month, d.day, 8, 30, tzinfo=NY)
        out.append(_event(title, "USD", _iso_utc(local), source=f"alfred_rid{rid}"))
    return out


def build_bea_forward_events(start: date, end: date) -> list[dict]:
    path = RAW_DIR / "bea_release_dates.json"
    if not path.is_file():
        try:
            _http_get("https://apps.bea.gov/API/signup/release_dates.json", path, timeout=30)
        except Exception:
            return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    mapping = {
        "Gross Domestic Product": "GDP",
        "Personal Income and Outlays": "Core PCE",
    }
    out: list[dict] = []
    for key, title in mapping.items():
        block = payload.get(key) or {}
        for stamp in block.get("release_dates") or []:
            try:
                dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            d = dt.astimezone(UTC).date()
            if d < start or d > end:
                continue
            when = dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            out.append(_event(title, "USD", when, source="bea_release_dates_json"))
    return out


def main() -> int:
    start = date(2015, 1, 1)
    end = date(2026, 12, 31)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    events: list[dict] = []
    events.extend(build_fomc_events(start, end))
    events.extend(build_nfp_events(start, end))
    events.extend(build_ecb_events(start, end))
    events.extend(build_cpi_events(start, end))
    events.extend(build_alfred_events(53, "GDP", start, end, cache_name="alfred_gdp_dates.txt"))
    events.extend(build_alfred_events(54, "Core PCE", start, end, cache_name="alfred_pce_dates.txt"))
    events.extend(build_bea_forward_events(start, end))

    uniq: dict[tuple, dict] = {}
    for e in events:
        key = (e["when"], e["currency"], e["title"])
        uniq[key] = e
    events = sorted(uniq.values(), key=lambda e: (e["when"], e["currency"], e["title"]))

    by_title: dict[str, int] = {}
    for e in events:
        by_title[e["title"]] = by_title.get(e["title"], 0) + 1

    now = datetime.now(tz=UTC)
    payload = {
        "schema": "forex_lab.pit_calendar.v1",
        "built_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "built_at_dhaka": now.astimezone(DHAKA).strftime("%Y-%m-%d %H:%M Asia/Dhaka"),
        "coverage_start": start.isoformat(),
        "coverage_end": end.isoformat(),
        "n_events": len(events),
        "currencies": sorted({e["currency"] for e in events}),
        "counts_by_title": by_title,
        "sources": [
            "Fed FOMC calendars (federalreserve.gov) - statement date @ 14:00 America/New_York",
            "NFP first-Friday schedule @ 08:30 America/New_York (Unemployment same stamp)",
            "ECB monetary-policy decision days @ 13:45 Europe/Berlin (curated from official calendars)",
            "BLS CPI yearly schedule archives (Wayback) + 2026 live CPI schedule @ 08:30 ET",
            "ALFRED release dates rid=53 GDP + rid=54 Personal Income and Outlays (Core PCE) @ 08:30 ET",
            "BEA apps.bea.gov release_dates.json for near-term GDP / Personal Income stamps",
        ],
        "limitations": [
            "Release-time blackout only - no actual/forecast/surprise fields (omitted: no free durable PIT consensus without look-ahead).",
            "NFP uses first-Friday rule; rare BLS holiday shifts may differ by ~1 day.",
            "ALFRED GDP/PCE lists include some revision dates (conservative extra blackouts).",
            "EUR HICP flash / EUR GDP flash omitted (no durable free PIT timestamp feed reachable here).",
            "ECB list is MPD/press-conference days, not every Governing Council meeting.",
            "Not an official Forex Factory dump; not surprise-aware.",
        ],
        "events": events,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUT_JSON} n_events={len(events)}")
    print("by_title:", by_title)
    by_ccy: dict[str, int] = {}
    for e in events:
        by_ccy[e["currency"]] = by_ccy.get(e["currency"], 0) + 1
    print("by_currency:", by_ccy)
    if events:
        print("span:", events[0]["when"], "->", events[-1]["when"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
