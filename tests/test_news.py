"""News RSS parse + keyword bias (no network required)."""
from __future__ import annotations

from forex_lab.news import (
    Headline,
    bias_from_headlines,
    fetch_pair_news,
    google_news_rss_url,
    pair_search_query,
)

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Google News</title>
<item>
  <title>Euro rallies as ECB sounds hawkish - Reuters</title>
  <link>https://example.com/a</link>
  <pubDate>Mon, 21 Sep 2026 10:00:00 GMT</pubDate>
  <source>Reuters</source>
</item>
<item>
  <title>Dollar slides after weak US data - FT</title>
  <link>https://example.com/b</link>
  <pubDate>Mon, 21 Sep 2026 09:30:00 GMT</pubDate>
  <source>FT</source>
</item>
<item>
  <title>FX market holiday hours update</title>
  <link>https://example.com/c</link>
  <pubDate>Mon, 21 Sep 2026 08:00:00 GMT</pubDate>
</item>
</channel></rss>
"""


def test_pair_search_query_includes_slash_form():
    q = pair_search_query("EURUSD")
    assert "EURUSD" in q
    assert "EUR/USD" in q
    assert "google" not in google_news_rss_url("EURUSD") or "news.google.com" in google_news_rss_url("EURUSD")


def test_parse_and_bias_from_fixture(tmp_path):
    cfg = {
        "news": {
            "enabled": True,
            "cache_file": str(tmp_path / "news_cache.json"),
            "max_headlines": 8,
            "cache_ttl_s": 60,
        }
    }
    bundle = fetch_pair_news("EURUSD", cfg, xml_text=SAMPLE_RSS)
    assert bundle.error is None
    assert len(bundle.headlines) == 3
    assert bundle.headlines[0].title.startswith("Euro rallies")
    assert bundle.headlines[0].link == "https://example.com/a"
    assert bundle.bias in {"bullish", "bearish", "mixed", "unclear"}
    assert any("Quoted:" in b for b in bundle.bullets)
    # Does not invent a fourth article
    assert all("holiday" in h.title.lower() or "euro" in h.title.lower() or "dollar" in h.title.lower() for h in bundle.headlines)


def test_bias_empty_is_unclear_not_invented():
    bias, bullets = bias_from_headlines("EURUSD", [])
    assert bias == "unclear"
    assert "No headlines" in bullets[0]


def test_bias_bearish_keywords():
    heads = [
        Headline("Euro plunges as dollar soars after hawkish Fed", "http://x", "now", "X"),
        Headline("EURUSD slides on risk-off move", "http://y", "now", "Y"),
    ]
    bias, bullets = bias_from_headlines("EURUSD", heads)
    assert bias in {"bearish", "mixed"}
    assert bullets and bullets[-1].startswith("Quoted:")


def test_fetch_failure_does_not_raise(tmp_path, monkeypatch):
    cfg = {"news": {"enabled": True, "cache_file": str(tmp_path / "n.json"), "timeout_s": 0.01}}

    def _boom(*_a, **_k):
        raise TimeoutError("offline")

    monkeypatch.setattr("forex_lab.news._http_get", _boom)
    bundle = fetch_pair_news("GBPUSD", cfg, force=True)
    assert bundle.headlines == []
    assert bundle.bias == "unclear"
    assert bundle.error
    assert "failed" in bundle.error.lower() or "offline" in bundle.error.lower()


def test_disabled_news():
    bundle = fetch_pair_news("EURUSD", {"news": {"enabled": False}})
    assert bundle.error == "disabled"
    assert bundle.headlines == []


def test_watchlist_news_swallows_per_pair_errors(monkeypatch):
    from forex_lab.news import fetch_watchlist_news

    def _boom(*_a, **_k):
        raise RuntimeError("explode")

    monkeypatch.setattr("forex_lab.news.fetch_pair_news", _boom)
    out = fetch_watchlist_news(["EURUSD", "GBPUSD"], {})
    assert set(out) == {"EURUSD", "GBPUSD"}
    assert all(b.bias == "unclear" for b in out.values())
    assert all(b.error for b in out.values())
    assert all(b.headlines == [] for b in out.values())
