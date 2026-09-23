"""JSON views over existing forex_lab board, bars, and pipeline helpers.

No model rewrite. STALE and MISSING stay visible; prices are never invented.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from forex_lab.advise import suggest_actions
from forex_lab.calendar import (
    CalendarBundle,
    countdown_label,
    event_affects_pair,
    event_window,
    fetch_calendar,
    next_event_for_pair,
    next_event_label,
    short_event_title,
)
from forex_lab.chart_indicators import chart_indicators_aligned, chart_price_digits, empty_indicators
from forex_lab.clock import fmt_display, parse_ts, timezone_name, timezone_tag
from forex_lab.config_loader import load_config, pip_size_for_pair
from forex_lab.data import (
    SOURCE_RESAMPLED_FROM_1H,
    SOURCE_YFINANCE,
    ensure_interval_ohlcv,
    load_cached_ohlcv,
    try_yfinance_refresh,
)
from forex_lab.features import true_range_atr
from forex_lab.ui.model_build import model_build_status
from forex_lab.freshness import (
    DEFAULT_STALE_BARS,
    VALIDITY_CLOSED,
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
    assess_ohlcv,
    board_cfg,
    format_failure_reason,
    fx_session_open,
    interval_seconds,
    is_rate_limited_reason,
    now_utc,
)
from forex_lab.session import classify_session, session_windows
from forex_lab.ui.alerts import process_watch, visible_alerts
from forex_lab.ui.board import _barrier_levels, attach_next_event, build_board_row, build_board_rows
from forex_lab.ui.quote import quote_digits
from forex_lab.ui.watchlist import (
    KNOWN_INTERVALS,
    WatchlistError,
    add_pair,
    load_watchlist,
    normalize_pair,
    remove_pair,
    save_watchlist,
)

from api.consensus import ensure_consensus, lean_label, read_consensus
from api.limiter import allow, data_refresh_seconds

TF_LABELS = {
    "15m": "M15",
    "1h": "H1",
    "4h": "H4",
    "1d": "D1",
    "1D": "D1",
}
INTERVAL_ALIASES = {
    "15m": "15m",
    "m15": "15m",
    "1h": "1h",
    "h1": "1h",
    "60m": "1h",
    "4h": "4h",
    "h4": "4h",
    "1d": "1d",
    "d1": "1d",
    "1D": "1d",
}
HOURLY_INTERVAL = "1h"
DAILY_INTERVAL = "1d"
OHLCV_INTERVALS = frozenset({"15m", "1h", "4h", "1d"})
ASSET_ALLOW = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "XAUUSD",
    "XAGUSD",
)
PROXIMITY_PIPS = 6.0
LIVE_SIGNALS = frozenset({"BUY", "SELL"})


def app_config() -> dict[str, Any]:
    return load_config()


def watchlist_path() -> Path | None:
    raw = os.environ.get("FORX_WATCHLIST_PATH")
    return Path(raw) if raw else None


def alert_state_path() -> Path | None:
    raw = os.environ.get("FORX_ALERT_STATE")
    return Path(raw) if raw else None


def tf_label(interval: str | None) -> str:
    key = str(interval or "").strip()
    return TF_LABELS.get(key, key.upper() or "—")


def parse_interval(raw: str | None, *, default: str = "1h") -> str:
    if raw is None or str(raw).strip() == "":
        return default
    key = INTERVAL_ALIASES.get(str(raw).strip())
    if key is None:
        raise WatchlistError(f"Unknown timeframe {raw!r}. Use 15m, 1h, 4h, or 1d.")
    return key


def _num(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(out):
        return None
    return out


def price_text(pair: str, value: float | None) -> str:
    if value is None:
        return "—"
    digits = quote_digits(pair)
    if "XAU" in pair.upper() or "XAG" in pair.upper():
        digits = 1
    return f"{value:.{digits}f}"


def compact_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def fetch_age_label(seconds: float | None) -> str:
    """Time since the last successful fetch. Not the forming-bar open age."""
    if seconds is None:
        return "—"
    if seconds < 60:
        return "just now"
    return f"fetched {compact_age(seconds)}"


def _age_s(label: object, *, now: datetime | None = None) -> float | None:
    ts = parse_ts(label)
    if ts is None:
        return None
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return (clock - ts).total_seconds()


def _stale_limit_s(interval: str, cfg: dict[str, Any] | None) -> float:
    """Same window as ``assess_ohlcv``: N × timeframe, plus a small slack."""
    iv_s = interval_seconds(interval)
    stale_bars = float(board_cfg(cfg).get("stale_bars") or DEFAULT_STALE_BARS)
    stale_after = max(iv_s * stale_bars, iv_s + 60)
    slack = min(900, max(60, iv_s * 0.15))
    return stale_after + slack


def agree_validity(validity: str, age_s: float | None, interval: str, cfg: dict[str, Any] | None) -> str:
    """A bar older than the stale window is never labeled live."""
    token = str(validity or VALIDITY_MISSING).upper().split()[0]
    if token == VALIDITY_OK and age_s is not None and age_s > _stale_limit_s(interval, cfg):
        return VALIDITY_STALE
    return token


def data_view(validity: str) -> dict[str, str]:
    token = str(validity or "").strip().upper().split()[0]
    if token == VALIDITY_OK:
        return {"text": "Live", "tone": "ok"}
    if token == VALIDITY_STALE:
        return {"text": "STALE", "tone": "lag"}
    if token == VALIDITY_MISSING:
        return {"text": "MISSING", "tone": "miss"}
    if token == VALIDITY_CLOSED:
        return {"text": "Closed", "tone": "closed"}
    if token == VALIDITY_ERROR:
        return {"text": "ERROR", "tone": "lag"}
    return {"text": token or "—", "tone": "miss"}


def session_view(row: Any) -> dict[str, str]:
    state = getattr(row, "session", None)
    if state is None:
        return {"text": "—", "key": "off"}
    name = str(getattr(state, "name", "") or "").lower()
    labels = [str(item).lower() for item in (getattr(state, "labels", None) or [])]
    if name == "closed":
        return {"text": "Closed", "key": "closed"}
    if "london" in labels and "ny" in labels:
        return {"text": "London+NY", "key": "london"}
    if "ny" in labels:
        return {"text": "NY", "key": "ny"}
    if "london" in labels:
        return {"text": "London", "key": "london"}
    if "asia" in labels:
        return {"text": "Asia", "key": "asia"}
    return {"text": "—", "key": "off"}


def _target_price(row: Any) -> float | None:
    risk = getattr(row, "risk", None)
    if risk is not None and getattr(risk, "available", False):
        return _num(getattr(risk, "tp", None))
    return None


def _stop_price(row: Any) -> float | None:
    risk = getattr(row, "risk", None)
    if risk is not None and getattr(risk, "available", False):
        return _num(getattr(risk, "sl", None))
    return None


def row_json(row: Any, *, now: datetime | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    pair = str(row.pair).upper()
    interval = str(row.timeframe or "1h")
    raw_validity = str(row.validity or VALIDITY_MISSING).upper().split()[0]
    signal = str(row.buy_sell or "—").upper()
    if signal not in {"BUY", "SELL", "HOLD"}:
        signal = "—"
    last_px = _num(getattr(row, "close", None))
    quote = getattr(row, "quote", None)
    if last_px is None and quote is not None:
        last_px = _num(getattr(quote, "last", None))
    target = _target_price(row)
    age = _age_s(getattr(row, "last_bar_at", None), now=now)
    fetched = _age_s(getattr(row, "last_fetch_at", None), now=now)
    validity = agree_validity(raw_validity, age, interval, cfg)
    reason = str(getattr(row, "validity_reason", "") or "")
    if validity == VALIDITY_STALE and raw_validity == VALIDITY_OK:
        signal = "—"
        target = None
        if not reason:
            reason = "last bar is older than the freshness window"
    mtf = getattr(row, "mtf", None)
    return {
        "pair": pair,
        "tf": tf_label(interval),
        "interval": interval,
        "signal": signal,
        "raw_signal": None if not getattr(row, "raw_signal", None) else str(row.raw_signal),
        "target": target,
        "target_text": price_text(pair, target),
        "last": last_px,
        "last_text": price_text(pair, last_px),
        "validity": validity,
        "validity_reason": reason,
        "data": data_view(validity),
        "session": session_view(row),
        "age": compact_age(age),
        "age_s": None if age is None else round(age, 1),
        "fetch_age": fetch_age_label(fetched),
        "fetch_age_s": None if fetched is None else round(fetched, 1),
        "last_bar_dhaka": fmt_display(getattr(row, "last_bar_at", None), seconds=True),
        "last_fetch_dhaka": fmt_display(getattr(row, "last_fetch_at", None), seconds=True),
        "last_signal_dhaka": fmt_display(getattr(row, "last_signal_at", None), seconds=True),
        "status": str(getattr(row, "status", "") or ""),
        "confidence": _num(getattr(row, "confidence", None)),
        "rationale": str(getattr(row, "rationale", "") or ""),
        "details": str(getattr(row, "signal_details", "") or ""),
        "mtf": None if mtf is None else str(getattr(mtf, "status", "") or ""),
        "mtf_note": None if mtf is None else str(getattr(mtf, "note", "") or ""),
        "next_event": str(getattr(row, "next_event", "") or "") or "—",
        "next_event_warn": bool(getattr(row, "next_event_warn", False)),
        "stop": _stop_price(row),
        "atr": _num(getattr(getattr(row, "risk", None), "atr", None)),
        "horizon_bars": None
        if getattr(row, "risk", None) is None
        else getattr(row.risk, "horizon", None),
    }


def load_wl(cfg: dict[str, Any] | None = None):
    cfg = cfg if cfg is not None else app_config()
    return load_watchlist(watchlist_path(), cfg, create=False)


def supported_assets(cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg if cfg is not None else app_config()
    out: list[str] = []
    seen: set[str] = set()
    for sym in ASSET_ALLOW:
        seen.add(sym)
        out.append(sym)
    for key in cfg.get("pairs") or {}:
        try:
            pair = normalize_pair(str(key))
        except WatchlistError:
            continue
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def assets_payload(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else app_config()
    wl = load_wl(cfg)
    watched = set(wl.pair_symbols())
    return {"assets": [{"pair": pair, "watched": pair in watched} for pair in supported_assets(cfg)]}


def watchlist_json(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else app_config()
    wl = load_wl(cfg)
    return {
        "refresh_seconds": int(wl.refresh_seconds),
        "interval": wl.lab_interval(cfg),
        "count": len(wl.pairs),
        "assets": assets_payload(cfg)["assets"],
        "pairs": [
            {
                "pair": item.pair,
                "interval": item.resolved_interval(wl.lab_interval(cfg)),
                "tf": tf_label(item.resolved_interval(wl.lab_interval(cfg))),
                "interval_override": item.interval,
            }
            for item in wl.pairs
        ],
    }


def mutate_watchlist(pair: str, *, interval: str | None = None, remove: bool = False) -> dict[str, Any]:
    cfg = app_config()
    wl = load_wl(cfg)
    symbol = normalize_pair(pair)
    iv = None
    if interval:
        iv = parse_interval(interval)
        if iv not in KNOWN_INTERVALS and iv not in OHLCV_INTERVALS:
            raise WatchlistError(f"Unsupported interval {interval!r}")
    if remove:
        remove_pair(wl, symbol)
    else:
        if symbol not in supported_assets(cfg):
            raise WatchlistError(f"{symbol} is not in the research asset list")
        add_pair(wl, symbol, iv)
    save_watchlist(wl, watchlist_path())
    return watchlist_json(cfg)


def _human_delta(seconds: float) -> str:
    s = int(max(0, round(seconds)))
    hours, rem = divmod(s, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def session_alert(cfg: dict[str, Any] | None = None, *, now: datetime | None = None) -> str | None:
    """Clock note for the strip. NY open countdown, or closed. Not a quote."""
    cfg = cfg if cfg is not None else app_config()
    clock = now_utc(now)
    state = classify_session(clock, cfg)
    if not state.market_open or not fx_session_open(clock):
        return "FX session closed"
    if "ny" in (state.labels or []):
        return None
    windows = session_windows(cfg)
    start, _end = windows.get("ny", (13.0, 21.0))
    start_hour = int(start)
    start_min = int(round((start - start_hour) * 60))
    candidate = clock.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
    if candidate <= clock:
        candidate = candidate + timedelta(days=1)
    # Skip a Saturday landing (market closed).
    while candidate.weekday() == 5 or (candidate.weekday() == 6 and candidate.hour < 21):
        candidate = candidate + timedelta(days=1)
        candidate = candidate.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
    delta = (candidate - clock).total_seconds()
    if delta <= 0:
        return None
    return f"USD session open in {_human_delta(delta)}"


def proximity_message(row: Any, cfg: dict[str, Any]) -> str | None:
    """Only when validity is OK and a real ATR target exists. Never a guessed pip count."""
    if str(getattr(row, "validity", "") or "").upper().split()[0] != VALIDITY_OK:
        return None
    target = _target_price(row)
    last = _num(getattr(row, "close", None))
    if target is None or last is None:
        return None
    pip = pip_size_for_pair(str(row.pair), cfg)
    if pip <= 0:
        return None
    dist = abs(last - target) / pip
    if dist > PROXIMITY_PIPS:
        return None
    shown = max(dist, 0.0)
    pips = f"{shown:.0f}" if shown >= 1 else f"{shown:.1f}"
    return (
        f"{str(row.pair).upper()} {tf_label(row.timeframe)} target hit proximity — "
        f"{price_text(str(row.pair), last)} within {pips} pips of TP"
    )


def standing_validity_message(row: Any) -> str | None:
    token = str(getattr(row, "validity", "") or "").upper().split()[0]
    if token not in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        return None
    reason = str(getattr(row, "validity_reason", "") or "").strip()
    base = f"{str(row.pair).upper()} {tf_label(row.timeframe)} data {token}"
    if reason:
        return f"{base} — {reason}"
    return base


def _event_minutes(cfg: dict[str, Any] | None) -> tuple[int, int, int]:
    block = dict((cfg or {}).get("advice") or {})
    cal = dict((cfg or {}).get("calendar") or {})
    before = int(block.get("before_minutes") or cal.get("before_minutes") or 60)
    during = int(block.get("during_minutes") or cal.get("during_minutes") or 15)
    after = int(block.get("after_minutes") or cal.get("after_minutes") or 30)
    return before, during, after


def load_calendar(cfg: dict[str, Any] | None = None, *, force: bool = False) -> CalendarBundle:
    """Cached weekly feed. Fail-soft: a dead fetch reuses ``data/calendar_cache.json``."""
    cfg = cfg if cfg is not None else app_config()
    probe = dict(cfg)
    override = os.environ.get("FORX_CALENDAR_CACHE")
    if override:
        cal = dict(probe.get("calendar") or {})
        cal["cache_file"] = override
        probe["calendar"] = cal
    try:
        return fetch_calendar(probe, force=force)
    except Exception as exc:
        return CalendarBundle(
            error=f"calendar unavailable ({type(exc).__name__})",
            notes=["Calendar unavailable."],
        )


def _watch_pairs(cfg: dict[str, Any]) -> list[str]:
    try:
        wl = load_wl(cfg)
    except Exception:
        return []
    out: list[str] = []
    for item in wl.pairs:
        symbol = str(getattr(item, "pair", "") or "").upper()
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def _clock(now: datetime | None) -> datetime:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc)


def _display_stamp(value: object, cfg: dict[str, Any]) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    shown = fmt_display(value, cfg, seconds=False)
    if shown == "n/a":
        return str(value)
    return shown


def next_event_payload(
    pair: str,
    events: list[Any],
    cfg: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Soonest high-impact print for this pair. None when the feed has no match."""
    clock = _clock(now)
    event = next_event_for_pair(events, pair, clock)
    if event is None:
        return None
    before, during, after = _event_minutes(cfg)
    window = event_window(
        event, clock, before_minutes=before, during_minutes=during, after_minutes=after
    )
    warn = window in {"before", "during"}
    when = event.when_dt()
    return {
        "title": event.title,
        "short_title": short_event_title(event.title),
        "currency": event.currency,
        "impact": event.impact,
        "when": event.when,
        "when_dhaka": _display_stamp(when, cfg),
        "countdown": countdown_label(when, clock),
        "label": next_event_label(event, clock, warn=warn),
        "window": window,
        "warn": warn,
        "highlight": bool(event.highlight),
        "forecast": event.forecast or "",
        "previous": event.previous or "",
    }


