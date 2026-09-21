"""JSON views over existing forex_lab board, bars, and pipeline helpers.

No model rewrite. STALE and MISSING stay visible; prices are never invented.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from forex_lab.clock import fmt_display, parse_ts, timezone_name, timezone_tag
from forex_lab.config_loader import load_config, pip_size_for_pair
from forex_lab.data import load_cached_ohlcv
from forex_lab.features import true_range_atr
from forex_lab.freshness import (
    VALIDITY_CLOSED,
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
    assess_ohlcv,
    fx_session_open,
    now_utc,
)
from forex_lab.session import classify_session, session_windows
from forex_lab.ui.alerts import process_watch, visible_alerts
from forex_lab.ui.board import build_board_row, build_board_rows
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


def _age_s(label: object, *, now: datetime | None = None) -> float | None:
    ts = parse_ts(label)
    if ts is None:
        return None
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return (clock - ts).total_seconds()


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


def row_json(row: Any, *, now: datetime | None = None) -> dict[str, Any]:
    pair = str(row.pair).upper()
    interval = str(row.timeframe or "1h")
    validity = str(row.validity or VALIDITY_MISSING).upper().split()[0]
    signal = str(row.buy_sell or "—").upper()
    if signal not in {"BUY", "SELL", "HOLD"}:
        signal = "—"
    last_px = _num(getattr(row, "close", None))
    quote = getattr(row, "quote", None)
    if last_px is None and quote is not None:
        last_px = _num(getattr(quote, "last", None))
    target = _target_price(row)
    age = _age_s(getattr(row, "last_bar_at", None), now=now)
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
        "validity_reason": str(getattr(row, "validity_reason", "") or ""),
        "data": data_view(validity),
        "session": session_view(row),
        "age": compact_age(age),
        "age_s": None if age is None else round(age, 1),
        "last_bar_dhaka": fmt_display(getattr(row, "last_bar_at", None), seconds=True),
        "last_fetch_dhaka": fmt_display(getattr(row, "last_fetch_at", None), seconds=True),
        "last_signal_dhaka": fmt_display(getattr(row, "last_signal_at", None), seconds=True),
        "status": str(getattr(row, "status", "") or ""),
        "confidence": _num(getattr(row, "confidence", None)),
        "rationale": str(getattr(row, "rationale", "") or ""),
        "details": str(getattr(row, "signal_details", "") or ""),
        "mtf": None if mtf is None else str(getattr(mtf, "status", "") or ""),
        "mtf_note": None if mtf is None else str(getattr(mtf, "note", "") or ""),
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


def board_payload(
    cfg: dict[str, Any] | None = None,
    *,
    refresh_data: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else app_config()
    wl = load_wl(cfg)
    rows = build_board_rows(wl, cfg, refresh_data=refresh_data, regenerate=True if refresh_data else False)
    _state, _fresh = process_watch(
        rows,
        calendar=None,
        cfg=cfg,
        now=now_utc(now) if now is not None else None,
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

    return {
        "timezone": timezone_name(cfg),
        "timezone_tag": timezone_tag(cfg),
        "refreshed_at_dhaka": fmt_display(datetime.now(timezone.utc), cfg, seconds=True),
        "refresh_seconds": int(wl.refresh_seconds),
        "count": len(rows),
        "rows": [row_json(r, now=now) for r in rows],
        "alerts": alerts,
    }


def _duration_text(interval: str, horizon_bars: int | None, *, live: bool) -> str:
    if not live or not horizon_bars:
        return "—"
    bars = int(horizon_bars)
    unit = {"15m": "m", "1h": "h", "4h": "h", "1d": "d"}.get(interval, "bars")
    mult = {"15m": 15, "1h": 1, "4h": 4, "1d": 1}.get(interval, 1)
    if unit == "bars":
        return f"≤ {bars} bars"
    total = bars * mult
    return f"≤ {total} {unit}"


def _scenario(pair: str, interval: str, row: Any, *, stop: float | None, live_signal: str | None) -> str:
    tf = tf_label(interval)
    validity = str(getattr(row, "validity", "") or "").upper().split()[0]
    if validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        last = str(getattr(row, "raw_signal", "") or "").upper()
        note = str(getattr(row, "validity_reason", "") or validity).strip()
        extra = f" Last model {last} is not a live call." if last in {"BUY", "SELL", "HOLD"} else ""
        return f"If scenario changes: n/a — {note}.{extra}".strip()
    if not live_signal or stop is None:
        status = str(getattr(row, "status", "") or "no directional level")
        if status == "need_fetch" or status == "need_train":
            return f"If scenario changes: n/a — need Fetch/Train ({interval})."
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
    stop = _stop_price(row) if live else None
    target = _target_price(row) if live else None
    horizon = None
    risk = getattr(row, "risk", None)
    if risk is not None and getattr(risk, "horizon", None):
        horizon = int(risk.horizon)
    else:
        try:
            horizon = int(cfg.get("horizon") or 0) or None
        except (TypeError, ValueError):
            horizon = None
    if signal == "BUY":
        chip = "Potential BUY"
        tone = "buy"
    elif signal == "SELL":
        chip = "Potential SELL"
        tone = "sell"
    elif validity == VALIDITY_STALE:
        chip = "STALE"
        tone = "stale"
    elif validity in {VALIDITY_MISSING, VALIDITY_ERROR} or str(row.status) in {"need_fetch", "need_train"}:
        chip = "MISSING"
        tone = "miss"
    elif flashed == "HOLD":
        chip = "HOLD"
        tone = "hold"
    else:
        chip = flashed if flashed in {"HOLD", "—"} else "—"
        tone = "miss"
    rationale = str(getattr(row, "rationale", "") or "").strip()
    if not rationale:
        rationale = str(getattr(row, "signal_details", "") or "").strip()
    atr_pips = _atr_pips(ohlcv, pair, cfg) if ohlcv is not None else _num(getattr(risk, "atr", None))
    if atr_pips is not None and ohlcv is None:
        pip = pip_size_for_pair(pair, cfg)
        atr_pips = float(atr_pips) / pip if pip else None
    return {
        "interval": interval,
        "tf": tf_label(interval),
        "signal": signal,
        "chip": chip,
        "tone": tone,
        "validity": validity,
        "status": str(getattr(row, "status", "") or ""),
        "now": now_px,
        "now_text": price_text(pair, now_px) if now_px is not None else "—",
        "stop": stop,
        "stop_text": price_text(pair, stop),
        "target": target,
        "target_text": price_text(pair, target),
        "duration": _duration_text(interval, horizon, live=live),
        "scenario": _scenario(pair, interval, row, stop=stop, live_signal=signal),
        "rationale": rationale,
        "atr_pips": None if atr_pips is None else round(float(atr_pips), 1),
        "raw_signal": None if not getattr(row, "raw_signal", None) else str(row.raw_signal),
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
    hourly = suggestion_from_row(hourly_row, cfg, ohlcv=hourly_bars)
    daily = suggestion_from_row(daily_row, cfg, ohlcv=daily_bars)
    primary = suggestion_from_row(primary_row, cfg, ohlcv=primary_bars)
    ensure_consensus(symbol, cfg)
    hourly_c = read_consensus(symbol, "hourly", cfg)
    daily_c = read_consensus(symbol, "daily", cfg)
    from api.paperdesk import paper_snapshot
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
    rationale = str(primary.get("rationale") or "").strip()
    return {
        "pair": symbol,
        "tf": tf_label(primary_iv),
        "interval": primary_iv,
        "bias": bias,
        "bias_tone": tone,
        "headline": headline,
        "sub": " · ".join(p for p in parts if p),
        "rationale": rationale,
        "primary": primary,
        "hourly": hourly,
        "daily": daily,
        "consensus": {"hourly": hourly_c, "daily": daily_c},
        "paper": paper_snapshot(symbol, cfg, primary_row),
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
        "note": note,
    }


def refresh_pair(pair: str, *, interval: str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rate-limited yfinance refresh + signal regen. Never writes synthetic bars."""
    cfg = cfg if cfg is not None else app_config()
    symbol = normalize_pair(pair)
    iv = parse_interval(interval, default=str(cfg.get("interval") or "1h"))
    row = build_board_row(symbol, cfg, interval=iv, refresh_data=True, regenerate=True)
    ensure_consensus(symbol, cfg)
    return {
        "ok": True,
        "rate_limited": False,
        "retry_after_s": 0,
        "pair": symbol,
        "interval": iv,
        "row": row_json(row),
        "source": str(getattr(row, "data_source", "") or ""),
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
