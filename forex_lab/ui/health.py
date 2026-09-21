"""v0 data-health strip for the trader screen.

Lists active feeds (per-watchlist OHLCV + news) with what is observed, the
refresh cadence, last successful update, and OK/STALE/FAIL. Not a full
awareness registry — no daily digest, no weekly retrain.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from forex_lab.clock import fmt_display, relabel
from forex_lab.freshness import FetchGate
from forex_lab.news import NewsBundle


def _calendar_health(calendar: Any | None, ttl_s: int) -> dict[str, str] | None:
    if calendar is None:
        return None
    cadence = f"FF weekly JSON · cache TTL {int(ttl_s)}s"
    events = list(getattr(calendar, "events", None) or [])
    err = getattr(calendar, "error", None)
    stale = bool(getattr(calendar, "stale_cache", False))
    fetched = relabel(getattr(calendar, "fetched_at", None))
    if err and not events:
        status, observed, detail = "FAIL", "no events", str(err)
    elif stale:
        status = "STALE"
        observed = f"{len(events)} cached events"
        detail = str(err or "stale calendar cache")
    elif events:
        first = events[0]
        title = getattr(first, "title", None) or "event"
        ccy = getattr(first, "currency", "")
        observed = f"{ccy} {title}"[:80]
        status, detail = "OK", f"{len(events)} high-impact events · {getattr(calendar, 'source', 'calendar')}"
    else:
        status, observed, detail = "MISSING", "no high-impact events", str(err or "empty")
    return {
        "Feed": "Event calendar",
        "Status": status,
        "Observed": observed,
        "Cadence": cadence,
        "Last update": fetched,
        "Detail": detail,
    }


def _yf_last_label(gate: FetchGate | None, pair: str) -> str:
    if gate is None:
        return "n/a"
    ts = (gate.last_yf_ok or {}).get(str(pair).upper())
    if not ts:
        return "n/a"
    try:
        return fmt_display(datetime.fromtimestamp(float(ts), tz=timezone.utc), seconds=True)
    except (OSError, OverflowError, ValueError):
        return "n/a"


def _fred_health(status: Any | None) -> dict[str, str] | None:
    if status is None or not bool(getattr(status, "enabled", False)):
        return None
    series = list(getattr(status, "series", None) or [])
    source = str(getattr(status, "source", "") or "missing")
    err = getattr(status, "error", None)
    fetched = relabel(getattr(status, "fetched_at", None))
    key_note = "FRED_API_KEY set" if getattr(status, "used_api_key", False) else "no API key (CSV ok)"
    cadence = f"FRED daily · as-of lag · {key_note}"
    if err and not series:
        status_s, observed, detail = "FAIL", "no series", str(err)
    elif source == "missing" and not series:
        status_s, observed, detail = "MISSING", "no cache", str(err or "FRED pack enabled, no series yet")
    elif series:
        status_s = "STALE" if err else "OK"
        observed = ", ".join(series[:5])
        detail = str(err or f"{len(series)} series · {source}")
    else:
        status_s, observed, detail = "MISSING", "no series", str(err or source)
    return {
        "Feed": "FRED macro",
        "Status": status_s,
        "Observed": observed,
        "Cadence": cadence,
        "Last update": fetched,
        "Detail": detail,
    }


def build_health_rows(
    board_rows: Iterable[Any],
    *,
    news_map: dict[str, NewsBundle] | None = None,
    calendar: Any | None = None,
    calendar_ttl_s: int = 1800,
    fred: Any | None = None,
    gate: FetchGate | None = None,
    realtime: bool = False,
    refresh_s: int = 60,
    news_ttl_s: int = 300,
    yf_min_interval_s: int = 60,
) -> list[dict[str, str]]:
    """One row per OHLCV feed + one per news feed + optional calendar/FRED."""
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
        last_fetch = relabel(getattr(row, "last_fetch_at", None) or yf_ok or "n/a")
        observed = relabel(getattr(row, "last_bar_at", None) or "n/a")
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
                "Last update": relabel(bundle.fetched_at or "n/a"),
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
                "Last update": relabel(gate.backoff_until_label() or "n/a"),
                "Detail": f"backing off until {gate.backoff_until_label()}",
            },
        )
    cal_row = _calendar_health(calendar, calendar_ttl_s)
    if cal_row is not None:
        out.append(cal_row)
    fred_row = _fred_health(fred)
    if fred_row is not None:
        out.append(fred_row)
    return out


UNHEALTHY = frozenset({"STALE", "FAIL", "ERROR", "MISSING"})


def health_unhealthy(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Feeds that should be obvious: stale, failed, missing, or error.

    CLOSED is expected on the weekend — not treated as a panic.
    """
    return [r for r in rows if str(r.get("Status") or "").upper() in UNHEALTHY]


def health_strip(rows: list[dict[str, str]]) -> str:
    """One-line feed status, always visible above the expander."""
    if not rows:
        return "No feeds — watchlist empty."
    bits = [f"{r.get('Feed', '?')} {r.get('Status', '?')}" for r in rows]
    return "Feeds: " + " · ".join(bits)
