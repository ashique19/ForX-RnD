"""Interactive Plotly candlestick for the Decision signal brief.

TradingView-like research chart of cached yfinance OHLCV — not a live ticker,
not a broker quote. Up candles stay BUY green; down candles stay SELL red.
EMA/RSI use the same causal helpers as model features. STALE/MISSING/ERROR
never invent candles.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from forex_lab.data import data_path
from forex_lab.features import ema, rsi
from forex_lab.freshness import VALIDITY_ERROR, VALIDITY_MISSING, VALIDITY_STALE

CANDLE_BARS_DEFAULT = 150
CANDLE_BARS_MIN = 100
CANDLE_BARS_MAX = 200
CHART_HEIGHT = 520
CHART_INTERVALS = ("1m", "15m", "1h", "4h", "1d")
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


def cached_chart_intervals(pair: str, cfg: dict[str, Any] | None = None) -> list[str]:
    """Intervals that already have an on-disk CSV for this pair. Missing stay disabled."""
    found: list[str] = []
    for iv in CHART_INTERVALS:
        try:
            path = data_path(str(pair).upper(), cfg or {}, iv)
        except Exception:
            continue
        if path.exists() and path.stat().st_size > 0:
            found.append(iv)
    return found


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


def _fmt_px(value: float, pair: str = "") -> str:
    digits = 3 if "JPY" in str(pair).upper() else 5
    return f"{value:.{digits}f}"


def _fmt_vol(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    v = float(value)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{v / 1_000:.2f}K"
    return f"{v:.0f}"


def last_bar_ohlc(frame: pd.DataFrame, *, pair: str = "") -> dict[str, Any]:
    """O/H/L/C + change % + volume for the last (or hovered-as-last) cached bar."""
    last = frame.iloc[-1]
    prev = frame.iloc[-2] if len(frame) > 1 else last
    o = float(last["Open"])
    h = float(last["High"])
    l = float(last["Low"])
    c = float(last["Close"])
    prev_c = float(prev["Close"])
    chg = c - prev_c
    pct = (chg / prev_c * 100.0) if prev_c else 0.0
    vol = None
    if "Volume" in frame.columns:
        try:
            vol = float(last["Volume"])
        except (TypeError, ValueError):
            vol = None
    up = c >= o
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "change": chg,
        "change_pct": pct,
        "volume": vol,
        "up": up,
        "open_s": _fmt_px(o, pair),
        "high_s": _fmt_px(h, pair),
        "low_s": _fmt_px(l, pair),
        "close_s": _fmt_px(c, pair),
        "change_s": _fmt_px(chg, pair),
        "pct_s": f"{pct:+.2f}%",
        "vol_s": _fmt_vol(vol),
    }


def ohlc_header_text(stats: dict[str, Any], *, pair: str = "", timeframe: str = "") -> str:
    pair_s = str(pair or "").upper()
    tf = str(timeframe or "")
    sign = stats["change_s"]
    if not str(sign).startswith(("+", "-")):
        sign = ("+" if float(stats["change"]) >= 0 else "") + sign
    head = f"{pair_s}  {tf}".strip()
    body = (
        f"O {stats['open_s']}  H {stats['high_s']}  L {stats['low_s']}  "
        f"C {stats['close_s']}  {sign} ({stats['pct_s']})  Vol {stats['vol_s']}"
    )
    return f"{head}   {body}".strip()


def candlestick_figure(
    ohlcv: Any,
    *,
    n: int | None = None,
    title: str = "",
    timeframe: str = "",
    pair: str = "",
    cfg: dict[str, Any] | None = None,
    validity: object = None,
    volume: bool = True,
    ema_fast: int = 50,
    ema_slow: int = 200,
    show_ema: bool = True,
    show_rsi: bool = True,
    rsi_period: int | None = None,
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

    from forex_lab.ui.theme import (
        BG,
        BORDER,
        BUY,
        CARD,
        EMA_FAST,
        EMA_SLOW,
        MUTED,
        RSI_LINE,
        SELL,
        TEXT,
        TEXT_BRIGHT,
        plotly_template,
    )

    take = int(n) if n is not None else candle_bar_count(cfg)
    frame = ohlc_window(ohlcv, n=take)
    if frame is None:
        return None, "no candlestick — cached OHLCV missing Open/High/Low/Close"

    close_all = pd.to_numeric(ohlcv["Close"], errors="coerce") if ohlcv is not None else frame["Close"]
    ema_fast_s = ema(close_all, int(ema_fast)).reindex(frame.index) if show_ema and ema_fast else None
    ema_slow_s = ema(close_all, int(ema_slow)).reindex(frame.index) if show_ema and ema_slow else None
    period = int(rsi_period or ((cfg or {}).get("rsi_period") or 14))
    rsi_s = rsi(close_all, period).reindex(frame.index) if show_rsi else None
    if ema_fast_s is not None and not ema_fast_s.notna().any():
        ema_fast_s = None
    if ema_slow_s is not None and not ema_slow_s.notna().any():
        ema_slow_s = None
    if rsi_s is not None and not rsi_s.notna().any():
        rsi_s = None
        show_rsi = False

    idx = pd.to_datetime(frame.index)
    has_vol = bool(
        volume
        and "Volume" in frame.columns
        and pd.to_numeric(frame["Volume"], errors="coerce").fillna(0).gt(0).any()
    )
    rows = 1 + int(has_vol) + int(bool(show_rsi and rsi_s is not None))
    if rows == 3:
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.58, 0.20, 0.22],
            vertical_spacing=0.03,
        )
        candle_row, vol_row, rsi_row = 1, 2, 3
    elif rows == 2 and has_vol:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.76, 0.24],
            vertical_spacing=0.04,
        )
        candle_row, vol_row, rsi_row = 1, 2, None
    elif rows == 2:
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            row_heights=[0.74, 0.26],
            vertical_spacing=0.04,
        )
        candle_row, vol_row, rsi_row = 1, None, 2
    else:
        fig = go.Figure()
        candle_row, vol_row, rsi_row = None, None, None

    stats = last_bar_ohlc(frame, pair=pair)
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
        hoverinfo="x+y",
    )
    if candle_row:
        fig.add_trace(candle, row=candle_row, col=1)
    else:
        fig.add_trace(candle)

    if ema_fast_s is not None:
        kw = dict(
            x=idx,
            y=ema_fast_s,
            name=f"EMA {ema_fast}",
            line=dict(color=EMA_FAST, width=1.5),
            hoverinfo="y+name",
        )
        if candle_row:
            fig.add_trace(go.Scatter(**kw), row=candle_row, col=1)
        else:
            fig.add_trace(go.Scatter(**kw))
    if ema_slow_s is not None:
        kw = dict(
            x=idx,
            y=ema_slow_s,
            name=f"EMA {ema_slow}",
            line=dict(color=EMA_SLOW, width=1.5),
            hoverinfo="y+name",
        )
        if candle_row:
            fig.add_trace(go.Scatter(**kw), row=candle_row, col=1)
        else:
            fig.add_trace(go.Scatter(**kw))

    if has_vol and vol_row:
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

    if show_rsi and rsi_s is not None and rsi_row:
        fig.add_trace(
            go.Scatter(
                x=idx,
                y=rsi_s,
                name=f"RSI {period}",
                line=dict(color=RSI_LINE, width=1.4),
                hoverinfo="y+name",
            ),
            row=rsi_row,
            col=1,
        )
        fig.add_hline(y=70, line_dash="dot", line_color=MUTED, line_width=1, row=rsi_row, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color=MUTED, line_width=1, row=rsi_row, col=1)

    last_close = stats["close"]
    last_color = BUY if stats["up"] else SELL
    if candle_row:
        fig.add_hline(
            y=last_close,
            line_dash="dot",
            line_color=last_color,
            line_width=1,
            row=candle_row,
            col=1,
        )
        fig.add_annotation(
            x=1,
            xref="x domain",
            y=last_close,
            yref="y" if candle_row == 1 else "y",
            text=stats["close_s"],
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            xshift=6,
            bgcolor=last_color,
            font=dict(color="#04140c" if stats["up"] else "#ffffff", size=12, family="sans-serif"),
            row=candle_row,
            col=1,
        )
    else:
        fig.add_hline(y=last_close, line_dash="dot", line_color=last_color, line_width=1)
        fig.add_annotation(
            x=1,
            xref="paper",
            y=last_close,
            yref="y",
            text=stats["close_s"],
            showarrow=False,
            xanchor="left",
            bgcolor=last_color,
            font=dict(color="#04140c" if stats["up"] else "#ffffff", size=12),
        )

    heading = ohlc_header_text(stats, pair=pair, timeframe=timeframe)
    if not pair and title:
        heading = f"{title}   {ohlc_header_text(stats, timeframe=timeframe)}"
    elif title and pair and pair.upper() not in title.upper():
        heading = f"{title}   {heading}"

    fig.update_layout(
        template=plotly_template(),
        paper_bgcolor=BG,
        plot_bgcolor=CARD,
        font=dict(color=TEXT, size=14, family="sans-serif"),
        title=dict(
            text=heading,
            font=dict(color=TEXT_BRIGHT, size=15, family="sans-serif"),
            x=0.0,
            xanchor="left",
        ),
        xaxis_rangeslider_visible=False,
        margin=dict(l=48, r=78, t=56, b=32),
        hovermode="x unified",
        height=CHART_HEIGHT,
        dragmode="pan",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            font=dict(size=12, color=MUTED),
            bgcolor="rgba(0,0,0,0)",
        ),
        spikedistance=20,
    )
    xaxis_kw = dict(
        gridcolor=BORDER,
        tickfont=dict(color=MUTED, size=12),
        showgrid=True,
        rangeslider_visible=False,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="dot",
        spikecolor=MUTED,
    )
    yaxis_kw = dict(
        gridcolor=BORDER,
        tickfont=dict(color=TEXT, size=12),
        title=dict(text="", font=dict(color=MUTED, size=12)),
        showgrid=True,
        zeroline=False,
        side="right",
        showspikes=True,
        spikemode="across",
        spikedash="dot",
        spikecolor=MUTED,
    )
    if rows > 1:
        fig.update_xaxes(**xaxis_kw, row=1, col=1)
        fig.update_yaxes(**yaxis_kw, row=1, col=1)
        if has_vol and vol_row:
            fig.update_xaxes(**xaxis_kw, row=vol_row, col=1)
            fig.update_yaxes(
                gridcolor=BORDER,
                tickfont=dict(color=MUTED, size=11),
                title=dict(text="Vol", font=dict(color=MUTED, size=12)),
                showgrid=True,
                zeroline=False,
                side="right",
                row=vol_row,
                col=1,
            )
        if show_rsi and rsi_s is not None and rsi_row:
            fig.update_xaxes(**xaxis_kw, row=rsi_row, col=1)
            fig.update_yaxes(
                gridcolor=BORDER,
                tickfont=dict(color=MUTED, size=11),
                title=dict(text=f"RSI {period}", font=dict(color=MUTED, size=12)),
                range=[0, 100],
                showgrid=True,
                zeroline=False,
                side="right",
                row=rsi_row,
                col=1,
            )
    else:
        fig.update_xaxes(**xaxis_kw)
        fig.update_yaxes(**yaxis_kw)

    tf = f" {timeframe}" if timeframe else ""
    note = (
        f"last {len(frame)} cached{tf} candles — yfinance last/mid-ish OHLCV, "
        "not broker bid/ask, not a live ticker. Research only."
    )
    return fig, note
