"""Pair spelling and consensus math. Synthetic snippets only — no saved pages."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from api.consensus import ensure_consensus, read_consensus, reset_consensus_state
from api.consensus_registry import (
    aggregate_consensus,
    pair_forms,
    parse_actionforex_feed,
    parse_fxempire_payload,
    parse_fxssi_signals,
    parse_ig_sentiment,
    parse_mataf_pivots,
    stocktwits_lean,
)
from forex_lab.ui.watchlist import WatchlistError


def test_pair_forms_collapse_usdjpy_spellings():
    expected = "USDJPY"
    for raw in ("USDJPY", "usdjpy", "USD/JPY", "usd-jpy", "USD_JPY", "USDJPY=X", "USD JPY"):
        forms = pair_forms(raw)
        assert forms["pair"] == expected
        assert forms["slash"] == "USD/JPY"
        assert forms["slug"] == "usd-jpy"
        assert forms["base"] == "USD"
        assert forms["quote"] == "JPY"
    eurusd = pair_forms("eur/usd")
    assert eurusd["pair"] == "EURUSD"
    assert eurusd["slug"] == "eur-usd"
    with pytest.raises(WatchlistError):
        pair_forms("USD")
    with pytest.raises(WatchlistError):
        pair_forms("USDJPYY")


def test_aggregate_agreement_and_no_invented_span():
    forecasters = [
        {"source": "A", "status": "OK", "direction": "Buy"},
        {"source": "B", "status": "OK", "direction": "Buy"},
        {"source": "C", "status": "OK", "direction": "Buy"},
        {"source": "D", "status": "OK", "direction": "Sell"},
        {"source": "E", "status": "OK", "direction": "Neutral"},
        {"source": "F", "status": "MISSING", "direction": None, "reason": "empty"},
        {"source": "G", "status": "ERROR", "direction": None, "reason": "blocked"},
        {"source": "H", "status": "SKIPPED", "direction": None, "reason": "robots"},
        {"source": "I", "status": "RANGE", "direction": None},
    ]
    ranges = [
        {"source": "A", "status": "OK", "low": 157.1, "high": 158.2},
        {"source": "B", "status": "OK", "low": 156.4, "high": 157.9},
        {"source": "C", "status": "MISSING", "low": None, "high": None},
        {"source": "D", "status": "OK", "low": 9, "high": 1},
    ]
    agg = aggregate_consensus(forecasters, ranges)
    assert agg["counts"] == {"Buy": 3, "Sell": 1, "Neutral": 1}
    assert agg["top_side"] == "Buy"
    assert agg["confidence"] == pytest.approx(0.75)
    assert agg["missing"] == 1
    assert agg["errors"] == 1
    assert agg["skipped"] == 1
    assert agg["range_span"] == {"low": 156.4, "high": 158.2, "count": 2}

    lone = aggregate_consensus([{"status": "OK", "direction": "Sell"}], [])
    assert lone["top_side"] == "Sell"
    assert lone["confidence"] is None
    assert lone["range_span"] is None

    tie = aggregate_consensus(
        [{"status": "OK", "direction": "Buy"}, {"status": "OK", "direction": "Sell"}],
        [],
    )
    assert tie["top_side"] is None
    assert tie["confidence"] is None

    quiet = aggregate_consensus([{"status": "MISSING", "direction": None}], [{"status": "MISSING"}])
    assert quiet["top_side"] is None
    assert quiet["confidence"] is None
    assert quiet["range_span"] is None


def test_fxempire_rating_and_classic_pivots_only():
    parsed = parse_fxempire_payload(
        {
            "summary": {"rating": "STRONG_BUY"},
            "pivots": [
                {"method": "Fibonacci", "s1": 1, "r1": 9},
                {"method": "Classic", "s1": 157.826, "r1": 157.992},
            ],
        }
    )
    assert parsed["direction"] == "Buy"
    assert parsed["low"] == 157.826
    assert parsed["high"] == 157.992
    assert parse_fxempire_payload({"summary": {}})["direction"] is None


def test_stocktwits_needs_three_explicit_tags():
    def msg(basic: str | None) -> dict:
        if basic is None:
            return {"entities": {}}
        return {"entities": {"sentiment": {"basic": basic}}}

    assert stocktwits_lean([msg("Bullish"), msg("Bullish"), msg(None)])[0] is None
    direction, detail = stocktwits_lean([msg("Bullish"), msg("Bullish"), msg("Bullish"), msg("Bearish")])
    assert direction == "Buy"
    assert "3 bullish" in detail
    mixed = stocktwits_lean([msg("Bullish"), msg("Bullish"), msg("Bearish"), msg("Bearish"), msg("Bearish")])
    assert mixed[0] == "Sell"
    split = stocktwits_lean([msg("Bullish"), msg("Bearish"), msg("Bullish"), msg("Bearish")])
    assert split[0] == "Neutral"


def test_actionforex_ig_fxssi_mataf_snippets():
    xml = """
    <rss><channel>
      <item>
        <title>USD/JPY Daily Outlook</title>
        <link>https://www.actionforex.com/example</link>
        <description>Intraday bias in USD/JPY remains neutral as consolidations continue.</description>
      </item>
    </channel></rss>
    """
    parsed = parse_actionforex_feed(xml, "USD/JPY")
    assert parsed["direction"] == "Neutral"
    assert "daily outlook" in parsed["window"]
    assert parse_actionforex_feed(xml, "EUR/USD")["direction"] is None

    ig = parse_ig_sentiment("<p>53% of client accounts are short on this market</p>")
    assert ig["direction"] == "Sell"
    assert "53%" in ig["reason"]
    assert parse_ig_sentiment("<p>no positioning figure</p>")["direction"] is None

    board = """
    <div class="line"><div class="symbol">EURUSD</div><div class="signal sell"></div></div>
    <div class="line"><div class="symbol">USDJPY</div><div class="signal neutral"></div></div>
    """
    signals = parse_fxssi_signals(board)
    assert signals["EURUSD"] == "Sell"
    assert signals["USDJPY"] == "Neutral"

    table = """
    <tr><th>Pivot points</th><th>R3</th><th>R2</th><th>R1</th><th>Pivot</th><th>S1</th><th>S2</th><th>S3</th></tr>
    <tr><th>USDJPY</th>
      <td class="font-red">158.84</td><td>158.31</td><td>157.89</td>
      <td>157.35</td><td class="font-green">156.93</td><td>156.40</td><td>155.97</td>
    </tr>
    """
    levels = parse_mataf_pivots(table)["USDJPY"]
    assert levels["r1"] == 157.89
    assert levels["s1"] == 156.93
    assert levels["pivot"] == 157.35


def test_read_consensus_normalizes_pair_and_lists_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "c.json"))
    snap = read_consensus("USD/JPY", "hourly", cache={})
    assert snap["pair"] == "USDJPY"
    names = [row["source"] for row in snap["forecasters"]]
    assert len(names) >= 14
    assert "FXEmpire" in names
    assert "TradingView" in names
    skipped = next(row for row in snap["forecasters"] if row["source"] == "TradingView")
    assert skipped["status"] == "SKIPPED"
    assert skipped["direction"] is None
    assert "robots" in skipped["reason"]


def test_background_refresh_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "c.json"))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "1")
    monkeypatch.setenv("FORX_CONSENSUS_GAP_S", "0")
    reset_consensus_state()
    stamp = "2026-09-23T12:00:00Z"

    def fake(pair: str, now: datetime | None = None):
        time.sleep(0.2)
        row = {
            "source": "FXEmpire",
            "direction": "Buy",
            "status": "OK",
            "reason": "",
            "url": "",
            "entry": None,
            "fetched_at": stamp,
        }
        horizon = {"fetched_at": stamp, "forecasters": [row], "ranges": []}
        return {"hourly": horizon, "daily": dict(horizon)}

    monkeypatch.setattr("api.consensus_registry.fetch_registered", fake)
    started = time.perf_counter()
    ensure_consensus("USDJPY", now=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.15
    deadline = time.perf_counter() + 2
    snap = {}
    while time.perf_counter() < deadline:
        snap = read_consensus("USDJPY", "hourly", now=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
        if any(row["source"] == "FXEmpire" and row["status"] == "OK" for row in snap["forecasters"]):
            break
        time.sleep(0.05)
    assert any(row["source"] == "FXEmpire" and row["direction"] == "Buy" for row in snap["forecasters"])
    reset_consensus_state()
