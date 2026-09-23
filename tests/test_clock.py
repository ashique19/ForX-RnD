"""UI clock: store UTC, display Asia/Dhaka by default."""
from __future__ import annotations

from datetime import datetime, timezone

from forex_lab.clock import (
    DEFAULT_TIMEZONE,
    clock_note,
    fmt_display,
    parse_ts,
    relabel,
    relabel_in_text,
    timezone_name,
    timezone_short_tag,
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
    assert relabel("no headlines") == "no headlines"


def test_relabel_in_text_converts_embedded_utc_and_skips_dhaka():
    raw = "last bar 2026-09-21 07:00 UTC is older than 2× 1h during a liquid session"
    out = relabel_in_text(raw)
    assert "2026-09-21 13:00 Asia/Dhaka" in out
    assert "UTC" not in out
    assert "older than 2× 1h" in out
    already = "last bar 2026-09-21 13:00 Asia/Dhaka is older than 2× 1h"
    assert relabel_in_text(already) == already
    iso = relabel_in_text("event 2026-09-21T10:00:00Z nearby")
    assert "2026-09-21 16:00" in iso
    assert "Asia/Dhaka" in iso


def test_timezone_short_tag_is_bd_for_dhaka():
    assert timezone_short_tag(None) == "BD"
    assert timezone_short_tag({}) == "BD"
    assert timezone_short_tag({"ui": {"timezone": "Asia/Dhaka"}}) == "BD"
    london = timezone_short_tag({"ui": {"timezone": "Europe/London", "timezone_tag": "Europe/London"}})
    assert london == "London"
    assert timezone_short_tag({"ui": {"timezone_short": "DHK"}}) == "DHK"


def test_clock_note_offset_follows_configured_zone():
    note = clock_note(None)
    assert "Asia/Dhaka" in note
    assert "UTC+6" in note
    london = clock_note({"ui": {"timezone": "Europe/London", "timezone_tag": "Europe/London"}})
    assert "Europe/London" in london
    assert "UTC+6" not in london