def _event_json(
    event: Any,
    cfg: dict[str, Any],
    now: datetime,
    pairs: list[str],
) -> dict[str, Any]:
    before, during, after = _event_minutes(cfg)
    window = event_window(
        event, now, before_minutes=before, during_minutes=during, after_minutes=after
    )
    when = event.when_dt()
    hit = [p for p in pairs if event_affects_pair(event, p)]
    return {
        "title": event.title,
        "currency": event.currency,
        "impact": event.impact,
        "when": event.when,
        "when_dhaka": _display_stamp(when, cfg),
        "countdown": countdown_label(when, now),
        "forecast": event.forecast or "",
        "previous": event.previous or "",
        "highlight": bool(event.highlight),
        "pairs": hit,
        "window": window,
        "warn": window in {"before", "during"},
    }


def calendar_context(bundle: CalendarBundle) -> dict[str, Any]:
    note = None
    if bundle.stale_cache:
        note = next((str(n) for n in bundle.notes if n), None) or (
            "Using stale local cache — live calendar fetch failed."
        )
    elif bundle.error and not bundle.events:
        note = str(bundle.error)
    return {
        "error": bundle.error,
        "stale_cache": bool(bundle.stale_cache),
        "note": note,
        "notes": [str(n) for n in bundle.notes if n],
        "count": len(bundle.events),
        "fetched_at": bundle.fetched_at,
        "source": bundle.source,
    }


