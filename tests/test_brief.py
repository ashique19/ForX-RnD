"""Signal brief — Hourly/Daily suggestion lines from lab data only."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.freshness import VALIDITY_MISSING, VALIDITY_OK, VALIDITY_STALE
from forex_lab.news import NewsBundle
from forex_lab.ui.brief import (
    DISCLAIMER,
    bias_label,
    build_signal_brief,
    duration_phrase,
    format_horizon_line,
    kicker_for_interval,
    pair_slash,
    potential_action,
    synthesize_why,
    tech_bullets,
)
from forex_lab.ui.theme import signal_brief_html, tech_bullets_html


def _row(**kw):
    base = dict(
        pair="EURUSD",
        timeframe="1h",
        buy_sell="SELL",
        validity=VALIDITY_OK,
        validity_reason="",
        signal_details="",
        rationale="Local model puts more weight on RSI and 20-bar slope on this bar.",
        drivers=[
            SimpleNamespace(feature="rsi", hint="RSI", contribution=0.12, value=0.62),
            SimpleNamespace(feature="sma20_slope", hint="20-bar SMA slope", contribution=0.08, value=-0.01),
        ],
        quote=SimpleNamespace(last_label=lambda: "1.14863"),
        close=1.14863,
        next_event="USD CPI in 2h",
        next_event_warn=True,
        mtf=SimpleNamespace(status="conflict", timeframe="4h", note="4h SMA slope disagrees with SELL"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _cfg():
    return {
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "interval": "1h",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 1.0},
        "entry_timing": "next_open",
        "rsi_period": 14,
        "paths": {"data_dir": "data"},
    }


def test_pair_slash_and_bias():
    assert pair_slash("EURUSD") == "EUR/USD"
    assert pair_slash("eur/usd") == "EUR/USD"
    assert bias_label("SELL") == "Bearish outlook"
    assert bias_label("BUY") == "Bullish outlook"
    assert bias_label("HOLD") == "Neutral outlook"
    assert bias_label("SELL", validity=VALIDITY_STALE) == "No live call"
    assert kicker_for_interval("1h") == "Hourly"
    assert kicker_for_interval("15m") == "Intraday"
    assert kicker_for_interval("1d") == "Daily"
    assert duration_phrase("1h", 3) == "3 hours"
    assert duration_phrase("1d", 2) == "1-2 days"
    assert potential_action("BUY") == "Potential buy"


def test_horizon_line_matches_requested_style():
    line = format_horizon_line(
        kicker="Hourly",
        side="BUY",
        now_at="1.14863",
        stop_loss="1.14850",
        target="1.14890",
        duration="3 hours",
    )
    assert line == (
        "Hourly: Potential buy: now at 1.14863, stop loss 1.14850, "
        "target 1.14890, duration 3 hours."
    )
    daily = format_horizon_line(
        kicker="Daily",
        side="BUY",
        now_at="1.14863",
        stop_loss="1.14850",
        target="1.14890",
        duration="1-2 days",
    )
    assert daily.startswith("Daily: Potential buy:")
    assert "duration 1-2 days." in daily


def test_brief_hourly_and_daily_from_atr_not_vendor_copy():
    df = generate_synthetic_ohlcv(bars=800, seed=7)
    daily = generate_synthetic_ohlcv(bars=80, interval="1d", seed=7)
    row = _row(buy_sell="BUY")
    news = NewsBundle(pair="EURUSD", bias="bearish", headlines=[], fetched_at="2026-09-21")
    brief = build_signal_brief(
        row,
        cfg=_cfg(),
        ohlcv=df,
        daily_ohlcv=daily,
        news=news,
        clock_label="2026-09-21 21:42 Asia/Dhaka",
    )
    assert not brief.blocked
    assert brief.headline.startswith("EUR/USD · Bullish outlook ·")
    assert "Asia/Dhaka" in brief.headline
    assert len(brief.horizons) == 2
    hourly, daily_card = brief.horizons
    assert hourly.kicker == "Hourly"
    assert daily_card.kicker == "Daily"
    assert hourly.available and daily_card.available
    assert hourly.line.startswith("Hourly: Potential buy: now at ")
    assert ", stop loss " in hourly.line
    assert ", target " in hourly.line
    assert "duration " in hourly.line
    assert daily_card.line.startswith("Daily: Potential buy:")
    assert "1-2 days" in daily_card.duration
    assert hourly.now_at == daily_card.now_at == "1.14863"
    assert hourly.stop_loss != "n/a" and daily_card.target != "n/a"
    assert "research label" in brief.why.lower()
    assert "Fed and ECB" not in brief.why
    assert "bearish flag" not in " ".join(brief.tech_bullets).lower()
    assert any("EMA(50)" in b for b in brief.tech_bullets)
    assert brief.invalidation
    assert any("stop-loss" in x.lower() for x in brief.invalidation)
    assert any("MTF conflict" in x for x in brief.invalidation)
    assert any("STALE" in x for x in brief.invalidation)
    html = signal_brief_html(
        headline=brief.headline,
        horizons=brief.horizons,
        why=brief.why,
        invalidation=brief.invalidation,
        disclaimer=DISCLAIMER,
    )
    assert "Potential buy" in html
    assert "If scenario changes" in html
    assert "now at" in html
    assert "<script" not in html


def test_missing_daily_says_need_fetch_not_fake_levels(monkeypatch):
    df = generate_synthetic_ohlcv(bars=40, seed=2)  # too short to resample 16 daily bars
    monkeypatch.setattr("forex_lab.ui.brief.load_cached_ohlcv", lambda *a, **k: None)
    brief = build_signal_brief(_row(), cfg=_cfg(), ohlcv=df, daily_ohlcv=None, clock_label="Asia/Dhaka")
    daily = next(c for c in brief.horizons if c.kicker == "Daily")
    assert not daily.available
    assert "need Fetch/Train for 1d" in daily.line
    assert daily.stop_loss == "n/a"
    assert daily.target == "n/a"


def test_stale_blocks_brief():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    row = _row(validity=VALIDITY_STALE, validity_reason="last bar too old")
    brief = build_signal_brief(row, cfg={"horizon": 8}, ohlcv=df, clock_label="Asia/Dhaka")
    assert brief.blocked
    assert brief.horizons == []
    assert "too old" in brief.block_reason
    missing = build_signal_brief(
        _row(validity=VALIDITY_MISSING, buy_sell="BUY"),
        ohlcv=None,
        clock_label="Asia/Dhaka",
    )
    assert missing.blocked
    assert missing.bias == "No live call"


def test_why_and_html_escape():
    row = _row(rationale="<script>x</script>. more.")
    why = synthesize_why(row, news=None, clock_label="Asia/Dhaka")
    assert "research label" in why.lower()
    html = signal_brief_html(
        headline="EUR/USD · Bearish outlook · Asia/Dhaka",
        primary=None,
        why="<script>alert(1)</script>",
        invalidation=["<script>x</script>"],
        disclaimer=DISCLAIMER,
    )
    assert "<script>" not in html
    assert "fx-brief-title" in html
    assert "fx-why" in html
    assert "If scenario changes" in html
    tech = tech_bullets_html(["Last close is below EMA(50) at 1.14."])
    assert "EMA(50)" in tech
    assert "<script" not in tech_bullets_html(["<script>x</script>"])


def test_tech_bullets_need_cache():
    row = _row()
    assert "Fetch" in tech_bullets(row, None)[0]
    df = pd.DataFrame({"Close": [1.1, 1.2]})
    out = tech_bullets(row, df, {"rsi_period": 14})
    assert out
