"""UI clock: store UTC, display Asia/Dhaka by default."""
from __future__ import annotations

from datetime import datetime, timezone

from forex_lab.clock import (
    DEFAULT_TIMEZONE,
    fmt_display,
    parse_ts,
    relabel,
    timezone_name,
    to_display,
)


def test_default_timezone_is_asia_dhaka():
    assert timezone_name(None) == DEFAULT_TIMEZONE
    assert timezone_name({}) == "Asia/Dhaka"
    assert timezone_name({"ui": {"timezone": "Europe/London"}}) == "Europe/London"


def test_utc_morning_is_afternoon_in_dhaka():
    utc = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)
    text = fmt_display(utc)
    assert text.startswith("2026-09-21 16:00")
    assert "Asia/Dhaka" in text
    local = to_display(utc)
    assert local is not None
    assert local.hour == 16
    assert local.utcoffset().total_seconds() == 6 * 3600


def test_naive_and_utc_label_and_iso_parse():
    naive = parse_ts(datetime(2026, 9, 21, 10, 0, 0))
    labeled = parse_ts("2026-09-21 10:00 UTC")
    iso = parse_ts("2026-09-21T10:00:00Z")
    assert naive == labeled == iso
    assert fmt_display("2026-09-21 10:00 UTC") == "2026-09-21 16:00 Asia/Dhaka"
    assert fmt_display("2026-09-21T10:00:00Z", seconds=True) == "2026-09-21 16:00:00 Asia/Dhaka"


def test_friday_utc_close_rolls_to_saturday_dhaka():
    # Fri 21:00 UTC = Sat 03:00 Asia/Dhaka
    text = fmt_display(datetime(2026, 9, 18, 21, 0, tzinfo=timezone.utc))
    assert text.startswith("2026-09-19 03:00")


def test_relabel_is_idempotent_for_dhaka_labels():
    once = relabel("2026-09-21 10:00 UTC")
    assert once == "2026-09-21 16:00 Asia/Dhaka"
    assert relabel(once) == once
    assert relabel(None) == "n/a"
    assert relabel("n/a") == "n/a"
    assert relabel("2026-09-21 10:00 UTC (100 bars)") == "2026-09-21 16:00 Asia/Dhaka (100 bars)"
    assert "no headlines" == relabel("no headlines")
    once = relabel("2026-09-21 10:00 UTC")
    assert once == "2026-09-21 16:00 Asia/Dhaka"
    assert relabel(once) == once
