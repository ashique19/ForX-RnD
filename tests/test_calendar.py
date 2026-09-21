"""Economic calendar parse + cache (no network required)."""
from __future__ import annotations

from datetime import datetime, timezone

from forex_lab.calendar import (
    CalendarEvent,
    countdown_label,
    event_affects_pair,
    event_window,
    events_affecting,
    fetch_calendar,
    parse_events,
)

SAMPLE = [
    {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "date": "2026-09-21T12:30:00-04:00",
        "impact": "High",
        "forecast": "140K",
        "previous": "130K",
    },
    {
        "title": "CPI y/y",
        "country": "USD",
        "date": "2026-09-21T14:00:00-04:00",
        "impact": "High",
        "forecast": "2.5%",
        "previous": "2.6%",
    },
    {
        "title": "FOMC Statement",
        "country": "USD",
        "date": "2026-09-22T14:00:00-04:00",
        "impact": "High",
        "forecast": "",
        "previous": "",
    },
    {
        "title": "German Buba Monthly Report",
        "country": "EUR",
        "date": "2026-09-21T06:00:00-04:00",
        "impact": "Low",
        "forecast": "",
        "previous": "",
    },
    {
        "title": "Bank Holiday",
        "country": "JPY",
        "date": "2026-09-21T19:00:00-04:00",
        "impact": "Holiday",
        "forecast": "",
        "previous": "",
    },
]


def test_parse_keeps_high_impact_and_highlights_nfp_cpi_fomc():
    events = parse_events(SAMPLE, min_rank=3)
    titles = [e.title for e in events]
    assert titles == ["Non-Farm Employment Change", "CPI y/y", "FOMC Statement"]
    assert all(e.currency == "USD" for e in events)
    assert all(e.highlight for e in events)
    assert all(e.when.endswith("Z") for e in events)


def test_pair_matching_and_windows():
    nfp = parse_events(SAMPLE)[0]
    assert event_affects_pair(nfp, "EURUSD")
    assert event_affects_pair(nfp, "USDJPY")
    assert not event_affects_pair(nfp, "EURGBP")
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)  # 12:00 EDT
    # NFP at 12:30-04:00 = 16:30 UTC
    assert event_window(nfp, now, before_minutes=60, during_minutes=15, after_minutes=30) == "before"
    during = datetime(2026, 9, 21, 16, 35, tzinfo=timezone.utc)
    assert event_window(nfp, during, before_minutes=60, during_minutes=15, after_minutes=30) == "during"
    after = datetime(2026, 9, 21, 16, 50, tzinfo=timezone.utc)
    assert event_window(nfp, after, before_minutes=60, during_minutes=15, after_minutes=30) == "after"
    none = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    assert event_window(nfp, none, before_minutes=60, during_minutes=15, after_minutes=30) == "none"


def test_countdown_and_affecting():
    when = datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    assert countdown_label(when, now) == "in 1h"
    assert "ago" in countdown_label(now, when)
    events = parse_events(SAMPLE)
    assert [e.title for e in events_affecting(events, "EURUSD")] == [
        "Non-Farm Employment Change",
        "CPI y/y",
        "FOMC Statement",
    ]
    assert events_affecting(events, "EURGBP") == []


def test_fetch_from_fixture_and_cache(tmp_path):
    cfg = {
        "calendar": {
            "enabled": True,
            "cache_file": str(tmp_path / "cal.json"),
            "cache_ttl_s": 600,
            "min_impact": "High",
            "max_events": 12,
            "lookback_hours": 24,
            "lookahead_hours": 72,
        }
    }
    now = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
    bundle = fetch_calendar(cfg, raw_json=SAMPLE, now=now)
    assert bundle.error is None
    assert len(bundle.events) == 3
    assert bundle.source == "faireconomy_ff_json"
    assert (tmp_path / "cal.json").exists()
    # cache hit: even if http would fail
    bundle2 = fetch_calendar(cfg, now=now)
    assert [e.title for e in bundle2.events] == [e.title for e in bundle.events]


def test_fetch_failure_uses_stale_cache(tmp_path, monkeypatch):
    cfg = {
        "calendar": {
            "enabled": True,
            "cache_file": str(tmp_path / "cal.json"),
            "cache_ttl_s": 1,
            "timeout_s": 0.01,
            "lookback_hours": 24,
            "lookahead_hours": 72,
        }
    }
    now = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
    fetch_calendar(cfg, raw_json=SAMPLE, now=now)

    def _boom(*_a, **_k):
        raise TimeoutError("offline")

    monkeypatch.setattr("forex_lab.calendar._http_get", _boom)
    later = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    bundle = fetch_calendar(cfg, force=True, now=later)
    assert bundle.stale_cache
    assert bundle.error and "offline" in bundle.error.lower() or "failed" in (bundle.error or "").lower()
    assert bundle.events
    assert any("Non-Farm" in e.title for e in bundle.events)


def test_fetch_failure_without_cache_is_empty(tmp_path, monkeypatch):
    cfg = {"calendar": {"enabled": True, "cache_file": str(tmp_path / "empty.json"), "timeout_s": 0.01}}

    def _boom(*_a, **_k):
        raise TimeoutError("offline")

    monkeypatch.setattr("forex_lab.calendar._http_get", _boom)
    bundle = fetch_calendar(cfg, force=True)
    assert bundle.events == []
    assert bundle.error
    assert not bundle.stale_cache


def test_disabled_calendar():
    bundle = fetch_calendar({"calendar": {"enabled": False}})
    assert bundle.error == "disabled"
    assert bundle.events == []


def test_event_window_none_without_timestamp():
    e = CalendarEvent(title="x", currency="USD", when="not-a-date", impact="High")
    assert event_window(e) == "none"


def test_next_event_for_pair_picks_soonest_and_short_title():
    from forex_lab.calendar import next_event_for_pair, next_event_label, short_event_title

    events = parse_events(SAMPLE)
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    nxt = next_event_for_pair(events, "EURUSD", now)
    assert nxt is not None
    assert "Non-Farm" in nxt.title
    assert next_event_for_pair(events, "EURGBP", now) is None
    assert short_event_title("Non-Farm Employment Change") == "NFP"
    assert short_event_title("FOMC Statement") == "FOMC"
    label = next_event_label(nxt, now, warn=True)
    assert label.startswith("⚠")
    assert "USD" in label and "NFP" in label
    # Past the during-grace window → skip to CPI
    after = datetime(2026, 9, 21, 17, 0, tzinfo=timezone.utc)
    nxt2 = next_event_for_pair(events, "EURUSD", after, grace_minutes=15)
    assert nxt2 is not None
    assert "CPI" in nxt2.title
