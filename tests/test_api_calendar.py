"""Calendar on the Decision API: cached feed, pair filter, brief caution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.limiter import reset as reset_limiter
from forex_lab.calendar import CalendarBundle, CalendarEvent


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(tmp_path / "watchlist.yaml"))
    monkeypatch.setenv("FORX_ALERT_STATE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "consensus.json"))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    monkeypatch.setenv("FORX_PAPER_STORE", str(tmp_path / "paper.json"))
    monkeypatch.setenv("FORX_REFRESH_MIN_S", "60")
    monkeypatch.setenv("FORX_CALENDAR_CACHE", str(tmp_path / "calendar.json"))
    reset_limiter()
    from api.main import create_app

    return TestClient(create_app())


def _bundle(*events: CalendarEvent, **kwargs) -> CalendarBundle:
    return CalendarBundle(events=list(events), fetched_at="2026-09-23 12:00 UTC", **kwargs)


def _soon(minutes: int) -> str:
    clock = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return clock.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return _client(tmp_path, monkeypatch)


def test_annotate_keeps_stop_and_target_and_marks_caution():
    from api.deskdata import _annotate_suggestion

    base = {
        "stop": 1.1,
        "target": 1.2,
        "stop_text": "1.10000",
        "target_text": "1.20000",
        "duration": "≤ 8 h",
        "scenario": "If scenario changes: hold the research barriers.",
    }
    event = {
        "window": "before",
        "warn": True,
        "label": "⚠ USD NFP in 30m",
        "countdown": "in 30m",
    }
    out = _annotate_suggestion(base, event)
    assert out["stop"] == 1.1
    assert out["target"] == 1.2
    assert out["stop_text"] == "1.10000"
    assert out["target_text"] == "1.20000"
    assert "caution in 30m" in out["duration"]
    assert "Event before" in out["scenario"]
    assert "⚠ USD NFP" in out["scenario"]

    later = _annotate_suggestion(
        base,
        {"window": "none", "warn": False, "label": "USD CPI in 3d", "countdown": "in 3d"},
    )
    assert later["duration"] == base["duration"]
    assert later["scenario"] == base["scenario"]
    assert later["stop"] == base["stop"]


def test_calendar_route_filters_pairs_and_fail_soft(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    soon = _soon(30)
    later = _soon(60 * 24 * 3)
    bundle = _bundle(
        CalendarEvent(
            title="Non-Farm Employment Change",
            currency="USD",
            when=soon,
            impact="High",
            highlight=True,
            forecast="180K",
            previous="142K",
        ),
        CalendarEvent(title="CPI y/y", currency="EUR", when=later, impact="High", highlight=True),
        CalendarEvent(title="BOJ Policy Rate", currency="JPY", when=soon, impact="High"),
        notes=["Unofficial Forex Factory weekly JSON via nfs.faireconomy.media — free, no API key."],
    )
    monkeypatch.setattr("api.deskdata.fetch_calendar", lambda *_a, **_k: bundle)

    res = client.get("/calendar", params={"pairs": "EURUSD,GBPUSD"})
    assert res.status_code == 200
    body = res.json()
    assert body["timezone"] == "Asia/Dhaka"
    assert body["cache_ttl_s"] == 1800
    assert body["stale_cache"] is False
    assert body["error"] is None
    titles = {row["title"]: row for row in body["events"]}
    assert set(titles) == {
        "Non-Farm Employment Change",
        "CPI y/y",
        "BOJ Policy Rate",
    }
    assert "EURUSD" in titles["Non-Farm Employment Change"]["pairs"]
    assert "GBPUSD" in titles["Non-Farm Employment Change"]["pairs"]
    assert titles["Non-Farm Employment Change"]["warn"] is True
    assert titles["Non-Farm Employment Change"]["window"] == "before"
    assert "Asia/Dhaka" in titles["Non-Farm Employment Change"]["when_dhaka"]
    assert titles["Non-Farm Employment Change"]["forecast"] == "180K"
    assert "EURUSD" in titles["CPI y/y"]["pairs"]
    assert titles["CPI y/y"]["warn"] is False
    assert titles["BOJ Policy Rate"]["pairs"] == []

    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr("api.deskdata.fetch_calendar", _boom)
    failed = client.get("/calendar")
    assert failed.status_code == 200
    fail = failed.json()
    assert fail["events"] == []
    assert "unavailable" in (fail["error"] or "").lower()
    assert fail["note"]


def test_stale_cache_note_is_visible(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    bundle = _bundle(
        CalendarEvent(title="CPI y/y", currency="USD", when=_soon(40), impact="High", highlight=True),
        error="calendar fetch failed (offline)",
        stale_cache=True,
        notes=["Using stale local cache — live calendar fetch failed."],
    )
    monkeypatch.setattr("api.deskdata.fetch_calendar", lambda *_a, **_k: bundle)
    res = client.get("/calendar", params={"pairs": "EURUSD"})
    assert res.status_code == 200
    body = res.json()
    assert body["stale_cache"] is True
    assert body["events"]
    assert "stale" in (body["note"] or "").lower()
    assert "EURUSD" in body["events"][0]["pairs"]


def test_brief_and_board_consider_nearby_event(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    soon = _soon(25)
    later = _soon(60 * 24 * 3)
    bundle = _bundle(
        CalendarEvent(
            title="Non-Farm Employment Change",
            currency="USD",
            when=soon,
            impact="High",
            highlight=True,
        ),
        CalendarEvent(title="CPI y/y", currency="EUR", when=later, impact="High", highlight=True),
    )
    monkeypatch.setattr("api.deskdata.fetch_calendar", lambda *_a, **_k: bundle)

    brief = client.get("/brief/EURUSD", params={"tf": "1h"})
    assert brief.status_code == 200
    body = brief.json()
    nxt = body["next_event"]
    assert nxt["currency"] == "USD"
    assert nxt["short_title"] == "NFP"
    assert nxt["warn"] is True
    assert nxt["window"] == "before"
    assert "Asia/Dhaka" in (nxt["when_dhaka"] or "")
    assert "Event before" in body["sub"]
    assert "NFP" in body["sub"]
    hourly = body["hourly"]
    daily = body["daily"]
    assert "Event before" in hourly["scenario"]
    assert "caution" in hourly["duration"]
    assert "Event before" in daily["scenario"]
    assert "caution" in daily["duration"]
    assert "event_stop" not in hourly
    assert isinstance(body["advice"], list)
    if hourly["validity"] not in {"STALE", "MISSING", "ERROR"}:
        assert any(card["action"] == "no_new_opens" for card in body["advice"])
        assert any(card["severity"] in {"warn", "caution"} for card in body["advice"])

    far = client.get("/brief/EURGBP", params={"tf": "1h"})
    assert far.status_code == 200
    other = far.json()
    assert other["next_event"]["title"] == "CPI y/y"
    assert other["next_event"]["warn"] is False
    assert other["next_event"]["window"] == "none"
    assert "Next event" in other["sub"]
    assert "caution" not in other["hourly"]["duration"]
    assert "Event before" not in other["hourly"]["scenario"]

    board = client.get("/board")
    assert board.status_code == 200
    rows = {row["pair"]: row for row in board.json()["rows"]}
    assert "NFP" in rows["EURUSD"]["next_event"]
    assert rows["EURUSD"]["next_event_warn"] is True
    assert board.json()["calendar"]["count"] == 2
    assert any("Non-Farm" in alert["message"] for alert in board.json()["alerts"])


def test_brief_without_events_stays_honest(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("api.deskdata.fetch_calendar", lambda *_a, **_k: CalendarBundle())
    res = client.get("/brief/EURUSD", params={"tf": "1h"})
    assert res.status_code == 200
    body = res.json()
    assert body["next_event"] is None
    assert all(not card.get("event_title") for card in body["advice"])
    assert "No high-impact event" in (body["calendar_note"] or "")
    assert "Event before" not in body["hourly"]["scenario"]
