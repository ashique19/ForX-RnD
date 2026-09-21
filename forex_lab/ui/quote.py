"""Watchlist last/mid and spread estimate (research UI).

yfinance FX has no executable bid/ask. Last close is shown as last/mid-ish
and labeled as such. Spread is the config pip assumption used in backtests —
not the broker's live spread. Optional last-bar High−Low is a range proxy
only, never presented as bid/ask.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from forex_lab.config_loader import pip_size_for_pair

YF_SOURCE = "yfinance last/mid-ish"
YF_NOTE = "yfinance last/mid-ish — not broker bid/ask"
RANGE_NOTE = "last bar High−Low range (not a bid/ask spread)"


@dataclass
class QuoteView:
    """Scan-line quote for one pair. Never invents bid/ask."""

    last: float | None = None
    mid: float | None = None
    bid: float | None = None
    ask: float | None = None
    kind: str = "last/mid-ish"
    source: str = YF_SOURCE
    note: str = YF_NOTE
    spread_pips: float | None = None
    spread_price: float | None = None
    pip_size: float | None = None
    range_pips: float | None = None
    range_note: str = ""
    digits: int = 5

    @property
    def available(self) -> bool:
        return self.last is not None and pd.notna(self.last)

    def last_label(self) -> str:
        if not self.available:
            return "n/a"
        return f"{self.last:.{self.digits}f}"

    def spread_label(self) -> str:
        if self.spread_pips is None:
            return "n/a"
        return f"{self.spread_pips:g}p"

    def as_table_last(self) -> str:
        if not self.available:
            return "n/a"
        return f"{self.last_label()} {self.kind}"

    def as_table_spread(self) -> str:
        if self.spread_pips is None:
            return "n/a"
        return f"{self.spread_pips:g} pip (config)"


def quote_digits(pair: str) -> int:
    return 3 if "JPY" in str(pair).upper() else 5


def _num(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(v):
        return None
    return v


def _bar_range_pips(ohlcv: pd.DataFrame, pip: float) -> float | None:
    if pip <= 0 or "High" not in ohlcv.columns or "Low" not in ohlcv.columns:
        return None
    high = _num(ohlcv["High"].iloc[-1])
    low = _num(ohlcv["Low"].iloc[-1])
    if high is None or low is None or high < low:
        return None
    return (high - low) / pip


def quote_from_ohlcv(
    ohlcv: pd.DataFrame | None,
    pair: str,
    cfg: dict[str, Any] | None = None,
) -> QuoteView:
    """Last/mid from cached OHLCV + config spread. Honest about yfinance."""
    cfg = cfg or {}
    pair = str(pair).upper()
    digits = quote_digits(pair)
    pip = pip_size_for_pair(pair, cfg)
    spread_pips = float(cfg.get("spread_pips") or 0.0)
    board = dict(cfg.get("board") or {})
    qcfg = dict(board.get("quote") or {})
    show_range = bool(qcfg.get("bar_range_proxy", True))

    empty = QuoteView(
        spread_pips=spread_pips,
        spread_price=spread_pips * pip,
        pip_size=pip,
        digits=digits,
        note=str(qcfg.get("source_note") or YF_NOTE),
        source=YF_SOURCE,
        kind=str(qcfg.get("last_label") or "last/mid-ish"),
    )
    if ohlcv is None or ohlcv.empty:
        empty.note = "no cached last — Fetch required. " + empty.note
        return empty

    bid = _num(ohlcv["Bid"].iloc[-1]) if "Bid" in ohlcv.columns else None
    ask = _num(ohlcv["Ask"].iloc[-1]) if "Ask" in ohlcv.columns else None
    last = None
    if "Close" in ohlcv.columns:
        last = _num(ohlcv["Close"].iloc[-1])
    if last is None and "Last" in ohlcv.columns:
        last = _num(ohlcv["Last"].iloc[-1])

    mid = None
    kind = str(qcfg.get("last_label") or "last/mid-ish")
    source = YF_SOURCE
    note = str(qcfg.get("source_note") or YF_NOTE)
    if bid is not None and ask is not None and ask >= bid:
        mid = (bid + ask) / 2.0
        kind = "mid"
        source = "bid/ask mid"
        note = "mid from Bid/Ask columns — still not a live broker quote"
        if last is None:
            last = mid
    else:
        # yfinance FX path: Close is last/mid-ish. Do not invent Bid/Ask.
        bid = None
        ask = None
        mid = last
        kind = str(qcfg.get("last_label") or "last/mid-ish")

    range_pips = _bar_range_pips(ohlcv, pip) if show_range else None
    range_note = RANGE_NOTE if range_pips is not None else ""

    return QuoteView(
        last=last,
        mid=mid,
        bid=bid,
        ask=ask,
        kind=kind,
        source=source,
        note=note,
        spread_pips=spread_pips,
        spread_price=spread_pips * pip,
        pip_size=pip,
        range_pips=range_pips,
        range_note=range_note,
        digits=digits,
    )