def calendar_payload(
    cfg: dict[str, Any] | None = None,
    *,
    pairs: list[str] | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """High-impact events for the desk. Cache TTL applies unless ``force``."""
    cfg = cfg if cfg is not None else app_config()
    bundle = load_calendar(cfg, force=force)
    clock = _clock(now)
    wanted = list(pairs) if pairs is not None else _watch_pairs(cfg)
    events = [_event_json(event, cfg, clock, wanted) for event in bundle.events]
    ttl = int((cfg.get("calendar") or {}).get("cache_ttl_s") or 1800)
    ctx = calendar_context(bundle)
    return {
        "timezone": timezone_name(cfg),
        "fetched_at": bundle.fetched_at,
        "fetched_at_dhaka": _display_stamp(bundle.fetched_at, cfg) if bundle.fetched_at else None,
        "source": bundle.source,
        "source_url": bundle.source_url,
        "stale_cache": bool(bundle.stale_cache),
        "error": bundle.error,
        "notes": ctx["notes"],
        "note": ctx["note"],
        "cache_ttl_s": ttl,
        "count": len(events),
        "pairs": wanted,
        "events": events,
    }


def _annotate_suggestion(suggestion: dict[str, Any], event: dict[str, Any] | None) -> dict[str, Any]:
    """Keep model stop/target numbers. Say when a nearby release changes the read."""
    if not event or event.get("window") in {None, "", "none"}:
        return suggestion
    out = dict(suggestion)
    label = str(event.get("label") or "event")
    window = str(event.get("window") or "")
    note = f"Event {window}: {label}."
    scenario = str(out.get("scenario") or "").rstrip()
    if note not in scenario:
        out["scenario"] = f"{scenario} {note}".strip()
    if event.get("warn"):
        duration = str(out.get("duration") or "").strip()
        countdown = str(event.get("countdown") or "").strip()
        suffix = f"caution {countdown}".strip()
        if suffix and suffix not in duration:
            out["duration"] = f"{duration} · {suffix}" if duration else suffix
    return out


def _advice_json(card: Any, pair: str) -> dict[str, Any]:
    proposed = card.suggested_sl
    return {
        "action": card.action,
        "title": card.title,
        "detail": card.detail,
        "window": card.window,
        "severity": card.severity,
        "event_title": card.event_title,
        "event_when": card.event_when,
        "countdown": card.countdown,
        "currencies": card.currencies,
        "suggested_sl": proposed,
        "suggested_sl_text": price_text(pair, proposed) if proposed is not None else "",
    }


def _attach_event_stop(blocks: list[dict[str, Any]], pair: str, cards: list[Any]) -> None:
    proposed = next((card.suggested_sl for card in cards if card.suggested_sl is not None), None)
    if proposed is None:
        return
    text = price_text(pair, float(proposed))
    for block in blocks:
        block["event_stop"] = float(proposed)
        block["event_stop_text"] = text


def board_payload(
    cfg: dict[str, Any] | None = None,
    *,
    refresh_data: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else app_config()
    wl = load_wl(cfg)
    rows = build_board_rows(wl, cfg, refresh_data=refresh_data, regenerate=True if refresh_data else False)
    bundle = load_calendar(cfg)
    events = list(bundle.events)
    clock = _clock(now) if now is not None else None
    for row in rows:
        attach_next_event(row, events, now=clock, cfg=cfg)
    _state, _fresh = process_watch(
        rows,
        calendar=bundle,
        cfg=cfg,
        now=clock,
        persist=True,
        path=alert_state_path(),
    )
    alerts: list[dict[str, str]] = []
    seen: set[str] = set()

    def _push(kind: str, message: str, pair: str = "") -> None:
        if not message or message in seen:
            return
        seen.add(message)
        alerts.append({"kind": kind, "message": message, "pair": pair})

    for row in rows:
        msg = proximity_message(row, cfg)
        if msg:
            _push("proximity", msg, str(row.pair).upper())
    note = session_alert(cfg, now=now)
    if note:
        _push("session", note)
    for row in rows:
        msg = standing_validity_message(row)
        if msg:
            _push(str(row.validity or "status").lower(), msg, str(row.pair).upper())
    for alert in visible_alerts(_state):
        _push(alert.kind, alert.message, alert.pair)

    cal = calendar_context(bundle)
    try:
        from api.paperdesk import run_auto_paper

        # Same cadence as the board poll — not a separate loop.
        run_auto_paper(cfg, rows=rows, now=now)
    except Exception:
        # A paper-journal failure must not blank the decision board.
        pass
    return {
        "timezone": timezone_name(cfg),
        "timezone_tag": timezone_tag(cfg),
        "refreshed_at_dhaka": fmt_display(datetime.now(timezone.utc), cfg, seconds=True),
        "refresh_seconds": int(wl.refresh_seconds),
        "data_refresh_seconds": data_refresh_seconds(),
        "count": len(rows),
        "rows": [row_json(r, now=now, cfg=cfg) for r in rows],
        "alerts": alerts,
        "calendar": {
            "error": cal["error"],
            "stale_cache": cal["stale_cache"],
            "note": cal["note"],
            "count": cal["count"],
            "fetched_at": cal["fetched_at"],
            "source": cal["source"],
        },
    }


def _duration_text(interval: str, horizon_bars: int | None) -> str:
    if not horizon_bars:
        return ""
    bars = int(horizon_bars)
    unit = {"15m": "m", "1h": "h", "4h": "h", "1d": "d"}.get(interval, "bars")
    mult = {"15m": 15, "1h": 1, "4h": 4, "1d": 1}.get(interval, 1)
    if unit == "bars":
        return f"≤ {bars} bars"
    total = bars * mult
    return f"≤ {total} {unit}"


def _level_gap(row: Any, validity: str, *, has_price: bool) -> str:
    """Why stop/target are absent. Never a silent dash when the brief can say why."""
    status = str(getattr(row, "status", "") or "")
    if not has_price:
        return "need Train" if status == "need_train" else "need Fetch"
    if status == "need_train":
        return "need Train"
    if validity == VALIDITY_STALE:
        return "data stale"
    if validity == VALIDITY_ERROR:
        return "data error"
    return "no barriers"


def _scenario(
    pair: str,
    interval: str,
    row: Any,
    *,
    stop: float | None,
    target: float | None,
    live_signal: str | None,
) -> str:
    tf = tf_label(interval)
    validity = str(getattr(row, "validity", "") or "").upper().split()[0]
    barriers = ""
    if stop is not None and target is not None and not live_signal:
        barriers = (
            f" Research barriers (not an order): stop {price_text(pair, stop)}"
            f" / target {price_text(pair, target)}."
        )
    if validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        last = str(getattr(row, "raw_signal", "") or "").upper()
        note = str(getattr(row, "validity_reason", "") or validity).strip()
        extra = f" Last model {last} is not a live call." if last in {"BUY", "SELL", "HOLD"} else ""
        if validity == VALIDITY_STALE and "not a live call" not in extra:
            extra += " not a live call."
        return f"If scenario changes: n/a — {note}.{extra}{barriers}".strip()
    if not live_signal or stop is None:
        status = str(getattr(row, "status", "") or "no directional level")
        if status == "need_fetch" or status == "need_train":
            return f"If scenario changes: n/a — need Fetch/Train ({interval}).{barriers}"
        if barriers:
            return f"If scenario changes: n/a — HOLD has no directional call.{barriers}"
        return "If scenario changes: n/a — no live stop on this bar."
    level = price_text(pair, stop)
    if live_signal == "BUY":
        return f"If scenario changes: break below {level} flips {tf} bias to neutral / sell."
    if live_signal == "SELL":
        return f"If scenario changes: break above {level} flips {tf} bias to neutral / buy."
    return "If scenario changes: n/a — no directional bias."


def _atr_pips(ohlcv: pd.DataFrame | None, pair: str, cfg: dict[str, Any]) -> float | None:
    if ohlcv is None or ohlcv.empty:
        return None
    period = int(cfg.get("atr_period") or 14)
    series = true_range_atr(ohlcv, period)
    if series is None or series.empty or not pd.notna(series.iloc[-1]):
        return None
    pip = pip_size_for_pair(pair, cfg)
    if pip <= 0:
        return None
    return float(series.iloc[-1]) / pip


def suggestion_from_row(row: Any, cfg: dict[str, Any], *, ohlcv: pd.DataFrame | None = None) -> dict[str, Any]:
    pair = str(row.pair).upper()
    interval = str(row.timeframe or "1h")
    validity = str(row.validity or VALIDITY_MISSING).upper().split()[0]
    flashed = str(row.buy_sell or "").upper()
    live = flashed in LIVE_SIGNALS and validity not in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}
    signal = flashed if live else None
    now_px = _num(getattr(row, "close", None))
    quote = getattr(row, "quote", None)
    if now_px is None and quote is not None:
        now_px = _num(getattr(quote, "last", None))
    if now_px is None and ohlcv is not None and not ohlcv.empty and "Close" in ohlcv.columns:
        now_px = _num(ohlcv["Close"].iloc[-1])
    levels = _barrier_levels(ohlcv, cfg) if now_px is not None else None
    side = flashed if flashed in {"BUY", "SELL"} else str(getattr(row, "raw_signal", "") or "").upper()
    stop: float | None = None
    target: float | None = None
    horizon: int | None = None
    if levels is not None and now_px is not None:
        if side == "SELL":
            stop, target = float(levels["short_sl"]), float(levels["short_tp"])
        else:
            # BUY uses the long barriers. HOLD / weak use the same ATR band
            # around last close (lower = stop, upper = target). Not an order.
            stop, target = float(levels["long_sl"]), float(levels["long_tp"])
        horizon = int(levels["horizon"])
    else:
        risk = getattr(row, "risk", None)
        if risk is not None and getattr(risk, "horizon", None):
            try:
                horizon = int(risk.horizon)
            except (TypeError, ValueError):
                horizon = None
        if horizon is None:
            try:
                horizon = int(cfg.get("horizon") or 0) or None
            except (TypeError, ValueError):
                horizon = None
    gap = ""
    if stop is None or target is None:
        stop = None
        target = None
        gap = _level_gap(row, validity, has_price=now_px is not None)
    if signal == "BUY":
        chip = "Potential BUY"
        tone = "buy"
    elif signal == "SELL":
        chip = "Potential SELL"
        tone = "sell"
    elif validity == VALIDITY_STALE:
        chip = "STALE"
        tone = "stale"
    elif (
        str(row.status) == "need_train"
        and now_px is not None
        and validity not in {VALIDITY_MISSING, VALIDITY_ERROR}
    ):
        chip = "need Train"
        tone = "miss"
    elif validity in {VALIDITY_MISSING, VALIDITY_ERROR} or str(row.status) in {"need_fetch", "need_train"}:
        chip = "MISSING"
        tone = "miss"
    elif flashed == "HOLD":
        chip = "HOLD"
        tone = "hold"
    else:
        chip = flashed if flashed in {"HOLD", "—"} else "—"
        tone = "miss"
    raw_reason = str(getattr(row, "validity_reason", "") or "").strip()
    failed = validity in {VALIDITY_MISSING, VALIDITY_ERROR} or str(getattr(row, "status", "") or "") == "need_fetch"
    if failed:
        shown_reason = format_failure_reason(
            interval,
            raw_reason or "no OHLCV cache",
            last_ok=getattr(row, "last_fetch_at", None),
        )
    else:
        shown_reason = raw_reason
    rationale = str(getattr(row, "rationale", "") or "").strip()
    if not rationale:
        rationale = str(getattr(row, "signal_details", "") or "").strip()
    risk = getattr(row, "risk", None)
    atr_pips = _atr_pips(ohlcv, pair, cfg) if ohlcv is not None else _num(getattr(risk, "atr", None))
    if atr_pips is not None and ohlcv is None:
        pip = pip_size_for_pair(pair, cfg)
        atr_pips = float(atr_pips) / pip if pip else None
    duration = _duration_text(interval, horizon if stop is not None else None)
    if not duration:
        duration = gap or "no barriers"
    scenario = _scenario(pair, interval, row, stop=stop, target=target, live_signal=signal)
    if failed and shown_reason:
        last_model = str(getattr(row, "raw_signal", "") or "").upper()
        if last_model in {"BUY", "SELL", "HOLD"}:
            scenario = f"{shown_reason.rstrip('.')}. Last model {last_model} is not a live call."
        else:
            scenario = shown_reason
    elif str(getattr(row, "status", "") or "") == "need_train" and "not copied" in shown_reason:
        scenario = shown_reason
    return {
        "interval": interval,
        "tf": tf_label(interval),
        "signal": signal,
        "chip": chip,
        "tone": tone,
        "validity": validity,
        "validity_reason": shown_reason,
        "status": str(getattr(row, "status", "") or ""),
        "now": now_px,
        "now_text": price_text(pair, now_px) if now_px is not None else "—",
        "stop": stop,
        "stop_text": price_text(pair, stop) if stop is not None else gap,
        "target": target,
        "target_text": price_text(pair, target) if target is not None else gap,
        "duration": duration,
        "horizon_bars": horizon if stop is not None else None,
        "scenario": scenario,
        "rationale": rationale,
        "atr_pips": None if atr_pips is None else round(float(atr_pips), 1),
        "raw_signal": None if not getattr(row, "raw_signal", None) else str(row.raw_signal),
        "confidence": _num(getattr(row, "confidence", None)),
    }


def _headline(pair: str, primary: dict[str, Any]) -> tuple[str, str, str]:
    signal = primary.get("signal")
    validity = str(primary.get("validity") or "")
    tf = str(primary.get("tf") or "")
    if signal == "BUY":
        return "buy", "BUY bias", f"{pair} — bullish research bias on {tf}"
    if signal == "SELL":
        return "sell", "SELL bias", f"{pair} — bearish research bias on {tf}"
    if validity == VALIDITY_STALE:
        return "flat", "NO LIVE BIAS", f"{pair} — data stale, not a live call"
    if validity in {VALIDITY_MISSING, VALIDITY_ERROR} or primary.get("status") in {"need_fetch", "need_train"}:
        return "flat", "NO LIVE BIAS", f"{pair} — need Fetch/Train"
    if str(primary.get("chip")) == "HOLD":
        return "flat", "HOLD", f"{pair} — no directional call on {tf}"
    return "flat", "NO LIVE BIAS", f"{pair} — no live bias on {tf}"


def build_brief(pair: str, tf: str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    primary_iv = parse_interval(tf, default=HOURLY_INTERVAL)
    hourly_row = build_board_row(symbol, cfg, interval=HOURLY_INTERVAL, refresh_data=False, regenerate=False)
    daily_row = build_board_row(symbol, cfg, interval=DAILY_INTERVAL, refresh_data=False, regenerate=False)
    if primary_iv == HOURLY_INTERVAL:
        primary_row = hourly_row
    elif primary_iv == DAILY_INTERVAL:
        primary_row = daily_row
    else:
        primary_row = build_board_row(symbol, cfg, interval=primary_iv, refresh_data=False, regenerate=False)

    hourly_bars = load_cached_ohlcv(symbol, cfg, HOURLY_INTERVAL)
    daily_bars = load_cached_ohlcv(symbol, cfg, DAILY_INTERVAL)
    primary_bars = (
        hourly_bars
        if primary_iv == HOURLY_INTERVAL
        else daily_bars
        if primary_iv == DAILY_INTERVAL
        else load_cached_ohlcv(symbol, cfg, primary_iv)
    )
    bundle = load_calendar(cfg)
    events = list(bundle.events)
    clock = _clock(None)
    attach_next_event(primary_row, events, now=clock, cfg=cfg)
    event = next_event_payload(symbol, events, cfg, clock)
    hourly = _annotate_suggestion(suggestion_from_row(hourly_row, cfg, ohlcv=hourly_bars), event)
    daily = _annotate_suggestion(suggestion_from_row(daily_row, cfg, ohlcv=daily_bars), event)
    primary = _annotate_suggestion(suggestion_from_row(primary_row, cfg, ohlcv=primary_bars), event)
    ensure_consensus(symbol, cfg)
    hourly_c = read_consensus(symbol, "hourly", cfg)
    daily_c = read_consensus(symbol, "daily", cfg)
    from api.paperdesk import paper_snapshot

    paper = paper_snapshot(symbol, cfg, primary_row)
    cards = suggest_actions(
        pair=symbol,
        signal=getattr(primary_row, "raw_signal", None) or getattr(primary_row, "buy_sell", None),
        validity=str(primary.get("validity") or ""),
        position=paper.get("position"),
        events=events,
        cfg=cfg,
        ohlcv=primary_bars,
        mtf=getattr(primary_row, "mtf", None),
        last_price=primary.get("now"),
        now=clock,
    )
    _attach_event_stop([hourly, daily, primary], symbol, cards)
    tone, bias, headline = _headline(symbol, primary)
    parts = [lean_label(hourly_c if primary_iv != DAILY_INTERVAL else daily_c)]
    if primary.get("mtf") or getattr(primary_row, "mtf", None) is not None:
        mtf = getattr(primary_row, "mtf", None)
        if mtf is not None and getattr(mtf, "status", None):
            parts.append(f"MTF {mtf.status}")
    atr = primary.get("atr_pips")
    if isinstance(atr, (int, float)):
        parts.append(f"ATR 14 ≈ {atr:.0f} pips")
    if primary.get("validity") in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        reason = str(getattr(primary_row, "validity_reason", "") or primary.get("validity"))
        if reason:
            parts.append(reason)
    if event is not None:
        if event.get("window") not in {None, "", "none"}:
            parts.append(f"Event {event['window']} · {event['label']}")
        else:
            parts.append(f"Next event {event['label']}")
    rationale = str(primary.get("rationale") or "").strip()
    cal = calendar_context(bundle)
    calendar_note = cal["note"]
    if calendar_note is None and event is None and not bundle.error:
        calendar_note = "No high-impact event for this pair in the window."
    model_build = model_build_status(symbol, cfg, now=clock)
    return {
        "pair": symbol,
        "tf": tf_label(primary_iv),
        "interval": primary_iv,
        "bias": bias,
        "bias_tone": tone,
        "confidence": primary.get("confidence"),
        "headline": headline,
        "sub": " · ".join(p for p in parts if p),
        "rationale": rationale,
        "primary": primary,
        "hourly": hourly,
        "daily": daily,
        "consensus": {"hourly": hourly_c, "daily": daily_c},
        "paper": paper,
        "next_event": event,
        "advice": [_advice_json(card, symbol) for card in cards],
        "calendar_error": bundle.error,
        "calendar_stale": bool(bundle.stale_cache),
        "calendar_note": calendar_note,
        "model_build": model_build,
    }


def ohlcv_payload(
    pair: str,
    *,
    interval: str | None = None,
    bars: int = 180,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    iv = parse_interval(interval, default=str(cfg.get("interval") or "1h"))
    if iv not in OHLCV_INTERVALS:
        raise WatchlistError(f"Unsupported interval {iv}")
    try:
        n = int(bars)
    except (TypeError, ValueError):
        n = 180
    n = max(20, min(n, 500))
    frame = load_cached_ohlcv(symbol, cfg, iv)
    fresh = assess_ohlcv(frame, iv, cfg)
    if frame is None or frame.empty:
        return {
            "pair": symbol,
            "interval": iv,
            "tf": tf_label(iv),
            "validity": fresh.validity,
            "reason": fresh.reason or "no OHLCV cache",
            "bars": [],
            "digits": chart_price_digits(symbol),
            "indicators": empty_indicators(),
            "note": "MISSING — no cached bars. Fetch required. Nothing is invented.",
        }
    tail = frame.tail(n)
    out_bars: list[dict[str, Any]] = []
    for ts, rec in tail.iterrows():
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        else:
            stamp = stamp.tz_convert("UTC")
        out_bars.append(
            {
                "time": int(stamp.timestamp()),
                "open": float(rec["Open"]),
                "high": float(rec["High"]),
                "low": float(rec["Low"]),
                "close": float(rec["Close"]),
                "volume": float(rec["Volume"]) if pd.notna(rec["Volume"]) else 0.0,
            }
        )
    note = ""
    if fresh.validity == VALIDITY_STALE:
        note = "STALE — cached bars, not a live feed. Refresh before treating this as current."
    elif fresh.validity == VALIDITY_CLOSED:
        note = "CLOSED — last cached session. Not a live broker chart."
    elif fresh.validity == VALIDITY_OK:
        note = "Cached OHLCV within the freshness window. Not a broker quote."
    else:
        note = fresh.reason or fresh.validity
    return {
        "pair": symbol,
        "interval": iv,
        "tf": tf_label(iv),
        "validity": fresh.validity,
        "reason": fresh.reason,
        "last_bar_dhaka": fmt_display(fresh.last_bar, cfg, seconds=True) if fresh.last_bar else "n/a",
        "bars": out_bars,
        "digits": chart_price_digits(symbol),
        "indicators": chart_indicators_aligned(frame, tail.index),
        "note": note,
    }


def _note_failed_refresh(row: Any, reason: str) -> None:
    """A failed poll must not stay Live. Keep the cached bar's real age."""
    note = f"refresh failed ({str(reason or 'yfinance').strip()})"
    has_bar = str(getattr(row, "last_bar_at", "") or "").strip().lower() not in {"", "n/a", "none"}
    row.validity = VALIDITY_STALE if has_bar else VALIDITY_ERROR
    prev = str(getattr(row, "validity_reason", "") or "").strip()
    if has_bar:
        row.validity_reason = f"{note}. {prev}".strip() if prev else note
    else:
        row.validity_reason = format_failure_reason(
            str(getattr(row, "timeframe", "") or ""),
            note if not prev else f"{note}. {prev}",
            last_ok=getattr(row, "last_fetch_at", None),
        )
    flashed = str(getattr(row, "buy_sell", "") or "").upper()
    if flashed in {"BUY", "SELL"}:
        row.raw_signal = getattr(row, "raw_signal", None) or row.buy_sell
        row.buy_sell = "—"


def _watch_targets(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    wl = load_wl(cfg)
    default = wl.lab_interval(cfg)
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in wl.pairs:
        symbol = normalize_pair(item.pair)
        iv = parse_interval(item.resolved_interval(default), default=default)
        key = (symbol, iv)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _hourly_before_daily(targets: list[tuple[str, str]], symbol: str) -> list[tuple[str, str]]:
    """Refresh 1h before 1d so a missing daily file can be aggregated from 1h."""
    deferred: list[tuple[str, str]] = []
    out: list[tuple[str, str]] = []
    flushed = False
    for pair, iv in targets:
        if pair == symbol and iv == DAILY_INTERVAL:
            deferred.append((pair, iv))
            continue
        out.append((pair, iv))
        if pair == symbol and iv == HOURLY_INTERVAL and not flushed:
            out.extend(deferred)
            deferred = []
            flushed = True
    out.extend(deferred)
    return out


def expand_active_intervals(
    targets: list[tuple[str, str]],
    active: str | None,
) -> list[tuple[str, str]]:
    """Heavy H1+D1 fill for the one Active pair. Other pairs stay on their interval."""
    text = str(active or "").strip()
    if not text:
        return targets
    symbol = normalize_pair(text)
    out = list(targets)
    seen = set(out)
    for iv in (HOURLY_INTERVAL, DAILY_INTERVAL):
        key = (symbol, iv)
        if key not in seen:
            out.append(key)
            seen.add(key)
    return _hourly_before_daily(out, symbol)


def _decision_intervals(primary: str) -> list[str]:
    """1h, the requested interval, then 1d. Daily is last so resample can see fresh 1h."""
    ordered: list[str] = []
    for iv in (HOURLY_INTERVAL, primary, DAILY_INTERVAL):
        if iv not in ordered:
            ordered.append(iv)
    if DAILY_INTERVAL in ordered:
        ordered = [iv for iv in ordered if iv != DAILY_INTERVAL] + [DAILY_INTERVAL]
    return ordered


def _dedupe_targets(raw: list[tuple[str, str | None]], cfg: dict[str, Any]) -> list[tuple[str, str]]:
    default = str(cfg.get("interval") or "1h")
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair, interval in raw:
        symbol = normalize_pair(pair)
        iv = parse_interval(interval, default=default)
        key = (symbol, iv)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def refresh_one(pair: str, *, interval: str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """One pair's market-data refresh, sharing the POST /refresh limiter.

    Does not train, backtest, or generate signals.
    """
    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    iv = parse_interval(interval, default=str(cfg.get("interval") or "1h"))
    allowed, retry = allow(f"{symbol}:{iv}")
    if not allowed:
        row = build_board_row(symbol, cfg, interval=iv, refresh_data=False, regenerate=False)
        cached = row_json(row, cfg=cfg)
        return {
            "ok": True,
            "rate_limited": True,
            "retry_after_s": round(retry, 1),
            "fetch_failed": False,
            "fetch_error": None,
            "pair": symbol,
            "interval": iv,
            "row": cached,
            "source": "cache",
            "detail": "Network refresh is waiting. Cached board was re-read.",
        }
    return refresh_pair(symbol, interval=iv, cfg=cfg)


def _yf_rate_limited(result: dict[str, Any]) -> bool:
    if result.get("rate_limited"):
        return True
    if not result.get("fetch_failed"):
        return False
    return is_rate_limited_reason(str(result.get("fetch_error") or ""))


def refresh_watchlist(
    pairs: list[tuple[str, str | None]] | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    active: str | None = None,
) -> dict[str, Any]:
    """Refresh OHLCV for the watchlist (or an explicit pair list).

    Market data only. Does not call the research pipeline.
    ``pairs is None`` uses the saved watchlist. An empty list refreshes nothing.
    ``active`` is the one Decision subject: that pair also gets 1h and 1d.
    Other pairs stay on the interval they were asked for.
    """
    cfg = cfg if cfg is not None else app_config()
    targets = _watch_targets(cfg) if pairs is None else _dedupe_targets(pairs, cfg)
    targets = expand_active_intervals(targets, active)
    results = [refresh_one(symbol, interval=iv, cfg=cfg) for symbol, iv in targets]
    updated = any(not item.get("rate_limited") and not item.get("fetch_failed") for item in results)
    hard_fail = [item for item in results if item.get("fetch_failed") and not _yf_rate_limited(item)]
    limited = [item for item in results if _yf_rate_limited(item)]
    waits = [float(item.get("retry_after_s") or 0) for item in limited if item.get("rate_limited")]
    if hard_fail:
        reason = "error"
        rate_limited = False
        fetch_failed = True
    elif not updated and limited:
        reason = "rate_limited"
        rate_limited = True
        fetch_failed = False
    else:
        reason = None
        rate_limited = False
        fetch_failed = False
    retry = max(waits) if waits else 0.0
    if reason == "rate_limited" and retry <= 0:
        retry = float(data_refresh_seconds())
    return {
        "ok": True,
        "updated": bool(updated or not results),
        "rate_limited": rate_limited,
        "fetch_failed": fetch_failed,
        "reason": reason,
        "retry_after_s": round(retry, 1),
        "data_refresh_seconds": data_refresh_seconds(),
        "refreshed_at_dhaka": fmt_display(datetime.now(timezone.utc), cfg, seconds=True),
        "count": len(results),
        "results": results,
    }


def refresh_pair(pair: str, *, interval: str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Polled yfinance refresh + cache re-read. Never writes synthetic bars.

    Not a broker tick stream. OHLCV is fetched even when no model is trained so
    the chart's last bar can move. Signal regen still needs a model. This does
    not scrape external forecasters: that runs only for the one Active pair.
    """
    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    iv = parse_interval(interval, default=str(cfg.get("interval") or "1h"))
    fetched, source, reason = ensure_interval_ohlcv(
        symbol,
        cfg,
        iv,
        incremental=True,
        refresh=try_yfinance_refresh,
    )
    row = build_board_row(symbol, cfg, interval=iv, refresh_data=False, regenerate=True)
    fetch_failed = fetched is None
    if fetch_failed:
        _note_failed_refresh(row, reason)
    if source == SOURCE_RESAMPLED_FROM_1H:
        source_label = SOURCE_RESAMPLED_FROM_1H
    elif source == SOURCE_YFINANCE:
        source_label = SOURCE_YFINANCE
    elif source == "cache":
        source_label = f"cache ({reason})"
    else:
        source_label = f"missing ({reason})"
    return {
        "ok": True,
        "rate_limited": False,
        "retry_after_s": 0,
        "fetch_failed": fetch_failed,
        "fetch_error": None if not fetch_failed else str(reason or ""),
        "pair": symbol,
        "interval": iv,
        "row": row_json(row, cfg=cfg),
        "source": source_label,
        "cache_source": source,
        "provider_note": reason if source == SOURCE_RESAMPLED_FROM_1H and reason else None,
    }


def refresh_active_pair(
    pair: str,
    *,
    interval: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh the Active subject: its interval, plus 1h and 1d.

    1h runs before 1d. Each interval keeps its own rate-limit key.
    The returned object is the requested interval; ``intervals`` lists the rest.
    """
    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    primary = parse_interval(interval, default=str(cfg.get("interval") or "1h"))
    results = [refresh_one(symbol, interval=iv, cfg=cfg) for iv in _decision_intervals(primary)]
    chosen = next(item for item in results if item.get("interval") == primary)
    return {
        **chosen,
        "ensured": [str(item.get("interval") or "") for item in results],
        "intervals": results,
    }


def run_pipeline_pair(
    pair: str,
    *,
    fetch: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """train → backtest → generate. Optional fetch first. Stops on the first failure."""
    from forex_lab.ui import pipeline as pl

    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    steps: list[dict[str, Any]] = []

    def _step(name: str, fn, **kwargs) -> tuple[int, str]:
        rc, log = fn(symbol, cfg=cfg, **kwargs)
        text = str(log or "")
        if len(text) > 4000:
            text = text[-4000:]
        steps.append({"step": name, "ok": rc == 0, "log": text})
        return rc, text

    if fetch:
        rc, _log = _step("fetch", pl.run_fetch)
        if rc != 0:
            return {"ok": False, "pair": symbol, "failed": "fetch", "steps": steps}
    rc, _log = _step("train", pl.run_train)
    if rc != 0:
        return {"ok": False, "pair": symbol, "failed": "train", "steps": steps}
    rc, _log = _step("backtest", pl.run_backtest)
    if rc != 0:
        return {"ok": False, "pair": symbol, "failed": "backtest", "steps": steps}
    rc, _log = _step("signals", pl.run_signals)
    if rc != 0:
        return {"ok": False, "pair": symbol, "failed": "signals", "steps": steps}
    return {"ok": True, "pair": symbol, "failed": None, "steps": steps}
