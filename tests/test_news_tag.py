"""Observe-only news tags: calendar cache, tagging, entry-row fields, backfill and report (no network)."""
import importlib.util
import json
import pathlib
from datetime import datetime, timezone

import pytest

from forex_lab import explore_book as eb
from forex_lab import news_tag as nt
from forex_lab import paper_shadow as ps

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
FF = [
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-14T08:30:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Cash Rate", "country": "AUD", "date": "2026-10-13T23:30:00-04:00", "impact": "High", "forecast": "", "previous": ""},
    {"title": "Housing", "country": "USD", "date": "2026-10-14T10:00:00-04:00", "impact": "Low", "forecast": "", "previous": ""},
    {"title": "IMF Meetings", "country": "All", "date": "2026-10-12T10:00:00-04:00", "impact": "High", "forecast": "", "previous": ""},
]


def _mod(name):
    spec = importlib.util.spec_from_file_location(f"_nt_{name}", SCRIPTS / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


@pytest.fixture()
def cal(tmp_path, monkeypatch):
    d = tmp_path / "calendar"
    monkeypatch.setenv("FORX_CALENDAR_DIR", str(d))
    monkeypatch.setenv("FORX_NEWS_PIT", str(tmp_path / "no_pit.json"))
    monkeypatch.delenv("FORX_NEWS_TAG", raising=False)
    nt.save_snapshot(json.dumps(FF), fetched_at=datetime(2026, 10, 12, tzinfo=timezone.utc), cal_dir=d)
    return d


def test_snapshot_and_tag(cal):
    ev, cov = nt.load_events()
    assert [e["title"] for e in ev] == ["Cash Rate", "CPI m/m"]  # high impact only, no 'All'
    t = nt.tag("EURUSD", datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
    assert t["news_within_min"] == 30.0 and t["news_event"] == "USD CPI m/m" and t["news_coverage"] == "full"
    assert nt.bucket(t) == "near"
    t2 = nt.tag("EURGBP", datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
    assert t2["news_within_min"] is None and nt.bucket(t2) == "far"
    t3 = nt.tag("EURUSD", datetime(2026, 9, 1, tzinfo=timezone.utc))  # before any snapshot
    assert t3["news_coverage"] == "none" and nt.bucket(t3) == "unknown"
    assert nt.tag("AUDUSD", datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc))["news_within_min"] == -30.0


def test_pit_partial_coverage(tmp_path, monkeypatch):
    pit = tmp_path / "pit.json"
    pit.write_text(json.dumps({"coverage_start": "2015-01-01T00:00:00Z", "coverage_end": "2026-12-31T00:00:00Z",
                               "currencies": ["USD", "EUR"],
                               "events": [{"title": "CPI", "currency": "USD", "when": "2026-10-14T12:30:00Z", "impact": "High"}]}),
                   encoding="utf-8")
    monkeypatch.setenv("FORX_CALENDAR_DIR", str(tmp_path / "none"))
    monkeypatch.setenv("FORX_NEWS_PIT", str(pit))
    t = nt.tag("GBPUSD", datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))
    assert t["news_coverage"] == "partial" and t["news_source"] == "pit_usd_eur" and nt.bucket(t) == "near"
    assert nt.bucket(nt.tag("GBPJPY", datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc))) == "unknown"


def test_bad_payload_raises_and_keeps_cache(cal):
    before = sorted(p.name for p in cal.iterdir())
    with pytest.raises(ValueError):
        nt.save_snapshot('{"error": "rate limited"}', cal_dir=cal)
    assert sorted(p.name for p in cal.iterdir()) == before


def test_fetch_script_offline_is_ok(cal, capsys):
    m = _mod("fetch_news_calendar")

    def offline():
        raise OSError("no network")

    assert m.main(fetcher=offline) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is False and (cal / "fetch.log").exists()


def test_entry_rows_tagged_without_behaviour_change(cal, tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "e.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    row = {"pair": "EURUSD", "signal": "HOLD", "raw_signal": "BUY", "confidence": 0.55, "gate_reason": "conf=0.55 < min 0.60",
           "validity": "OK", "status": "ready", "muted": False, "last": 1.1, "stop": 1.098, "target": 1.102,
           "horizon_bars": 8, "last_bar_dhaka": "b", "last_signal_dhaka": "b"}
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    eb.run_pass([row], now=now)
    o = [e for e in eb.load() if e["event"] == "open"][0]
    assert o["news_within_min"] == 30.0 and o["news_event"] == "USD CPI m/m"
    monkeypatch.setenv("FORX_NEWS_TAG", "off")
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "e2.jsonl"))
    eb.run_pass([row], now=now)
    o2 = [e for e in eb.load() if e["event"] == "open"][0]
    assert "news_within_min" not in o2
    strip = lambda d: {k: v for k, v in d.items() if k not in nt.TAG_KEYS + ("ts_utc", "ts_dhaka", "position_id", "fill_id")}
    assert strip(o) == strip(o2)  # identical entry apart from the tag


def test_main_book_open_tagged_close_not(cal):
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    o = ps.build_event("open", pair="EURUSD", side="BUY", entry_mid=1.1, sl=1.09, tp=1.11, now=now)
    c = ps.build_event("close", pair="EURUSD", side="BUY", entry_mid=1.1, exit_mid=1.1, now=now)
    assert o["news_within_min"] == 30.0 and "news_within_min" not in c


def test_backfill_and_report(cal, tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "e.jsonl"))
    for pid, ts, r in (("a", "2026-10-14T12:10:00Z", -1.0), ("b", "2026-10-13T09:00:00Z", 1.0)):
        eb.append({"event": "open", "position_id": pid, "pair": "EURUSD", "side": "BUY", "ts_utc": ts,
                   "entry_mid": 1.1, "sl": 1.098, "assumed_cost_pips": 1.0})
        eb.append({"event": "close", "position_id": pid, "pair": "EURUSD", "side": "BUY", "pnl_r": r, "pnl_price": r * 0.002})
    bf, rep = _mod("news_tag_backfill"), _mod("news_tag_report")
    rows = bf.explore_rows(eb.load())
    assert len(rows) == 2 and all(r["annotation_type"] == "news_tag" and r["exclude_from_explore"] is False for r in rows)
    for r in rows:
        eb.append(r)
    assert bf.explore_rows(eb.load()) == []  # idempotent
    assert eb.sum_r()["sum_r"] == 0.0 and eb.sum_r()["closed_counted"] == 2  # tags never change results
    out = rep.report(eb.load(), [])
    e = out["books"]["explore_reconciled_demo_fill"]
    assert e["near"]["n"] == 1 and e["near"]["sum_r"] == -1.0 and e["far"]["n"] == 1
    assert out["decision_rule"]["met"] is False


def test_main_backfill_annotation_never_excludes(cal, tmp_path, monkeypatch):
    ev = [{"event": "open", "position_id": "p", "fill_id": "f", "pair": "EURUSD", "side": "BUY",
           "ts_utc": "2026-10-14T12:10:00Z", "entry_mid": 1.1, "sl": 1.098}]
    rows = _mod("news_tag_backfill").main_rows(ev)
    assert rows[0]["exclude_from_stage2"] is False
    assert ps.excluded_refs(ev + rows) == (set(), set())
