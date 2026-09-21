"""Plotly candlestick for the Chart tab — cached OHLCV, not a live ticker."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.freshness import VALIDITY_MISSING, VALIDITY_OK, VALIDITY_STALE
from forex_lab.ui.chart import (
    CANDLE_BARS_DEFAULT,
    CHART_HEIGHT,
    CHART_INTERVALS,
    cached_chart_intervals,
    candle_bar_count,
    candlestick_figure,
    chart_blocked_reason,
    last_bar_ohlc,
    ohlc_header_text,
    ohlc_window,
)
from forex_lab.ui.theme import BG, BUY, CARD, MUTED, SELL, TEXT


plotly = pytest.importorskip("plotly")


def test_ohlc_window_requires_ohlc_columns():
    df = generate_synthetic_ohlcv(bars=30, seed=2)
    win = ohlc_window(df, n=12)
    assert win is not None
    assert len(win) == 12
    assert list(win.columns)[:4] == ["Open", "High", "Low", "Close"]
    assert ohlc_window(None) is None
    assert ohlc_window(pd.DataFrame({"Close": [1.1, 1.2]})) is None
    assert ohlc_window(pd.DataFrame()) is None


def test_candle_bar_count_clamps_config():
    assert candle_bar_count(None) == CANDLE_BARS_DEFAULT
    assert candle_bar_count({"board": {"candle_bars": 150}}) == 150
    assert candle_bar_count({"board": {"candle_bars": 12}}) == 100
    assert candle_bar_count({"board": {"candle_bars": 999}}) == 200
    assert candle_bar_count({"board": {"candle_bars": "nope"}}) == CANDLE_BARS_DEFAULT


def test_stale_missing_do_not_invent_candles():
    df = generate_synthetic_ohlcv(bars=40, seed=1)
    assert chart_blocked_reason(VALIDITY_STALE)
    assert chart_blocked_reason("MISSING")
    assert chart_blocked_reason("ERROR")
    assert chart_blocked_reason(VALIDITY_OK) is None
    assert chart_blocked_reason("CLOSED") is None
    fig, note = candlestick_figure(df, validity=VALIDITY_STALE)
    assert fig is None
    assert "STALE" in note
    fig2, note2 = candlestick_figure(df, validity=VALIDITY_MISSING)
    assert fig2 is None
    assert "MISSING" in note2


def test_candlestick_plotly_light_theme_buy_sell_and_volume():
    df = generate_synthetic_ohlcv(bars=180, seed=5)
    fig, note = candlestick_figure(
        df,
        n=120,
        title="EURUSD 1h",
        timeframe="1h",
        validity=VALIDITY_OK,
    )
    assert fig is not None
    assert hasattr(fig, "to_plotly_json") or hasattr(fig, "data")
    assert "cached" in note.lower()
    assert "not broker" in note.lower() or "not a live" in note.lower()
    types = [getattr(tr, "type", "") for tr in fig.data]
    assert "candlestick" in types
    assert "bar" in types  # volume
    candle = next(tr for tr in fig.data if tr.type == "candlestick")
    inc = candle.increasing.line.color
    dec = candle.decreasing.line.color
    assert str(inc).lower() == BUY.lower()
    assert str(dec).lower() == SELL.lower()
    layout = fig.layout
    assert str(layout.paper_bgcolor).lower() == BG.lower()
    assert str(layout.plot_bgcolor).lower() == CARD.lower()
    tick = str(getattr(getattr(layout.yaxis, "tickfont", None), "color", "") or "").lower()
    assert tick in {TEXT.lower(), MUTED.lower()} or tick == ""
    template = str(getattr(layout, "template", "") or "")
    assert "plotly_white" in template.lower() or BG.lower() in ("#f4f6f8",)
    assert layout.xaxis.rangeslider.visible is False
    assert "EURUSD" in str(layout.title.text or "")
    assert "O " in str(layout.title.text or "")
    assert "Vol" in str(layout.title.text or "")
    assert layout.height >= 420
    assert layout.height == CHART_HEIGHT
    names = [str(getattr(tr, "name", "") or "") for tr in fig.data]
    assert any(n.startswith("EMA") for n in names)
    assert any(n.startswith("RSI") for n in names)
    assert len(candle.x) == 120


def test_candlestick_missing_cache_fail_soft():
    fig, note = candlestick_figure(None)
    assert fig is None
    assert "no candlestick" in note.lower()
    fig2, note2 = candlestick_figure(pd.DataFrame({"Close": [1.0, 1.1]}))
    assert fig2 is None
    assert "missing" in note2.lower() or "no candlestick" in note2.lower()


def test_chart_helper_does_not_import_broker():
    source = Path("forex_lab/ui/chart.py").read_text(encoding="utf-8")
    assert "from forex_lab.broker" not in source
    assert "submit(" not in source
    app = Path("streamlit_app.py").read_text(encoding="utf-8")
    assert "candlestick_figure" in app
    assert "_render_price_chart" in app
    assert "plotly_chart" in app
    cfg = Path("config/default.yaml").read_text(encoding="utf-8")
    assert "candle_bars" in cfg
    assert "CHART_INTERVALS" in source
    assert "1m" in CHART_INTERVALS and "1h" in CHART_INTERVALS
    app = Path("streamlit_app.py").read_text(encoding="utf-8")
    assert "_render_chart_toolbar" in app
    assert "board_realtime" in app
    assert "board_reload" in app
    assert '"Manual update"' not in app
    assert '"Update selected"' not in app
    assert "build_signal_brief" in app
    assert "Run pipeline" in app
    assert "Add pair" in app


def test_last_bar_ohlc_and_header():
    df = generate_synthetic_ohlcv(bars=40, seed=4)
    win = ohlc_window(df, n=20)
    stats = last_bar_ohlc(win, pair="EURUSD")
    assert stats["close"] == pytest.approx(float(win["Close"].iloc[-1]))
    assert "vol_s" in stats
    text = ohlc_header_text(stats, pair="EURUSD", timeframe="1h")
    assert "EURUSD" in text and "1h" in text
    assert "O " in text and "C " in text


def test_cached_intervals_sees_repo_sample():
    found = cached_chart_intervals("EURUSD", {"paths": {"data_dir": "data"}})
    assert "1h" in found


def test_ema200_trace_when_series_is_long_enough():
    df = generate_synthetic_ohlcv(bars=280, seed=9)
    fig, _note = candlestick_figure(
        df, n=120, pair="EURUSD", timeframe="1h", validity=VALIDITY_OK, show_rsi=False
    )
    assert fig is not None
    names = [str(getattr(tr, "name", "") or "") for tr in fig.data]
    assert "EMA 50" in names
    assert "EMA 200" in names
    assert not any(n.startswith("RSI") for n in names)
