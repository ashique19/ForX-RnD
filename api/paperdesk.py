"""Paper Buy/Sell/Close for the Decision desk. Journal only — never a live venue.

Auto paper uses the same PaperBroker store. It never calls a live backend.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from forex_lab.broker import BrokerError, PaperBroker, make_broker, position_for_pair
from forex_lab.clock import fmt_display, parse_ts, timezone_name
from forex_lab.data import load_cached_ohlcv
from forex_lab.freshness import INTERVAL_SECONDS
from forex_lab.score import normalize_outcome
from forex_lab.ui.board import (
    conf_label,
    paper_submit_allowed,
    paper_submit_block_reason,
    paper_submit_risk_defaults,
)

_PAPER_LOCK = threading.RLock()
_SECONDS_IN_STAMP = re.compile(r"\d{1,2}:\d{2}:\d{2}")
_CLOSED_LIMIT = 40

def _desk():
    from api import deskdata

    return deskdata


class PaperBlocked(RuntimeError):
    """STALE / MISSING / ERROR — open is refused. Close is separate."""


def paper_store_path() -> Path | None:
    raw = os.environ.get("FORX_PAPER_STORE")
    return Path(raw) if raw else None


def _broker(cfg: dict[str, Any]):
    return make_broker(cfg, path=paper_store_path())


def _price_from_cache(pair: str, cfg: dict[str, Any], interval: str, row: Any) -> tuple[float | None, str]:
    frame = load_cached_ohlcv(pair, cfg, interval)
    if frame is not None and not frame.empty and "Close" in frame.columns:
        return float(frame["Close"].iloc[-1]), str(frame.index[-1])
    close = getattr(row, "close", None)
    try:
        px = float(close) if close is not None else None
    except (TypeError, ValueError):
        px = None
    if px is None or px <= 0:
        return None, ""
    return px, str(getattr(row, "last_bar_at", "") or "")


def _position_json(pos: dict[str, Any] | None, cfg: dict[str, Any]) -> dict[str, Any] | None:
    if not pos:
        return None
    return {
        "id": pos.get("id"),
        "pair": pos.get("pair"),
        "side": pos.get("side"),
        "size": pos.get("size"),
        "entry_price": pos.get("entry_price"),
        "entry_time_dhaka": fmt_display(pos.get("entry_time"), cfg, seconds=True),
        "sl": pos.get("sl"),
        "tp": pos.get("tp"),
    }


def paper_snapshot(pair: str, cfg: dict[str, Any], row: Any) -> dict[str, Any]:
    symbol = str(pair).upper()
    broker = _broker(cfg)
    pos = position_for_pair(broker, symbol)
    allowed = paper_submit_allowed(getattr(row, "validity", None), row, cfg)
    reason = paper_submit_block_reason(getattr(row, "validity", None), row, cfg)
    if pos is not None:
        allowed = False
        reason = reason or f"{symbol} already has an open paper position — close it first"
    size = float((cfg.get("broker") or {}).get("default_size") or 1.0)
    return {
        "allowed": allowed,
        "block_reason": reason,
        "default_size": size,
        "position": _position_json(pos, cfg),
    }


def paper_order(
    pair: str,
    side: str,
    *,
    size: float | None = None,
    interval: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _PAPER_LOCK:
        return _paper_order_impl(pair, side, size=size, interval=interval, cfg=cfg)


def _paper_order_impl(
    pair: str,
    side: str,
    *,
    size: float | None = None,
    interval: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    from forex_lab.ui.watchlist import normalize_pair

    symbol = normalize_pair(pair)
    iv = desk.parse_interval(interval, default=str(cfg.get("interval") or "1h"))
    row = desk.build_board_row(symbol, cfg, interval=iv, refresh_data=False, regenerate=False)
    broker = _broker(cfg)
    action = str(side or "").upper()
    if action == "CLOSE":
        pos = position_for_pair(broker, symbol)
        if pos is None:
            raise BrokerError(f"no open paper position for {symbol}")
        price, _bar = _price_from_cache(symbol, cfg, iv, row)
        if price is None:
            raise BrokerError("no cached price — cannot close paper position")
        broker.close(str(pos["id"]), price=price, reason="manual")
        return {
            "ok": True,
            "pair": symbol,
            "side": "CLOSE",
            "message": f"Paper close recorded @ {price:.5f} — local journal only.",
            "paper": paper_snapshot(symbol, cfg, row),
        }
    if action not in {"BUY", "SELL"}:
        raise BrokerError("side must be BUY, SELL, or CLOSE")
    if not paper_submit_allowed(row.validity, row, cfg):
        raise PaperBlocked(
            paper_submit_block_reason(row.validity, row, cfg) or "Paper BUY/SELL disabled"
        )
    price, entry_bar = _price_from_cache(symbol, cfg, iv, row)
    if price is None:
        raise BrokerError("no cached price — cannot paper-fill")
    frame = load_cached_ohlcv(symbol, cfg, iv)
    sl, tp = paper_submit_risk_defaults(frame, cfg, action, row.validity)
    qty = float(size) if size is not None else float((cfg.get("broker") or {}).get("default_size") or 1.0)
    broker.submit(
        action,
        symbol,
        size=qty,
        sl=sl,
        tp=tp,
        price=price,
        timeframe=iv,
        validity=row.validity,
        model_signal=getattr(row, "raw_signal", None) or getattr(row, "buy_sell", None),
        confidence=getattr(row, "confidence", None),
        dir_edge=getattr(row, "dir_edge", None),
        p_buy=getattr(row, "p_buy", None),
        p_sell=getattr(row, "p_sell", None),
        p_hold=getattr(row, "p_hold", None),
        rationale=str(getattr(row, "rationale", "") or ""),
        entry_bar_time=entry_bar,
        entry_ref="last close (paper fill; not a broker quote)",
        horizon=int(cfg.get("horizon") or 8),
        note=f"validity={row.validity}",
        source="manual",
    )
    _remember_signal(broker, symbol, action)
    snap = paper_snapshot(symbol, cfg, row)
    return {
        "ok": True,
        "pair": symbol,
        "side": action,
        "message": f"Paper {action} recorded @ {price:.5f} — local journal only.",
        "paper": snap,
    }


def _paper(cfg: dict[str, Any]) -> PaperBroker:
    broker = _broker(cfg)
    if not isinstance(broker, PaperBroker):
        raise BrokerError("auto paper requires broker.backend paper")
    return broker


def _remember_signal(broker: PaperBroker, pair: str, signal: str) -> None:
    auto = broker.read_auto()
    seen = dict(auto["seen"])
    seen[str(pair).upper()] = str(signal or "")
    broker.write_auto(seen=seen)


def _stamp(now: datetime | None) -> str:
    if now is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    clock = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _f(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def human_duration(start: datetime, end: datetime) -> str | None:
    """Wall-clock span. ``2h 15m`` style. None when the end is before the start."""
    delta = (end - start).total_seconds()
    if delta < 0:
        return None
    total = int(delta)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    if minutes:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    return f"{seconds}s"


def _dhaka(value: object, cfg: dict[str, Any]) -> str | None:
    if value is None or not str(value).strip():
        return None
    show_seconds = bool(_SECONDS_IN_STAMP.search(str(value)))
    text = fmt_display(value, cfg, seconds=show_seconds)
    if not text or text == "n/a":
        return None
    return text


def _signed_price_text(pair: str, value: float | None) -> str:
    if value is None:
        return "—"
    desk = _desk()
    body = desk.price_text(pair, abs(value))
    if body == "—":
        return "—"
    if value > 0:
        return f"+{body}"
    if value < 0:
        return f"-{body}"
    return body


def _price_move(side: str, entry: float | None, mark: float | None) -> float | None:
    if entry is None or mark is None:
        return None
    if side == "BUY":
        return mark - entry
    if side == "SELL":
        return entry - mark
    return None


def _r_multiple(side: str, entry: float | None, mark: float | None, sl: float | None) -> float | None:
    """R from the stored stop distance. Missing or inverted stops stay blank."""
    move = _price_move(side, entry, mark)
    if move is None or entry is None or sl is None:
        return None
    risk = (entry - sl) if side == "BUY" else (sl - entry)
    if risk <= 0:
        return None
    return move / risk


def _barrier_reason(pos: dict[str, Any], price: float) -> str | None:
    side = str(pos.get("side") or "").upper()
    sl = _f(pos.get("sl"))
    tp = _f(pos.get("tp"))
    if side == "BUY":
        hit_sl = sl is not None and price <= sl
        hit_tp = tp is not None and price >= tp
    elif side == "SELL":
        hit_sl = sl is not None and price >= sl
        hit_tp = tp is not None and price <= tp
    else:
        return None
    if hit_sl:
        return "sl"
    if hit_tp:
        return "tp"
    return None


def _duration_expired(pos: dict[str, Any], now: datetime) -> bool:
    """Brief duration is horizon bars × the entry timeframe. Unknown TF does not expire."""
    tf = str(pos.get("timeframe") or "")
    if tf not in INTERVAL_SECONDS:
        return False
    try:
        horizon = int(pos.get("horizon") or 0)
    except (TypeError, ValueError):
        return False
    if horizon <= 0:
        return False
    start = parse_ts(pos.get("entry_time"))
    if start is None:
        return False
    return _as_utc(now) >= start + timedelta(seconds=INTERVAL_SECONDS[tf] * horizon)


def _live_signal(suggestion: dict[str, Any]) -> str | None:
    signal = str(suggestion.get("signal") or "").upper()
    if signal in {"BUY", "SELL"}:
        return signal
    return None


def _watch_rows(cfg: dict[str, Any]) -> list[Any]:
    desk = _desk()
    wl = desk.load_wl(cfg)
    return desk.build_board_rows(wl, cfg, refresh_data=False, regenerate=False)


def _open_auto(
    broker: PaperBroker,
    row: Any,
    cfg: dict[str, Any],
    suggestion: dict[str, Any],
    *,
    side: str,
    price: float,
    entry_bar: str,
    when: str,
) -> None:
    frame = load_cached_ohlcv(str(row.pair), cfg, str(row.timeframe or ""))
    sl, tp = paper_submit_risk_defaults(frame, cfg, side, str(getattr(row, "validity", "") or ""))
    if sl is None and suggestion.get("signal") == side:
        sl = _f(suggestion.get("stop"))
    if tp is None and suggestion.get("signal") == side:
        tp = _f(suggestion.get("target"))
    confidence = _f(suggestion.get("confidence"))
    if confidence is None:
        confidence = _f(getattr(row, "confidence", None))
    raw_h = suggestion.get("horizon_bars")
    try:
        horizon = int(raw_h) if raw_h else 0
    except (TypeError, ValueError):
        horizon = 0
    qty = float((cfg.get("broker") or {}).get("default_size") or 1.0)
    broker.submit(
        side,
        str(row.pair),
        size=qty,
        sl=sl,
        tp=tp,
        price=price,
        timeframe=str(row.timeframe or ""),
        validity=getattr(row, "validity", None),
        model_signal=getattr(row, "raw_signal", None) or side,
        confidence=confidence,
        dir_edge=getattr(row, "dir_edge", None),
        p_buy=getattr(row, "p_buy", None),
        p_sell=getattr(row, "p_sell", None),
        p_hold=getattr(row, "p_hold", None),
        rationale=str(suggestion.get("rationale") or getattr(row, "rationale", "") or ""),
        entry_bar_time=entry_bar,
        entry_ref="last close (paper fill; not a broker quote)",
        horizon=horizon,
        timestamp=when,
        source="auto",
        note="auto paper from brief",
    )


def _auto_one(
    broker: PaperBroker,
    row: Any,
    cfg: dict[str, Any],
    now: datetime | None,
    seen: dict[str, str],
) -> list[str]:
    symbol = str(getattr(row, "pair", "") or "").upper()
    if not symbol:
        return []
    interval = str(getattr(row, "timeframe", "") or cfg.get("interval") or "1h")
    frame = load_cached_ohlcv(symbol, cfg, interval)
    desk = _desk()
    suggestion = desk.suggestion_from_row(row, cfg, ohlcv=frame)
    price, entry_bar = _price_from_cache(symbol, cfg, interval, row)
    live = _live_signal(suggestion)
    allowed = paper_submit_allowed(getattr(row, "validity", None), row, cfg)
    when = _stamp(now)
    clock = _as_utc(now)
    events: list[str] = []
    pos = position_for_pair(broker, symbol)
    closed_opposite = False
    if pos is not None and price is not None:
        reason = _barrier_reason(pos, price)
        if reason is None and _duration_expired(pos, clock):
            reason = "duration"
        if reason is None and allowed and live and live != str(pos.get("side") or "").upper():
            reason = "opposite"
        if reason:
            broker.close(str(pos["id"]), price=price, reason=reason, timestamp=when)
            events.append(f"{symbol} close {reason}")
            closed_opposite = reason == "opposite"
            pos = None
    flipped = symbol in seen and seen.get(symbol) != live
    pending_fill = pos is None and allowed and bool(live) and price is None and (flipped or closed_opposite)
    if pos is None and allowed and live and price is not None and (flipped or closed_opposite):
        _open_auto(
            broker,
            row,
            cfg,
            suggestion,
            side=live,
            price=price,
            entry_bar=entry_bar,
            when=when,
        )
        events.append(f"{symbol} open {live}")
    # A flip we could not fill stays unseen so the next cadence can retry.
    if allowed and not pending_fill:
        seen[symbol] = live or ""
    return events


def run_auto_paper(
    cfg: dict[str, Any] | None = None,
    *,
    rows: list[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open/close paper trades from the current brief. No-op when auto is paused.

    First sight of a BUY/SELL is recorded and not filled. A later change into
    BUY or SELL (or an opposite close) is the flip that trades.
    """
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    with _PAPER_LOCK:
        broker = _paper(cfg)
        broker.reload()
        auto = broker.read_auto()
        if not auto["enabled"]:
            return {"events": [], "errors": [], "auto_enabled": False}
        seen = dict(auto["seen"])
        original = dict(seen)
        if rows is None:
            rows = _watch_rows(cfg)
        events: list[str] = []
        errors: list[dict[str, str]] = []
        for row in rows:
            pair = str(getattr(row, "pair", "") or "")
            try:
                events.extend(_auto_one(broker, row, cfg, now, seen))
            except Exception as exc:
                errors.append({"pair": pair.upper(), "error": str(exc)})
        if seen != original:
            broker.write_auto(seen=seen)
        return {"events": events, "errors": errors, "auto_enabled": True}


