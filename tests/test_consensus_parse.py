"""Offline parsers: synthetic pages only. No article prose and no network."""
from __future__ import annotations

from api.consensus_fetch import (
    classify_dailyforex_horizon,
    fetch_fxstreet,
    fetch_investing,
    parse_dailyforex_article,
    parse_investing_technical,
)


DAILYFOREX_HTML = """
<article class="content-body">
  <h2>Bearish view</h2>
  <p>Sell the pair and set a take-profit at 1.1400. Add a stop-loss at 1.1550.</p>
  <p>Timeline: 1-2 days.</p>
  <h2>Bullish view</h2>
  <p>Buy the pair and set a take-profit at 1.1700.</p>
</article>
"""

INVESTING_HTML = """
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"state":{"technicalStore":{"analysisDetails":{
  "1h":{"summary":"strong_sell","pivotPoints":{"s1":"1.1467","r1":"1.1473"}},
  "1d":{"summary":"buy","pivotPoints":{"s1":"1.1400","r1":"1.1500"}}
}}}}}}
</script>
"""


def test_dailyforex_title_bias_does_not_scrape_prices():
    html = """
    <html><head><title>USD/JPY Forecast: Upside Bias in Focus</title></head>
    <body><h1>USD/JPY Forecast: Upside Bias in Focus</h1>
    <p>Broker spread 0.1 and a random 157.20 in the chrome.</p></body></html>
    """
    parsed = parse_dailyforex_article(html)
    assert parsed["direction"] == "Buy"
    assert parsed["low"] is None
    assert parsed["high"] is None
    assert "Upside Bias" not in str(parsed["reason"])


def test_dailyforex_heading_is_sell_and_levels_only():
    parsed = parse_dailyforex_article(DAILYFOREX_HTML)
    assert parsed["direction"] == "Sell"
    assert parsed["low"] == 1.14
    assert parsed["high"] == 1.155
    assert parsed["window"] == "1-2 days"
    assert "Sell the pair" not in str(parsed)
    assert classify_dailyforex_horizon("/forex/eurusd-signal", "1-2 days") == "daily"
    assert classify_dailyforex_horizon("/forex/eurusd-signal", "4 hours") == "hourly"


def test_investing_timeframes_map_without_inventing():
    node = parse_investing_technical(INVESTING_HTML)
    assert node["1h"]["summary"] == "strong_sell"
    assert node["1d"]["summary"] == "buy"


def test_investing_and_fxstreet_status_from_fake_http(monkeypatch):
    def _ok(url, timeout=20.0):
        return 200, INVESTING_HTML, ""

    monkeypatch.setattr("api.consensus_fetch.http_get", _ok)
    inv = fetch_investing("EURUSD")
    assert inv["hourly"]["forecaster"]["direction"] == "Sell"
    assert inv["hourly"]["forecaster"]["status"] == "OK"
    assert inv["hourly"]["range"]["low"] == 1.1467
    assert inv["daily"]["forecaster"]["direction"] == "Buy"

    def _blocked(url, timeout=20.0):
        return 403, "<html>Just a moment...</html>", ""

    monkeypatch.setattr("api.consensus_fetch.http_get", _blocked)
    fx = fetch_fxstreet("EURUSD")
    assert fx["hourly"]["forecaster"]["status"] == "ERROR"
    assert fx["hourly"]["forecaster"]["direction"] is None
    assert "blocked" in fx["hourly"]["forecaster"]["reason"]
