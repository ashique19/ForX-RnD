"""Interactive Plotly candlestick for the Streamlit Chart tab.

Cached OHLCV only — not a live ticker, not a broker quote. Does not call
the paper journal. Up candles stay BUY green; down candles stay SELL red.
The scan-board sparkline stays a mini close spark; candles live in the drawer.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from forex_lab.freshness import VALIDITY_ERROR, VALIDITY_MISSING, VALIDITY_STALE

CANDLE_BARS_DEFAULT = 150
CANDLE_BARS_MIN = 100
CANDLE_BARS_MAX = 200
_OHLC = ("Open", "High", "Low", "Close")
_BLOCKED = frozenset({VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR, "FAIL"})


def candle_bar_count(cfg: dict[str, Any] | None = None) -> int:
    """Last-N candles for the drawer. Config ``board.candle_bars`` (100–200)."""
    raw = CANDLE_BARS_DEFAULT
    try:
        raw = int(((cfg or {}).get("board") or {}).get("candle_bars") or CANDLE_BARS_DEFAULT)
    except (TypeError, ValueError):
        raw = CANDLE_BARS_DEFAULT
    return max(CANDLE_BARS_MIN, min(CANDLE_BARS_MAX, raw))


def chart_blocked_reason(validity: object) -> str | None:
    """STALE / MISSING / ERROR never plot — do not invent candles from a dead cache."""
    token = str(validity or "").strip().upper().split()[0] if validity is not None else ""
    if token in _BLOCKED:
        return (
            f"no candlestick — Data● is {token}. "
            "Refresh (Fetch) first. Cached stale bars are not shown as a live chart."
        )
    return None


def ohlc_window(ohlcv: Any, n: int = CANDLE_BARS_DEFAULT):
    """Last ``n`` rows with Open/High/Low/Close. None when the cache cannot plot."""
    if ohlcv is None or getattr(ohlcv, "empty", True):
        return None
    missing = [c for c in _OHLC if c not in getattr(ohlcv, "columns", [])]
    if missing:
        return None
    try:
        cols = list(_OHLC)
        extra = ["Volume"] if "Volume" in ohlcv.columns else []
        frame = ohlcv.loc[:, cols + extra].copy()
        for col in cols + extra:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=list(_OHLC))
    except Exception:
        return None
    if frame.empty:
        return None
    take = max(8, int(n or CANDLE_BARS_DEFAULT))
    return frame.tail(take)


def candlestick_figure(
    ohlcv: Any,
    *,
    n: int | None = None,
    title: str = "",
    timeframe: str = "",
    cfg: dict[str, Any] | None = None,
    validity: object = None,
    volume: bool = True,
):
    """Plotly figure on the desk palette, or ``(None, reason)``.

    Research chart of cached yfinance OHLCV — not broker bid/ask.
    """
    blocked = chart_blocked_reason(validity)
    if blocked:
        return None, blocked
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        return None, "no candlestick — plotly is not installed"

    from forex_lab.ui.theme import BG, BORDER, BUY, MUTED, SELL, SURFACE, TEXT, TEXT_BRIGHT

    take = int(n) if n is not None else candle_bar_count(cfg)
    frame = ohlc_window(ohlcv, n=take)
    if frame is None:
        return None, "no candlestick — cached OHLCV missing Open/High/Low/Close"

    idx = pd.to_datetime(frame.index)
    has_vol = bool(
        volume
        and "Volume" in frame.columns
        and pd.to_numeric(frame["Volume"], errors="coerce").fillna(0).gt(0).any()
    )
    if has_vol:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.74, 0.26],
            vertical_spacing=0.04,
        )
        candle_row, vol_row = 1, 2
    else:
        fig = go.Figure()
        candle_row, vol_row = None, None

    candle = go.Candlestick(
        x=idx,
        open=frame["Open"],
        high=frame["High"],
        low=frame["Low"],
        close=frame["Close"],
        name="OHLC",
        increasing=dict(line=dict(color=BUY), fillcolor=BUY),
        decreasing=dict(line=dict(color=SELL), fillcolor=SELL),
        showlegend=False,
        whiskerwidth=0.7,
    )
    if has_vol:
        fig.add_trace(candle, row=candle_row, col=1)
        vol_colors = [BUY if float(c) >= float(o) else SELL for o, c in zip(frame["Open"], frame["Close"])]
        fig.add_trace(
            go.Bar(
                x=idx,
                y=frame["Volume"],
                marker_color=vol_colors,
                name="Volume",
                showlegend=False,
                opacity=0.85,
            ),
            row=vol_row,
            col=1,
        )
    else:
        fig.add_trace(candle)

    tf = f" {timeframe}" if timeframe else ""
    heading = title or f"cached{tf} OHLC"
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor=BG,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT, size=13, family="sans-serif"),
        title=dict(text=heading, font=dict(color=TEXT_BRIGHT, size=16, family="sans-serif")),
        xaxis_rangeslider_visible=False,
        margin=dict(l=52, r=18, t=52, b=36),
        hovermode="x unified",
        height=480 if has_vol else 400,
        dragmode="pan",
    )
    xaxis_kw = dict(
        gridcolor=BORDER,
        tickfont=dict(color=MUTED, size=12),
        showgrid=True,
        rangeslider_visible=False,
    )
    yaxis_kw = dict(
        gridcolor=BORDER,
        tickfont=dict(color=MUTED, size=12),
        title=dict(text="price (cached)", font=dict(color=MUTED, size=13)),
        showgrid=True,
        zeroline=False,
        side="right",
    )
    if has_vol:
        fig.update_xaxes(**xaxis_kw, row=1, col=1)
        fig.update_xaxes(**xaxis_kw, row=2, col=1)
        fig.update_yaxes(**yaxis_kw, row=1, col=1)
        fig.update_yaxes(
            gridcolor=BORDER,
            tickfont=dict(color=MUTED, size=11),
            title=dict(text="vol", font=dict(color=MUTED, size=12)),
            showgrid=True,
            zeroline=False,
            row=2,
            col=1,
        )
    else:
        fig.update_xaxes(**xaxis_kw)
        fig.update_yaxes(**yaxis_kw)

    note = (
        f"last {len(frame)} cached{tf} candles — yfinance last/mid-ish OHLCV, "
        "not broker bid/ask, not a live ticker. Research only."
    )
    return fig, note
