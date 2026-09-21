"""Daily digest builders — Asia/Dhaka windows, paper counts, flips, awareness."""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from forex_lab.calendar import CalendarBundle, CalendarEvent
from forex_lab.digest import (
    HONEST_NOTE,
    build_digest,
    collect_digest,
    day_window,
    format_digest_text,
    in_window,
    paper_counts_for_window,
    persist_digest,
    summarize_awareness,
    summarize_calendar,
    summarize_flips,
    summarize_freshness,
    summarize_paper,
    windows_for,
)
from forex_lab.ui.alerts import Alert, KIND_FLIP, KIND_STALE
from forex_lab.ui.health import build_health_rows


CFG = {
    "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
    "digest": {"when": "both", "calendar_lookahead_hours": 24, "persist_file": "data/digest_latest.json"},
    "broker": {"backend": "paper", "store": "data/paper_broker.json"},
    "calendar": {"cache_ttl_s": 1800},
}


def _utc(y, m, d, hh, mm=0, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_day_window_today_and_yesterday_are_asia_dhaka():
    # 2026-09-21 13:26 UTC = 2026-09-21 19:26 Asia/Dhaka
    now = _utc(2026, 9, 21, 13, 26)
    today = day_window(now, CFG, which="today")
    yday = day_window(now, CFG, which="yesterday")
    assert today.label == "today"
    assert today.local_date == "2026-09-21"
    assert today.timezone == "Asia/Dhaka"
    assert yday.label == "yesterday"
    assert yday.local_date == "2026-09-20"
    # Dhaka midnight 2026-09-21 = 2026-09-20 18:00 UTC
    assert today.start == _utc(2026, 9, 20, 18, 0)
    assert yday.end == today.start
    assert in_window(_utc(2026, 9, 21, 12, 0), today)
    assert not in_window(_utc(2026, 9, 20, 17, 59), today)
    assert in_window(_utc(2026, 9, 20, 18, 0), today)
    assert in_window(_utc(2026, 9, 20, 12, 0), yday)
    assert not in_window(now, yday)
    both = windows_for(now, CFG, which="both")
    assert [w.label for w in both] == ["yesterday", "today"]


def test_paper_counts_right_wrong_in_window():
    now = _utc(2026, 9, 21, 13, 0)
    today = day_window(now, CFG, which="today")
    yday = day_window(now, CFG, which="yesterday")
    closed = [
        {
            "pair": "EURUSD",
            "outcome": "RIGHT",
            "exit_time": "2026-09-21 10:00:00 UTC",
            "entry_time": "2026-09-21 08:00:00 UTC",
        },
        {
            "pair": "EURUSD",
            "outcome": "WRONG",
            "exit_time": "2026-09-21 11:00:00 UTC",
        },
        {
            "pair": "GBPUSD",
            "outcome": "RIGHT",
            "exit_time": "2026-09-20 12:00:00 UTC",  # yesterday Dhaka
        },
        {
            "pair": "USDJPY",
            "outcome": "FLAT",
            "exit_time": "2026-09-21 09:00:00 UTC",
        },
        {
            "pair": "EURUSD",
            "outcome": "TIMEOUT",
            "realized": -0.002,
            "exit_time": "2026-09-21 09:30:00 UTC",
        },
    ]
    open_rows = [{"pair": "EURUSD", "outcome": "PENDING", "entry_time": "2026-09-21 12:00:00 UTC"}]
    t = paper_counts_for_window(closed, today, open_rows)
    assert t["right"] == 1
    assert t["wrong"] == 2  # WRONG + timeout with negative realized
    assert t["flat"] == 1
    assert t["pending"] == 1
    assert t["n_scored"] == 3
    y = paper_counts_for_window(closed, yday)
    assert y["right"] == 1
    assert y["wrong"] == 0
    rolled = summarize_paper(closed, [yday, today], open_rows)
    assert rolled["right"] == 2
    assert rolled["wrong"] == 2
    assert "not a live edge" in rolled["note"]


def test_summarize_flips_filters_to_window_and_ignores_stale():
    now = _utc(2026, 9, 21, 13, 0)
    windows = windows_for(now, CFG, which="both")
    alerts = [
        Alert(
            id="a",
            kind=KIND_FLIP,
            pair="EURUSD",
            timeframe="1h",
            message="EURUSD BUY -> SELL",
            created_at="2026-09-21T10:00:00Z",
            from_value="BUY",
            to_value="SELL",
        ),
        Alert(
            id="b",
            kind=KIND_FLIP,
            pair="GBPUSD",
            timeframe="1h",
            message="GBPUSD HOLD -> BUY",
            created_at="2026-09-19T10:00:00Z",  # before yesterday
            from_value="HOLD",
            to_value="BUY",
        ),
        Alert(
            id="c",
            kind=KIND_STALE,
            pair="EURUSD",
            timeframe="1h",
            message="EURUSD data STALE",
            created_at="2026-09-21T10:05:00Z",
        ),
        {
            "id": "d",
            "kind": KIND_FLIP,
            "pair": "USDJPY",
            "timeframe": "1h",
            "message": "USDJPY SELL -> HOLD",
            "created_at": "2026-09-20T12:00:00Z",
            "from_value": "SELL",
            "to_value": "HOLD",
        },
    ]
    rows = summarize_flips(alerts, windows, CFG)
    pairs = {r["pair"] for r in rows}
    assert pairs == {"EURUSD", "USDJPY"}
    eurusd = next(r for r in rows if r["pair"] == "EURUSD")
    assert eurusd["from"] == "BUY"
    assert eurusd["to"] == "SELL"
    assert eurusd["window"] == "today"
    assert "Asia/Dhaka" in eurusd["when"]
    assert "16:00" in eurusd["when"]  # 10:00 UTC -> 16:00 Dhaka


def test_calendar_ahead_and_freshness_and_awareness():
    now = _utc(2026, 9, 21, 13, 0)
    events = [
        CalendarEvent(
            title="Non-Farm Employment Change",
            currency="USD",
            when="2026-09-21T14:30:00+00:00",
            impact="High",
            highlight=True,
        ),
        CalendarEvent(
            title="Old CPI",
            currency="USD",
            when="2026-09-21T10:00:00+00:00",  # already past
            impact="High",
        ),
        CalendarEvent(
            title="Too far",
            currency="EUR",
            when="2026-09-25T12:00:00+00:00",
            impact="High",
        ),
    ]
    ahead = summarize_calendar(events, now=now, cfg=CFG, lookahead_hours=24)
    assert len(ahead) == 1
    assert ahead[0]["currency"] == "USD"
    assert "Asia/Dhaka" in ahead[0]["when"]
    assert "in " in ahead[0]["countdown"]

    board = [
        SimpleNamespace(
            pair="EURUSD",
            timeframe="1h",
            validity="STALE",
            validity_reason="data stale — refresh required",
            last_bar_at="2026-09-21 10:00 UTC",
            last_fetch_at="2026-09-21 09:00 UTC",
        ),
        SimpleNamespace(
            pair="GBPUSD",
            timeframe="1h",
            validity="OK",
            validity_reason="",
            last_bar_at="2026-09-21 12:00 UTC",
            last_fetch_at="2026-09-21 12:00 UTC",
        ),
    ]
    fresh = summarize_freshness(board, CFG)
    assert fresh[0]["validity"] == "STALE"
    assert "Asia/Dhaka" in fresh[0]["last_bar"]

    health = build_health_rows(board, news_map={}, realtime=False)
    aw = summarize_awareness(health)
    tokens = {i["token"] for i in aw["issues"]}
    assert "STALE" in tokens or "MISSING" in tokens
    assert "FAIL" in tokens or "MISSING" in tokens or aw["n_unhealthy"] >= 1


def test_build_digest_assembles_sections_and_honest_note():
    now = _utc(2026, 9, 21, 13, 0)
    payload = build_digest(
        CFG,
        now=now,
        which="today",
        board_rows=[
            SimpleNamespace(
                pair="EURUSD",
                timeframe="1h",
                validity="OK",
                validity_reason="",
                last_bar_at="2026-09-21 12:00 UTC",
                last_fetch_at="2026-09-21 12:00 UTC",
            )
        ],
        health_rows=build_health_rows(
            [
                SimpleNamespace(
                    pair="EURUSD",
                    timeframe="1h",
                    validity="FAIL",
                    validity_reason="timeout",
                    last_bar_at=None,
                    last_fetch_at=None,
                )
            ]
        ),
        calendar=CalendarBundle(
            events=[
                CalendarEvent(
                    title="FOMC",
                    currency="USD",
                    when="2026-09-21T18:00:00+00:00",
                    impact="High",
                    highlight=True,
                )
            ]
        ),
        alerts=[
            Alert(
                id="x",
                kind=KIND_FLIP,
                pair="EURUSD",
                timeframe="1h",
                message="EURUSD HOLD -> BUY",
                created_at="2026-09-21T12:00:00Z",
                from_value="HOLD",
                to_value="BUY",
            )
        ],
        closed_paper=[
            {"outcome": "RIGHT", "exit_time": "2026-09-21 12:30:00 UTC", "pair": "EURUSD"}
        ],
        errors=["news cache skipped (demo)"],
    )
    assert payload["live_edge"] is False
    assert payload["timezone"] == "Asia/Dhaka"
    assert "Asia/Dhaka" in payload["generated_at"]
    assert payload["paper"]["right"] == 1
    assert payload["flips"][0]["to"] == "BUY"
    assert payload["calendar_ahead"][0]["title"] == "FOMC"
    assert payload["awareness"]["issues"]
    text = format_digest_text(payload)
    assert "Daily digest" in text
    assert "RIGHT" in text
    assert "HOLD -> BUY" in text
    assert "FOMC" in text
    assert "not a live edge" in text.lower()
    assert "\u2192" not in text  # ASCII arrow for Windows consoles
    assert HONEST_NOTE.split("—")[0].strip()[:20] in text or "Research digest" in text


def test_collect_digest_fail_soft_empty_tmp(tmp_path, monkeypatch):
    cfg = {
        **CFG,
        "pairs": {"EURUSD": "EURUSD=X"},
        "interval": "1h",
        "paths": {"data_dir": str(tmp_path / "data"), "models_dir": str(tmp_path / "models")},
        "broker": {"backend": "paper", "store": str(tmp_path / "paper.json")},
        "digest": {"persist_file": str(tmp_path / "digest.json"), "when": "today"},
        "calendar": {"cache_file": str(tmp_path / "cal.json"), "cache_ttl_s": 1800},
        "news": {"cache_file": str(tmp_path / "news.json")},
        "board": {"alerts": {"persist_file": str(tmp_path / "alerts.json")}},
    }
    payload = collect_digest(cfg, now=_utc(2026, 9, 21, 13, 0), persist=True)
    assert payload["live_edge"] is False
    assert payload["paper"]["right"] == 0
    assert payload["flips"] == []
    saved = persist_digest(payload, cfg)
    assert saved is not None and Path(saved).exists()
    dumped = json.loads(Path(saved).read_text(encoding="utf-8"))
    assert dumped["timezone"] == "Asia/Dhaka"


def test_cli_digest_and_retrain_commands_exist():
    from forex_lab.cli import build_parser

    p = build_parser()
    d = p.parse_args(["digest", "--when", "today", "--no-save"])
    assert d.command == "digest"
    assert d.when == "today"
    assert d.no_save is True
    r = p.parse_args(["retrain", "--pair", "EURUSD", "--dry-run"])
    assert r.command == "retrain"
    assert r.pair == "EURUSD"
    assert r.dry_run is True


def test_digest_module_does_not_import_broker():
    tree = ast.parse(Path("forex_lab/digest.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("broker" in name.split(".") for name in imported)
    from forex_lab.broker import BrokerPort

    assert sorted(BrokerPort.__abstractmethods__) == sorted(
        {"submit", "close", "list_positions", "list_fills"}
    )
