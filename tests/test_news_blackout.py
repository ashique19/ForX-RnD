"""News blackout gate (SERIAL step 6) — unit tests + coverage honesty."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from forex_lab.calendar import CalendarEvent
from forex_lab.features import LABEL_MAP
from forex_lab.news_blackout import (
    RESOLVED_ACTIVE,
    RESOLVED_OFF,
    RESOLVED_SKIPPED,
    apply_news_blackout_to_pred,
    apply_replay_news_blackout,
    build_blackout_mask,
    events_cover_range,
    filter_events_for_blackout,
    is_in_news_blackout,
    news_blackout_minutes,
    news_blackout_report_line,
)


def _evt(
    title: str,
    currency: str,
    when: datetime,
    impact: str = "High",
    highlight: bool = True,
) -> CalendarEvent:
    return CalendarEvent(
        title=title,
        currency=currency,
        when=when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        impact=impact,
        highlight=highlight,
    )


def _cfg(**nb_kwargs):
    base = {
        "calendar": {
            "before_minutes": 60,
            "during_minutes": 15,
            "after_minutes": 30,
            "min_impact": "High",
            "cache_file": "data/calendar_cache.json",
        },
        "replay": {
            "session_gate": "overlap",
            "slippage_pips": 0.2,
            "exit_slippage": True,
            "news_blackout": {
                "enabled": True,
                "before_minutes": 60,
                "during_minutes": 15,
                "after_minutes": 30,
                "require_historic_coverage": True,
                **nb_kwargs,
            },
        },
        "signals": {"min_confidence": 0.55, "sessions": []},
    }
    return base


def test_minutes_prefer_news_blackout_block():
    cfg = _cfg(before_minutes=45, during_minutes=10, after_minutes=20)
    assert news_blackout_minutes(cfg) == (45, 10, 20)


def test_is_in_blackout_before_during_after():
    nfp = _evt("Non-Farm Employment Change", "USD", datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc))
    cfg = _cfg()
    # 30m before
    blocked, ev, win = is_in_news_blackout(
        datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        [nfp],
        pair="EURUSD",
        cfg=cfg,
    )
    assert blocked and win == "before" and ev is not None
    # during (release + 5m)
    blocked, ev, win = is_in_news_blackout(
        datetime(2026, 9, 25, 12, 35, tzinfo=timezone.utc),
        [nfp],
        pair="EURUSD",
        cfg=cfg,
    )
    assert blocked and win == "during"
    # after (release + 20m; during=15 so after window)
    blocked, ev, win = is_in_news_blackout(
        datetime(2026, 9, 25, 12, 50, tzinfo=timezone.utc),
        [nfp],
        pair="EURUSD",
        cfg=cfg,
    )
    assert blocked and win == "after"
    # far away
    blocked, ev, win = is_in_news_blackout(
        datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc),
        [nfp],
        pair="EURUSD",
        cfg=cfg,
    )
    assert not blocked and win == "none"


def test_pair_filter_ignores_unrelated_ccy():
    aud = _evt("Employment Change", "AUD", datetime(2026, 9, 24, 1, 30, tzinfo=timezone.utc))
    cfg = _cfg()
    blocked, _, win = is_in_news_blackout(
        datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc),
        [aud],
        pair="EURUSD",
        cfg=cfg,
    )
    assert not blocked and win == "none"
    assert filter_events_for_blackout([aud], pair="EURUSD", cfg=cfg) == []


def test_explicit_currencies_override():
    aud = _evt("Employment Change", "AUD", datetime(2026, 9, 24, 1, 30, tzinfo=timezone.utc))
    cfg = _cfg(currencies=["AUD"])
    blocked, _, win = is_in_news_blackout(
        datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc),
        [aud],
        pair="EURUSD",
        cfg=cfg,
    )
    assert blocked and win == "before"


def test_low_impact_filtered():
    soft = _evt("Speaks", "USD", datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc), impact="Low")
    cfg = _cfg()
    assert filter_events_for_blackout([soft], pair="EURUSD", cfg=cfg) == []


def test_build_mask_counts_blackout_bars():
    nfp = _evt("NFP", "USD", datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc))
    idx = pd.date_range("2026-09-25 10:00", periods=6, freq="h", tz="UTC")
    # bars: 10,11,12,13,14,15 — blackout before=60m so 11:30–12:30 before,
    # during to 12:45, after to 13:15 → hours 12 and 13 roughly
    cfg = _cfg()
    mask = build_blackout_mask(idx, [nfp], pair="EURUSD", cfg=cfg)
    assert mask.dtype == bool
    assert int(mask.sum()) >= 1
    # 10:00 is >60m before → clear; 15:00 far after → clear
    assert not bool(mask.iloc[0])
    assert not bool(mask.iloc[-1])


def test_events_cover_range_rejects_week_dump_vs_year():
    week = [
        _evt("A", "USD", datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)),
        _evt("B", "EUR", datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)),
    ]
    start = datetime(2024, 9, 24, tzinfo=timezone.utc)
    end = datetime(2026, 9, 25, tzinfo=timezone.utc)
    assert not events_cover_range(week, start, end)


def test_events_cover_range_accepts_matching_span():
    events = [
        _evt("A", "USD", datetime(2024, 9, 25, tzinfo=timezone.utc)),
        _evt("B", "USD", datetime(2025, 3, 1, tzinfo=timezone.utc)),
        _evt("C", "EUR", datetime(2025, 9, 20, tzinfo=timezone.utc)),
    ]
    start = datetime(2024, 9, 24, tzinfo=timezone.utc)
    end = datetime(2025, 9, 24, tzinfo=timezone.utc)
    assert events_cover_range(events, start, end)


def test_apply_skips_without_pit_coverage(tmp_path, monkeypatch):
    # Point cache at a this-week-only file
    cache = tmp_path / "cal.json"
    cache.write_text(
        '{"events":[{"title":"NFP","currency":"USD","when":"2026-09-25T12:30:00Z","impact":"High"}]}',
        encoding="utf-8",
    )
    cfg = _cfg()
    cfg["calendar"]["cache_file"] = str(cache)
    idx = pd.date_range("2024-09-24", periods=100, freq="h", tz="UTC")
    apply_replay_news_blackout(cfg, pair="EURUSD", index=idx)
    assert cfg["replay"]["news_blackout_resolved"] == RESOLVED_SKIPPED
    assert "_news_blackout_events" not in cfg


def test_apply_off():
    cfg = _cfg(enabled=False)
    apply_replay_news_blackout(cfg, pair="EURUSD")
    assert cfg["replay"]["news_blackout_resolved"] == RESOLVED_OFF


def test_apply_active_when_coverage_ok_and_pred_holds(tmp_path):
    # Historic-ish file spanning the tiny index
    hist = tmp_path / "hist.json"
    hist.write_text(
        """{"events":[
          {"title":"NFP","currency":"USD","when":"2026-09-25T12:30:00Z","impact":"High"},
          {"title":"CPI","currency":"EUR","when":"2026-09-25T10:00:00Z","impact":"High"}
        ]}""",
        encoding="utf-8",
    )
    cfg = _cfg(events_file=str(hist), require_historic_coverage=True)
    idx = pd.date_range("2026-09-25 09:00", periods=8, freq="h", tz="UTC")
    apply_replay_news_blackout(cfg, pair="EURUSD", index=idx)
    assert cfg["replay"]["news_blackout_resolved"] == RESOLVED_ACTIVE
    assert len(cfg["_news_blackout_events"]) >= 1

    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"]] * len(idx),
            "confidence": [0.7] * len(idx),
        },
        index=idx,
    )
    out = apply_news_blackout_to_pred(pred, cfg, pair="EURUSD")
    assert (out["pred"] == LABEL_MAP["HOLD"]).any()
    assert (out["pred"] == LABEL_MAP["BUY"]).any()  # some bars outside window still BUY


def test_report_line_mentions_resolved():
    cfg = _cfg(enabled=False)
    apply_replay_news_blackout(cfg)
    line = news_blackout_report_line(cfg)
    assert "news_blackout:" in line
    assert "off" in line
