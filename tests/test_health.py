"""Awareness / data-health strip v0."""
from __future__ import annotations

from types import SimpleNamespace

from forex_lab.news import Headline, NewsBundle
from forex_lab.ui.health import build_health_rows, health_strip, health_unhealthy


def test_health_rows_ohlcv_and_news():
    row = SimpleNamespace(
        pair="EURUSD",
        timeframe="1h",
        validity="STALE",
        validity_reason="data stale — refresh required",
        last_bar_at="2026-09-21 10:00 UTC",
        last_fetch_at="2026-09-21 09:00 UTC",
        n_bars=100,
        data_source="cached",
    )
    news = {
        "EURUSD": NewsBundle(
            pair="EURUSD",
            bias="mixed",
            bullets=["note"],
            headlines=[Headline("Euro mixed", "https://ex", "now", "T")],
            fetched_at="2026-09-21 09:05 UTC",
        )
    }
    rows = build_health_rows([row], news_map=news, realtime=True, refresh_s=60)
    feeds = [r["Feed"] for r in rows]
    assert feeds[0].startswith("OHLCV EURUSD")
    assert feeds[1].startswith("News EURUSD")
    assert rows[0]["Status"] == "STALE"
    assert "realtime 60s" in rows[0]["Cadence"]
    assert rows[1]["Status"] == "OK"
    fail = NewsBundle(pair="EURUSD", bias="unclear", error="timeout", fetched_at=None)
    rows2 = build_health_rows([row], news_map={"EURUSD": fail}, realtime=False)
    assert rows2[1]["Status"] == "FAIL"
    assert "manual only" in rows2[0]["Cadence"]
    bad = health_unhealthy(rows)
    assert [r["Status"] for r in bad] == ["STALE"]
    assert "OHLCV EURUSD 1h STALE" in health_strip(rows)
    assert "News EURUSD OK" in health_strip(rows)
    closed_only = [{"Feed": "OHLCV EURUSD 1h", "Status": "CLOSED"}]
    assert health_unhealthy(closed_only) == []
    assert health_strip([]) == "No feeds — watchlist empty."
