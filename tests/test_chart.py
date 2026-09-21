"""Dark candlestick for the Chart tab — cached OHLCV, not a live ticker."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.ui.chart import candlestick_figure, ohlc_window
from forex_lab.ui.theme import BG, BUY, MUTED, SELL, SURFACE, TEXT


def test_ohlc_window_requires_ohlc_columns():
    df = generate_synthetic_ohlcv(bars=30, seed=2)
    win = ohlc_window(df, n=12)
    assert win is not None
    assert len(win) == 12
    assert list(win.columns) == ["Open", "High", "Low", "Close"]
    assert ohlc_window(None) is None
    assert ohlc_window(pd.DataFrame({"Close": [1.1, 1.2]})) is None
    assert ohlc_window(pd.DataFrame()) is None


def test_candlestick_dark_theme_readable_axes_and_buy_sell():
    df = generate_synthetic_ohlcv(bars=60, seed=5)
    fig, note = candlestick_figure(df, title="EURUSD 1h", timeframe="1h")
    assert fig is not None
    fig.canvas.draw()
    assert "cached" in note.lower()
    assert "not broker" in note.lower() or "not a live" in note.lower()
    ax = fig.axes[0]
    from matplotlib.colors import to_hex

    assert to_hex(fig.patch.get_facecolor()).lower() == BG.lower()
    assert to_hex(ax.get_facecolor()).lower() == SURFACE.lower()
    xticks = [t for t in ax.get_xticklabels() if t.get_text()]
    yticks = [t for t in ax.get_yticklabels() if t.get_text()]
    assert xticks and yticks
    assert to_hex(xticks[0].get_color()).lower() == MUTED.lower()
    assert to_hex(yticks[0].get_color()).lower() == MUTED.lower()
    assert float(xticks[0].get_fontsize()) >= 10
    assert float(yticks[0].get_fontsize()) >= 11
    left = getattr(ax, "title", None)
    # loc='left' stores the visible title on _left_title, not ax.title.
    shown = getattr(ax, "_left_title", left)
    assert shown is not None and shown.get_text() == "EURUSD 1h"
    assert to_hex(shown.get_color()).lower() == TEXT.lower()
    assert float(shown.get_fontsize()) >= 14
    colors = {to_hex(p.get_facecolor()).lower() for p in ax.patches}
    assert BUY.lower() in colors
    assert SELL.lower() in colors
    import matplotlib.pyplot as plt

    plt.close(fig)


def test_candlestick_missing_cache_fail_soft():
    fig, note = candlestick_figure(None)
    assert fig is None
    assert "no candlestick" in note.lower()


def test_chart_helper_does_not_import_broker():
    source = Path("forex_lab/ui/chart.py").read_text(encoding="utf-8")
    assert "from forex_lab.broker" not in source
    assert "submit(" not in source
    app = Path("streamlit_app.py").read_text(encoding="utf-8")
    assert "candlestick_figure" in app
    assert "_render_price_chart" in app
