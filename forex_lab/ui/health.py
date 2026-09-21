"""v0 data-health strip for the trader screen.

Lists active feeds (per-watchlist OHLCV + news) with what is observed, the
refresh cadence, last successful update, and OK/STALE/FAIL. Not a full
awareness registry — no daily digest, no weekly retrain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from forex_lab.freshness import FetchGate, fmt_ts
from forex_lab.news import NewsBundle


def _yf_last_label(gate: FetchGate | None, pair: str) -> str:
    if gate is None:
        return "n/a"
    ts = (gate.last_yf_ok or {}).get(str(pair).upper())
    if not ts:
        return "n/a"
    try:
        return fmt_ts(datetime.fromtimestamp(float(ts), tz=timezone.utc), seconds=True)
    except (OSError, OverflowError, ValueError):
        return "n/a"


def build_health_rows(
    board_rows: Iterable[Any],
    *,
    news_map: dict[str, NewsBundle] | None = None,
    gate: FetchGate | None = None,
    realtime: bool = False,
    refresh_s: int = 60,
    news_ttl_s: int = 300,
    yf_min_interval_s: int = 60,
) -> list[dict[str, str]]:
    """One row per OHLCV feed + one per news feed. Cheap v0."""
    cadence = (
        f"realtime {int(refresh_s)}s (local signals; yfinance when due, "
        f"1 pair/tick, min {int(yf_min_interval_s)}s)"
        if realtime
        else "manual only (Realtime off)"
    )
    news_cadence = f"Google News RSS · cache TTL {int(news_ttl_s)}s"
    out: list[dict[str, str]] = []
    news_map = news_map or {}
    for row in board_rows:
        pair = str(getattr(row, "pair", "") or "")
        tf = str(getattr(row, "timeframe", "") or "")
        validity = str(getattr(row, "validity", "MISSING") or "MISSING")
        yf_ok = _yf_last_label(gate, pair)
        last_fetch = str(getattr(row, "last_fetch_at", None) or yf_ok or "n/a")
        observed = str(getattr(row, "last_bar_at", None) or "n/a")
        n_bars = getattr(row, "n_bars", None)
        if n_bars is not None:
            observed = f"{observed} ({n_bars} bars)"
        src = str(getattr(row, "data_source", None) or "cached")
        out.append(
            {
                "Feed": f"OHLCV {pair} {tf}",
                "Status": validity,
                "Observed": observed,
                "Cadence": cadence,
                "Last update": last_fetch,
                "Detail": str(getattr(row, "validity_reason", None) or src),
            }
        )
        bundle = news_map.get(pair)
        if bundle is None:
            out.append(
                {
                    "Feed": f"News {pair}",
                    "Status": "MISSING",
                    "Observed": "no headlines",
                    "Cadence": news_cadence,
                    "Last update": "n/a",
                    "Detail": "news not fetched this tick",
                }
            )
            continue
        if bundle.error and not bundle.headlines:
            status, observed_n, detail = "FAIL", "no headlines", str(bundle.error)
        elif bundle.headlines:
            status = "OK"
            first = bundle.headlines[0].title
            observed_n = first if len(first) < 80 else first[:77] + "…"
            detail = f"{len(bundle.headlines)} headlines · bias {bundle.bias}"
        else:
            status, observed_n, detail = "MISSING", "no headlines", bundle.error or "empty feed"
        out.append(
            {
                "Feed": f"News {pair}",
                "Status": status,
                "Observed": observed_n,
                "Cadence": news_cadence,
                "Last update": str(bundle.fetched_at or "n/a"),
                "Detail": detail,
            }
        )
    if gate is not None and gate.last_error and not gate.allowed(
        datetime.now(timezone.utc).timestamp()
    ):
        out.insert(
            0,
            {
                "Feed": "yfinance gate",
                "Status": "FAIL",
                "Observed": str(gate.last_error),
                "Cadence": cadence,
                "Last update": gate.backoff_until_label() or "n/a",
                "Detail": f"backing off until {gate.backoff_until_label()}",
            },
        )
    return out
