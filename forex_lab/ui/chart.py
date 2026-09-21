"""Dark candlestick for the Streamlit Chart tab.

Cached OHLCV only — not a live ticker, not a broker quote. Does not call
the paper journal. Up candles stay BUY green; down candles stay SELL red.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

CANDLE_BARS = 80
_OHLC = ("Open", "High", "Low", "Close")


def ohlc_window(ohlcv: Any, n: int = CANDLE_BARS):
    """Last ``n`` rows with Open/High/Low/Close. None when the cache cannot plot."""
    if ohlcv is None or getattr(ohlcv, "empty", True):
        return None
    missing = [c for c in _OHLC if c not in getattr(ohlcv, "columns", [])]
    if missing:
        return None
    try:
        frame = ohlcv.loc[:, list(_OHLC)].copy()
        for col in _OHLC:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=list(_OHLC))
    except Exception:
        return None
    if frame.empty:
        return None
    take = max(8, int(n or CANDLE_BARS))
    return frame.tail(take)


def _tick_label(ts: object) -> str:
    raw = str(ts)
    # 2026-09-21 16:00:00 -> 09-21 16:00
    if len(raw) >= 16 and raw[4] == "-" and raw[10] in {" ", "T"}:
        return raw[5:16].replace("T", " ")
    return raw[:16]


def candlestick_figure(
    ohlcv: Any,
    *,
    n: int = CANDLE_BARS,
    title: str = "",
    timeframe: str = "",
):
    """Matplotlib figure on the desk palette, or ``(None, reason)``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    from forex_lab.ui.theme import BG, BORDER, BUY, MUTED, SELL, SURFACE, TEXT

    frame = ohlc_window(ohlcv, n=n)
    if frame is None:
        return None, "no candlestick — cached OHLCV missing Open/High/Low/Close"

    fig, ax = plt.subplots(figsize=(11.2, 4.4), dpi=112)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(SURFACE)
    ax.tick_params(colors=MUTED, which="both")

    width = 0.62
    for i, rec in enumerate(frame.itertuples()):
        o = float(rec.Open)
        h = float(rec.High)
        low = float(rec.Low)
        c = float(rec.Close)
        color = BUY if c >= o else SELL
        ax.add_line(
            Line2D(
                [i, i],
                [low, h],
                color=color,
                linewidth=1.15,
                solid_capstyle="round",
                zorder=2,
            )
        )
        body = abs(c - o)
        span = max(h - low, 1e-9)
        if body < span * 0.02:
            body = span * 0.02
        ax.add_patch(
            Rectangle(
                (i - width / 2.0, min(o, c)),
                width,
                body,
                facecolor=color,
                edgecolor=color,
                linewidth=0.5,
                zorder=3,
            )
        )

    step = max(1, len(frame) // 6)
    ticks = list(range(0, len(frame), step))
    if ticks[-1] != len(frame) - 1:
        ticks.append(len(frame) - 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels(
        [_tick_label(frame.index[i]) for i in ticks],
        color=MUTED,
        fontsize=11,
    )
    ax.tick_params(axis="y", labelsize=12, colors=MUTED, length=3)
    ax.tick_params(axis="x", labelsize=11, colors=MUTED, length=3)
    ax.set_ylabel("price (cached)", fontsize=12)
    ax.yaxis.label.set_color(MUTED)
    ax.set_xlim(-0.85, len(frame) - 0.15)
    if title:
        ttl = ax.set_title(title, fontsize=15, fontweight="bold", loc="left", pad=10)
        ttl.set_color(TEXT)
        ttl.set_fontsize(15)
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    ax.grid(axis="y", color=BORDER, alpha=0.65, linewidth=0.7)
    ax.set_axisbelow(True)
    fig.tight_layout()
    tf = f" {timeframe}" if timeframe else ""
    note = (
        f"last {len(frame)} cached{tf} candles — yfinance last/mid-ish OHLCV, "
        "not broker bid/ask, not a live ticker"
    )
    return fig, note