def set_auto_enabled(enabled: bool, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    with _PAPER_LOCK:
        broker = _paper(cfg)
        broker.reload()
        return broker.write_auto(enabled=bool(enabled))


def _row_view(
    row: dict[str, Any],
    cfg: dict[str, Any],
    *,
    now: datetime,
    mark: float | None,
) -> dict[str, Any]:
    desk = _desk()
    pair = str(row.get("pair") or "").upper()
    side = str(row.get("side") or "").upper()
    status = "closed" if str(row.get("status") or "") == "closed" else "open"
    entry = _f(row.get("entry_price"))
    exit_px = _f(row.get("exit_price")) if status == "closed" else None
    entry_at = _dhaka(row.get("entry_time"), cfg)
    exit_at = _dhaka(row.get("exit_time"), cfg) if status == "closed" else None
    start = parse_ts(row.get("entry_time"))
    end = parse_ts(row.get("exit_time")) if status == "closed" else now
    duration = "—"
    if start is not None and end is not None:
        text = human_duration(start, end)
        if text:
            duration = text
    basis: str | None = None
    pnl_price: float | None = None
    if status == "closed" and exit_px is not None:
        pnl_price = _price_move(side, entry, exit_px)
        basis = "realized" if pnl_price is not None else None
    elif status == "open" and mark is not None:
        pnl_price = _price_move(side, entry, mark)
        basis = "mark" if pnl_price is not None else None
    sl = _f(row.get("sl"))
    r_mark = exit_px if status == "closed" else mark
    r_mult = _r_multiple(side, entry, r_mark, sl) if pnl_price is not None else None
    pnl_text = _signed_price_text(pair, pnl_price)
    if r_mult is not None and pnl_text != "—":
        pnl_text = f"{pnl_text} · {r_mult:+.2f}R"
    outcome = normalize_outcome(row) if status == "closed" else ""
    if outcome not in {"RIGHT", "WRONG", "FLAT"}:
        outcome = ""
    confidence = _f(row.get("confidence"))
    return {
        "id": row.get("id"),
        "pair": pair,
        "status": status,
        "trigger": side if side in {"BUY", "SELL"} else "",
        "confidence": confidence,
        "confidence_text": conf_label(confidence),
        "entry_price": entry,
        "entry_price_text": desk.price_text(pair, entry),
        "entry_time_dhaka": entry_at,
        "exit_price": exit_px,
        "exit_price_text": desk.price_text(pair, exit_px) if exit_px is not None else "—",
        "exit_time_dhaka": exit_at,
        "duration": duration,
        "pnl_price": None if pnl_price is None else round(pnl_price, 8),
        "pnl_text": pnl_text,
        "pnl_r": None if r_mult is None else round(r_mult, 4),
        "pnl_basis": basis,
        "outcome": outcome or None,
        "exit_reason": (str(row.get("exit_reason")) if status == "closed" and row.get("exit_reason") else None),
        "source": str(row.get("source") or "") or None,
        "size": _f(row.get("size")),
        "sl": sl,
        "tp": _f(row.get("tp")),
    }


def _mark_for(pair: str, cfg: dict[str, Any], interval: str) -> float | None:
    frame = load_cached_ohlcv(pair, cfg, interval or str(cfg.get("interval") or "1h"))
    if frame is None or frame.empty or "Close" not in frame.columns:
        return None
    return _f(frame["Close"].iloc[-1])


def portfolio_payload(
    cfg: dict[str, Any] | None = None,
    *,
    sync: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open positions and recent closed paper trades. Optional auto step first."""
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    auto_run: dict[str, Any] = {"events": [], "errors": [], "auto_enabled": True}
    if sync:
        auto_run = run_auto_paper(cfg, now=now)
    clock = _as_utc(now)
    with _PAPER_LOCK:
        broker = _paper(cfg)
        broker.reload()
        auto = broker.read_auto()
        open_rows = []
        for pos in broker.list_positions():
            interval = str(pos.get("timeframe") or cfg.get("interval") or "1h")
            mark = _mark_for(str(pos.get("pair") or ""), cfg, interval)
            open_rows.append(_row_view(pos, cfg, now=clock, mark=mark))
        closed_src = list(broker.list_closed())

        def _exit_key(row: dict[str, Any]) -> float:
            ts = parse_ts(row.get("exit_time") or row.get("entry_time"))
            return ts.timestamp() if ts is not None else 0.0

        closed_src.sort(key=_exit_key)
        closed_rows = [
            _row_view(row, cfg, now=clock, mark=None) for row in reversed(closed_src[-_CLOSED_LIMIT:])
        ]
    try:
        refresh = int(desk.load_wl(cfg).refresh_seconds)
    except Exception:
        refresh = int((cfg.get("board") or {}).get("realtime_seconds") or 60)
    return {
        "timezone": timezone_name(cfg),
        "auto_enabled": bool(auto["enabled"]),
        "refresh_seconds": max(60, refresh),
        "generated_at_dhaka": fmt_display(clock, cfg, seconds=True),
        "open": open_rows,
        "closed": closed_rows,
        "auto_events": list(auto_run.get("events") or []),
        "auto_errors": list(auto_run.get("errors") or []),
    }
