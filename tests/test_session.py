"""UTC session classification for the watchlist clock badge."""
from __future__ import annotations

from datetime import datetime

from forex_lab.session import (
    SESSION_CLOSED,
    SESSION_OFF,
    active_sessions,
    classify_session,
    hour_in_window,
    session_windows,
)


def test_default_windows_cover_24h_without_asia_gap_at_sunday_open():
    wins = session_windows()
    assert wins["asia"] == (21.0, 7.0)
    assert wins["london"] == (7.0, 16.0)
    assert wins["ny"] == (13.0, 21.0)
    # Every in-week hour maps to at least one named session.
    for h in range(24):
        labels = active_sessions(float(h), wins)
        assert labels, f"hour {h} UTC should not be a gap in default windows"


def test_hour_in_window_wrap_and_end_exclusive():
    assert hour_in_window(21.0, (21.0, 7.0)) is True
    assert hour_in_window(6.99, (21.0, 7.0)) is True
    assert hour_in_window(7.0, (21.0, 7.0)) is False
    assert hour_in_window(16.0, (7.0, 16.0)) is False
    assert hour_in_window(7.0, (7.0, 16.0)) is True
    assert hour_in_window(21.0, (13.0, 21.0)) is False
    assert hour_in_window(13.0, (13.0, 21.0)) is True


def test_asia_london_ny_and_overlap():
    # Monday 2026-09-21 is in-week.
    asia = classify_session(datetime(2026, 9, 21, 3, 0, 0))
    assert asia.name == "asia"
    assert asia.labels == ["asia"]
    assert asia.badge() == "ASIA"
    assert asia.market_open is True

    london = classify_session(datetime(2026, 9, 21, 10, 0, 0))
    assert london.name == "london"
    assert london.badge() == "LONDON"

    overlap = classify_session(datetime(2026, 9, 21, 14, 30, 0))
    assert overlap.labels == ["london", "ny"]
    assert overlap.name == "london+ny"
    assert overlap.badge() == "LONDON+NY"
    assert "overlap" in overlap.note

    ny = classify_session(datetime(2026, 9, 21, 18, 0, 0))
    assert ny.name == "ny"
    assert ny.badge() == "NY"

    late_asia = classify_session(datetime(2026, 9, 21, 22, 15, 0))
    assert late_asia.name == "asia"
    assert late_asia.badge() == "ASIA"


def test_weekend_and_friday_close_are_closed_not_london():
    sat = classify_session(datetime(2026, 9, 19, 12, 0, 0))
    assert sat.name == SESSION_CLOSED
    assert sat.badge() == "CLOSED"
    assert sat.labels == []
    assert sat.market_open is False

    fri_close = classify_session(datetime(2026, 9, 18, 21, 30, 0))
    assert fri_close.name == SESSION_CLOSED

    sun_morning = classify_session(datetime(2026, 9, 20, 10, 0, 0))
    assert sun_morning.name == SESSION_CLOSED

    sun_open = classify_session(datetime(2026, 9, 20, 21, 30, 0))
    assert sun_open.name == "asia"
    assert sun_open.market_open is True


def test_config_windows_override_and_can_leave_a_gap():
    cfg = {"board": {"sessions": {"asia": [0, 7], "london": [7, 16], "ny": [13, 21]}}}
    wins = session_windows(cfg)
    assert wins["asia"] == (0.0, 7.0)
    late = classify_session(datetime(2026, 9, 21, 22, 0, 0), cfg)
    assert late.name == SESSION_OFF
    assert late.badge() == "OFF"
    assert active_sessions(22.0, wins) == []

    tokyo_only = classify_session(datetime(2026, 9, 21, 2, 0, 0), cfg)
    assert tokyo_only.name == "asia"


def test_invalid_window_keeps_default():
    cfg = {"board": {"sessions": {"asia": [99, 7], "london": "nope", "ny": [13]}}}
    wins = session_windows(cfg)
    assert wins == session_windows(None)
