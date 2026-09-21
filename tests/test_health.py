"""Awareness panel: every source the desk fetches or observes."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from forex_lab.calendar import CalendarBundle, CalendarEvent
from forex_lab.clock import relabel
from forex_lab.fred import FredStatus
from forex_lab.freshness import FetchGate
from forex_lab.news import Headline, NewsBundle
from forex_lab.ui.health import (
    AWARENESS_COLS,
    awareness_counts,
    awareness_status_html,
    awareness_summary,
    build_awareness_rows,
    build_health_rows,
    health_strip,
    health_unhealthy,
    model_status_map,
    status_token,
    style_awareness,
)


def _board(
    pair="EURUSD",
    timeframe="1h",
    validity="STALE",
    validity_reason="data stale — refresh required",
    last_bar_at="2026-09-21 10:00 UTC",
    last_fetch_at="2026-09-21 09:00 UTC",
    n_bars=100,
    data_source="cached",
):
    return SimpleNamespace(
        pair=pair,
        timeframe=timeframe,
        validity=validity,
        validity_reason=validity_reason,
        last_bar_at=last_bar_at,
        last_fetch_at=last_fetch_at,
        n_bars=n_bars,
        data_source=data_source,
    )


def _news_ok(pair="EURUSD"):
    return NewsBundle(
        pair=pair,
        bias="mixed",
        bullets=["note"],
        headlines=[Headline("Euro mixed", "https://ex", "now", "T")],
        fetched_at="2026-09-21 09:05 UTC",
    )


def test_awareness_rows_ohlcv_news_and_columns():
    row = _board()
    news = {"EURUSD": _news_ok()}
    rows = build_health_rows([row], news_map=news, realtime=True, refresh_s=60)
    assert build_awareness_rows is build_health_rows
    assert list(rows[0]) == list(AWARENESS_COLS)
    sources = [r["Source"] for r in rows]
    assert sources[0] == "EURUSD 1h OHLCV"
    assert sources[1] == "EURUSD news RSS"
    assert rows[0]["Observing"] == "price"
    assert rows[1]["Observing"] == "headlines"
    assert rows[0]["Status"].startswith("STALE")
    assert "data stale" in rows[0]["Status"]
    assert status_token(rows[0]) == "STALE"
    assert "realtime 60s" in rows[0]["Cadence"]
    assert "Asia/Dhaka" in rows[0]["Last OK"]
    assert rows[1]["Status"] == "OK"
    assert "Asia/Dhaka" in rows[1]["Last OK"]
    fail = NewsBundle(pair="EURUSD", bias="unclear", error="timeout", fetched_at=None)
    rows2 = build_health_rows([row], news_map={"EURUSD": fail}, realtime=False)
    assert status_token(rows2[1]) == "FAIL"
    assert "timeout" in rows2[1]["Status"]
    assert rows2[1]["Last OK"] == "n/a"
    assert "manual only" in rows2[0]["Cadence"]
    bad = health_unhealthy(rows)
    assert [status_token(r) for r in bad] == ["STALE"]
    assert "EURUSD 1h OHLCV STALE" in health_strip(rows)
    assert "EURUSD news RSS OK" in health_strip(rows)
    closed_only = [{"Source": "EURUSD 1h OHLCV", "Status": "CLOSED · market likely closed"}]
    assert health_unhealthy(closed_only) == []
    assert "no sources" in health_strip([]).lower()


def test_stale_and_fail_never_look_ok():
    stale = build_health_rows([_board(validity="STALE", validity_reason="too old")])
    assert status_token(stale[0]) == "STALE"
    assert stale[0]["Status"] != "OK"
    assert not stale[0]["Status"].startswith("OK")
    err = build_health_rows([_board(validity="ERROR", validity_reason="unreadable csv")])
    assert status_token(err[0]) == "FAIL"
    assert "FAIL" in err[0]["Status"]
    missing = build_health_rows(
        [_board(validity="MISSING", validity_reason="no OHLCV cache", last_fetch_at=None)]
    )
    assert status_token(missing[0]) == "MISSING"
    # Cached news after a failed refresh is STALE, never OK.
    stale_news = NewsBundle(
        pair="EURUSD",
        bias="mixed",
        headlines=[Headline("Cached", "https://ex", "earlier", "T")],
        fetched_at="2026-09-21 08:00 UTC",
        error="timeout",
    )
    rows = build_health_rows([_board(validity="OK")], news_map={"EURUSD": stale_news})
    news_row = next(r for r in rows if r["Source"].endswith("news RSS"))
    assert status_token(news_row) == "STALE"
    assert news_row["Status"] != "OK"
    assert "timeout" in news_row["Status"]
    assert news_row["Last OK"] != "n/a"


def test_health_includes_calendar_ok_and_fail():
    cal = CalendarBundle(
        events=[
            CalendarEvent(
                title="Non-Farm Employment Change",
                currency="USD",
                when="2026-09-21T16:30:00Z",
                impact="High",
                highlight=True,
            )
        ],
        fetched_at="2026-09-21 09:00 UTC",
        source="faireconomy_ff_json",
    )
    rows = build_health_rows([_board(validity="OK")], calendar=cal, calendar_ttl_s=1800)
    sources = [r["Source"] for r in rows]
    assert "calendar" in sources
    cal_row = next(r for r in rows if r["Source"] == "calendar")
    assert cal_row["Observing"] == "events"
    assert cal_row["Status"] == "OK"
    assert "Asia/Dhaka" in cal_row["Last OK"]
    fail = CalendarBundle(error="timeout", fetched_at=None)
    rows2 = build_health_rows([_board(validity="OK")], calendar=fail)
    fail_row = next(r for r in rows2 if r["Source"] == "calendar")
    assert status_token(fail_row) == "FAIL"
    assert "timeout" in fail_row["Status"]
    stale = CalendarBundle(
        events=cal.events,
        fetched_at="2026-09-21 09:00 UTC",
        error="cdn down",
        stale_cache=True,
    )
    stale_row = next(
        r
        for r in build_health_rows([_board(validity="OK")], calendar=stale)
        if r["Source"] == "calendar"
    )
    assert status_token(stale_row) == "STALE"
    assert stale_row["Status"] != "OK"


def test_model_file_presence_ok_and_missing(tmp_path):
    present = tmp_path / "EURUSD_xgboost.joblib"
    present.write_bytes(b"joblib-bytes")
    rows = build_health_rows(
        [_board(validity="OK")],
        models={
            "EURUSD": {
                "exists": True,
                "path": present,
                "fetched_at": datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
                "type": "xgboost",
            }
        },
    )
    model_row = next(r for r in rows if "model" in r["Source"])
    assert model_row["Source"] == "EURUSD xgboost model"
    assert model_row["Observing"] == "signals"
    assert model_row["Cadence"] == "manual (Train)"
    assert model_row["Status"] == "OK"
    assert "Asia/Dhaka" in model_row["Last OK"]
    missing_rows = build_health_rows([_board(validity="OK")], models={"EURUSD": {"exists": False}})
    miss = next(r for r in missing_rows if "model" in r["Source"])
    assert status_token(miss) == "MISSING"
    assert miss["Status"] != "OK"
    assert miss["Last OK"] == "n/a"
    assert miss in health_unhealthy(missing_rows)


def test_model_status_map_reads_disk(tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    joblib = models_dir / "EURUSD_xgboost.joblib"
    joblib.write_bytes(b"x")
    cfg = {"paths": {"models_dir": str(models_dir)}, "model": {"type": "xgboost"}}
    found = model_status_map(["EURUSD", "GBPUSD"], cfg)
    assert found["EURUSD"]["exists"] is True
    assert found["GBPUSD"]["exists"] is False
    rows = build_health_rows(
        [_board("EURUSD"), _board("GBPUSD", validity="OK")],
        models=found,
    )
    by_src = {r["Source"]: r for r in rows}
    assert by_src["EURUSD xgboost model"]["Status"] == "OK"
    assert status_token(by_src["GBPUSD xgboost model"]) == "MISSING"


def test_fred_optional_pack_off_and_fail():
    off = build_health_rows([_board(validity="OK")], fred=FredStatus(enabled=False))
    fred = next(r for r in off if r["Source"] == "FRED")
    assert fred["Observing"] == "macro"
    assert fred["Status"] == "OFF"
    assert fred not in health_unhealthy(off)
    fail = FredStatus(enabled=True, error="no cache", series=[], source="missing")
    fail_rows = build_health_rows([_board(validity="OK")], fred=fail)
    fred_fail = next(r for r in fail_rows if r["Source"] == "FRED")
    assert status_token(fred_fail) in {"FAIL", "MISSING"}
    assert fred_fail["Status"] != "OK"
    stale = FredStatus(
        enabled=True,
        series=["DTWEXBGS"],
        source="cache",
        error="partial",
        fetched_at="2026-09-21 09:00 UTC",
    )
    stale_row = next(
        r for r in build_health_rows([_board(validity="OK")], fred=stale) if r["Source"] == "FRED"
    )
    assert status_token(stale_row) == "STALE"
    assert stale_row["Status"] != "OK"


def test_yfinance_gate_fail_is_obvious():
    gate = FetchGate(until=9e12, last_error="429 too many requests", last_yf_ok={"EURUSD": 1.0})
    rows = build_health_rows([_board(validity="OK")], gate=gate)
    assert rows[0]["Source"] == "yfinance gate"
    assert status_token(rows[0]) == "FAIL"
    assert "429" in rows[0]["Status"]


def test_awareness_summary_and_html_tone():
    ok = [{"Source": "a", "Status": "OK"}]
    assert awareness_summary(ok) == "1 OK"
    assert "ok" in awareness_status_html(ok)
    stale = [{"Source": "a", "Status": "STALE · old"}]
    assert "1 STALE" in awareness_summary(stale)
    assert 'class="warn"' in awareness_status_html(stale)
    fail = [{"Source": "a", "Status": "FAIL · timeout"}]
    assert 'class="bad"' in awareness_status_html(fail)
    assert awareness_counts(stale + fail)["STALE"] == 1
    assert awareness_counts(stale + fail)["FAIL"] == 1


def test_style_awareness_colors_status():
    import pandas as pd

    from forex_lab.ui.theme import BUY, SELL, WARN, awareness_status_style

    df = pd.DataFrame(
        [
            {
                "Source": "EURUSD 1h OHLCV",
                "Observing": "price",
                "Cadence": "manual only (Realtime off)",
                "Last OK": "n/a",
                "Status": "STALE · data stale — refresh required",
            },
            {
                "Source": "EURUSD news RSS",
                "Observing": "headlines",
                "Cadence": "Google News RSS · cache TTL 300s",
                "Last OK": "n/a",
                "Status": "FAIL · timeout",
            },
            {
                "Source": "calendar",
                "Observing": "events",
                "Cadence": "weekly JSON · cache TTL 1800s",
                "Last OK": relabel("2026-09-21 09:00 UTC", seconds=True),
                "Status": "OK",
            },
        ]
    )
    styled = style_awareness(df)
    html = styled.to_html() if hasattr(styled, "to_html") else ""
    if "background-color" in html:
        assert WARN.lower() in html.lower() or "f5a524" in html.lower()
        assert SELL.lower() in html.lower() or "ea3943" in html.lower()
    assert WARN in awareness_status_style("STALE · x") or "f5a524" in awareness_status_style(
        "STALE · x"
    )
    assert SELL in awareness_status_style("FAIL · x")
    assert BUY in awareness_status_style("OK")


def test_awareness_does_not_import_broker():
    import ast
    from pathlib import Path

    from forex_lab.broker import BrokerPort

    tree = ast.parse(Path("forex_lab/ui/health.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("broker" in name.split(".") for name in imported)
    assert sorted(BrokerPort.__abstractmethods__) == sorted(
        {"submit", "close", "list_positions", "list_fills"}
    )


def test_last_ok_uses_asia_dhaka():
    rows = build_health_rows([_board(validity="OK")])
    last = rows[0]["Last OK"]
    assert "Asia/Dhaka" in last
    # 09:00 UTC → 15:00 Dhaka
    assert "15:00" in last
    assert "UTC" not in last
