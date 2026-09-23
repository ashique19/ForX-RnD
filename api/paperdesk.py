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
from api.strategies import (
    BRIEF,
    STRATEGIES,
    STRATEGY_IDS,
    book_id,
    normalize_champion,
    strategy_name,
    strategy_views,
)
from forex_lab.ui.board import (
    conf_label,
    paper_submit_allowed,
    paper_submit_block_reason,
    paper_submit_risk_defaults,
)
from forex_lab.ui.watchlist import active_pair

_PAPER_LOCK = threading.RLock()
_SECONDS_IN_STAMP = re.compile(r"\d{1,2}:\d{2}:\d{2}")
_CLOSED_LIMIT = 40
_COMPARE_WINDOW = timedelta(days=7)

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


def _champion_id(broker: PaperBroker) -> str:
    return normalize_champion(broker.read_auto().get("champion"))


def current_champion(cfg: dict[str, Any] | None = None) -> str:
    """Decision champion stored on the paper journal. Unknown values stay Brief."""
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    broker = _broker(cfg)
    if not isinstance(broker, PaperBroker):
        return BRIEF
    return _champion_id(broker)


def paper_snapshot(pair: str, cfg: dict[str, Any], row: Any) -> dict[str, Any]:
    symbol = str(pair).upper()
    broker = _broker(cfg)
    book = _champion_id(broker) if isinstance(broker, PaperBroker) else BRIEF
    pos = position_for_pair(broker, symbol, book)
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
    book = _champion_id(broker) if isinstance(broker, PaperBroker) else BRIEF
    if action == "CLOSE":
        pos = position_for_pair(broker, symbol, book)
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
    suggestion = desk.suggestion_from_row(row, cfg, ohlcv=frame)
    sl, tp, _horizon = _brackets(row, cfg, suggestion, side=action, frame=frame)
    if sl is None or tp is None:
        raise PaperBlocked("Missing stop/target")
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
        strategy_id=book,
        strategy_name=strategy_name(book),
    )
    if isinstance(broker, PaperBroker):
        _remember_signal(broker, symbol, action, book)
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


def _remember_signal(broker: PaperBroker, pair: str, signal: str, strategy_id: str) -> None:
    auto = broker.read_auto()
    seen = {key: dict(value) for key, value in auto["seen"].items()}
    seen.setdefault(str(pair).upper(), {})[strategy_id] = str(signal or "")
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


def _auto_opens_in_window(broker: PaperBroker, now: datetime, strategy_id: str) -> int:
    """Auto opens for one strategy whose entry time falls in the last 60 minutes.

    The UI number is the budget for each book, not a pile shared across books.
    Manual fills are not counted. A row with no parseable entry time does not count.
    """
    cutoff = _as_utc(now) - timedelta(minutes=60)
    count = 0
    rows = list(broker.list_positions()) + list(broker.list_closed())
    for row in rows:
        if str(row.get("source") or "") != "auto":
            continue
        if book_id(row) != strategy_id:
            continue
        opened = parse_ts(row.get("entry_time"))
        if opened is None or opened < cutoff:
            continue
        count += 1
    return count


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


def _as_pct(raw: object) -> int | None:
    """Same percent the desk prints. 0–1 fractions become 0–100. Missing stays missing."""
    value = _f(raw)
    if value is None:
        return None
    if 0.0 <= value <= 1.0:
        value = 100.0 * value
    return int(round(value))


def _confidence_pct(suggestion: dict[str, Any], row: Any) -> int | None:
    raw = _f(suggestion.get("confidence"))
    if raw is None:
        raw = _f(getattr(row, "confidence", None))
    return _as_pct(raw)


def _eligible_side(live: str | None, pct: int | None, min_confidence: int) -> str:
    if live in {"BUY", "SELL"} and pct is not None and pct >= min_confidence:
        return live
    return ""


def _directional_side(row: Any, live: str | None) -> str | None:
    """BUY/SELL from the brief, including a side the stale flash has hidden."""
    if live in {"BUY", "SELL"}:
        return live
    for attr in ("raw_signal", "buy_sell"):
        side = str(getattr(row, attr, "") or "").upper()
        if side in {"BUY", "SELL"}:
            return side
    return None


def _block_status(
    *,
    enabled: bool,
    rate_status: str | None,
    blocks: list[str],
    min_confidence: int,
) -> str | None:
    """One short line for the binding auto-open block. Empty when nothing is blocking."""
    parts: list[str] = []
    if not enabled:
        parts.append("Paused")
    if rate_status:
        parts.append(rate_status)
    elif "gated" in blocks:
        parts.append("Gated")
    elif "below" in blocks:
        parts.append(f"Below threshold ({min_confidence}%)")
    return " · ".join(parts) or None


def _reason_lines(
    block_status: str | None,
    notices: list[str] | None,
    errors: list[dict[str, str]] | None,
) -> list[str]:
    """Short lines for the desk. One reason per failure, with no blanks."""
    lines: list[str] = []

    def add(text: str) -> None:
        cleaned = " ".join(str(text or "").split())
        if not cleaned or cleaned in lines:
            return
        lines.append(cleaned[:180])

    if block_status:
        add(block_status)
    for note in notices or []:
        add(note)
    for err in errors or []:
        pair = str(err.get("pair") or "").strip().upper()
        detail = str(err.get("error") or "request failed").strip() or "request failed"
        prefix = f"{pair}: " if pair else ""
        add(f"{prefix}API error: {detail}")
    return lines


def _watch_rows(cfg: dict[str, Any]) -> list[Any]:
    """One board row for the active pair. Inactive pairs are not built here."""
    desk = _desk()
    wl = desk.load_wl(cfg)
    symbol = active_pair(wl)
    if not symbol:
        return []
    default = wl.lab_interval(cfg)
    item = next((pair for pair in wl.pairs if pair.pair == symbol), None)
    if item is None:
        return []
    return [
        desk.build_board_row(
            symbol,
            cfg,
            interval=item.resolved_interval(default),
            refresh_data=False,
            regenerate=False,
        )
    ]


def _book_event(symbol: str, text: str, strategy_id: str) -> str:
    """Brief events stay unsuffixed so existing logs keep their wording."""
    if strategy_id == BRIEF:
        return f"{symbol} {text}"
    return f"{symbol} {text} {strategy_id}"


def _brackets(
    row: Any,
    cfg: dict[str, Any],
    suggestion: dict[str, Any],
    *,
    side: str,
    frame: Any = None,
) -> tuple[float | None, float | None, int]:
    """Stop, target, and horizon for one paper open. Missing levels stay missing."""
    if frame is None:
        frame = load_cached_ohlcv(str(row.pair), cfg, str(getattr(row, "timeframe", "") or ""))
    sl, tp = paper_submit_risk_defaults(frame, cfg, side, str(getattr(row, "validity", "") or ""))
    if sl is None and suggestion.get("signal") == side:
        sl = _f(suggestion.get("stop"))
    if tp is None and suggestion.get("signal") == side:
        tp = _f(suggestion.get("target"))
    raw_h = suggestion.get("horizon_bars")
    try:
        horizon = int(raw_h) if raw_h else 0
    except (TypeError, ValueError):
        horizon = 0
    return sl, tp, horizon


def _note_block(
    blocked: str | None,
    *,
    symbol: str,
    row: Any,
    cfg: dict[str, Any],
    blocks: list[str],
    notices: list[str],
) -> None:
    if not blocked:
        return
    blocks.append(blocked)
    if blocked != "gated":
        return
    detail = paper_submit_block_reason(getattr(row, "validity", None), row, cfg) or "Paper open is blocked"
    notices.append(f"{symbol}: {detail}")


def _skip_notice(symbol: str, strategy_id: str, text: str) -> str:
    if strategy_id == BRIEF:
        return f"{symbol}: {text}"
    return f"{symbol} · {strategy_name(strategy_id)}: {text}"


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
    strategy_id: str,
    confidence: float | None,
    sl: float,
    tp: float,
    horizon: int,
) -> None:
    name = strategy_name(strategy_id)
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
        strategy_id=strategy_id,
        strategy_name=name,
        note=f"auto paper from {name}",
    )


def _champion_blocked(
    broker: PaperBroker,
    row: Any,
    symbol: str,
    views: list[dict[str, Any]],
    *,
    champion: str,
    live: str | None,
    allowed: bool,
    min_confidence: int,
) -> str | None:
    """Pair gate, or the champion book sitting under the confidence minimum."""
    directional = _directional_side(row, live)
    if directional and not allowed:
        return "gated"
    champ = next((view for view in views if view["id"] == champion), None)
    if champ is None or not allowed:
        return None
    if champion == BRIEF:
        champ_dir = directional
    else:
        champ_dir = champ["signal"] if champ["signal"] in {"BUY", "SELL"} else None
    eligible = _eligible_side(champ["signal"], _as_pct(champ["confidence"]), min_confidence)
    if champ_dir and not eligible and position_for_pair(broker, symbol, champion) is None:
        return "below"
    return None


def _auto_one(
    broker: PaperBroker,
    row: Any,
    cfg: dict[str, Any],
    now: datetime | None,
    seen: dict[str, dict[str, str]],
    *,
    champion: str,
    min_confidence: int,
    blocks: list[str],
    notices: list[str],
    trade: bool = True,
) -> list[str]:
    symbol = str(getattr(row, "pair", "") or "").upper()
    if not symbol:
        return []
    interval = str(getattr(row, "timeframe", "") or cfg.get("interval") or "1h")
    frame = load_cached_ohlcv(symbol, cfg, interval)
    desk = _desk()
    suggestion = desk.suggestion_from_row(row, cfg, ohlcv=frame)
    views = strategy_views(row, cfg, suggestion)
    price, entry_bar = _price_from_cache(symbol, cfg, interval, row)
    live = _live_signal(suggestion)
    allowed = paper_submit_allowed(getattr(row, "validity", None), row, cfg)
    if not trade:
        blocked = _champion_blocked(
            broker,
            row,
            symbol,
            views,
            champion=champion,
            live=live,
            allowed=allowed,
            min_confidence=min_confidence,
        )
        _note_block(blocked, symbol=symbol, row=row, cfg=cfg, blocks=blocks, notices=notices)
        return []
    when = _stamp(now)
    clock = _as_utc(now)
    events: list[str] = []
    pair_seen = seen.setdefault(symbol, {})
    for view in views:
        sid = str(view["id"])
        pos = position_for_pair(broker, symbol, sid)
        side_now = view["signal"] if view["signal"] in {"BUY", "SELL"} else None
        if pos is not None and price is not None:
            reason = _barrier_reason(pos, price)
            if reason is None and _duration_expired(pos, clock):
                reason = "duration"
            if reason is None and allowed and side_now and side_now != str(pos.get("side") or "").upper():
                reason = "opposite"
            if reason:
                broker.close(str(pos["id"]), price=price, reason=reason, timestamp=when)
                events.append(_book_event(symbol, f"close {reason}", sid))
                pos = None
        # A cross is a new eligible side versus that book's last allowed reading.
        # The first reading is stored and not filled. The same side staying
        # eligible — including a small confidence tick — does not open again.
        eligible = _eligible_side(side_now, _as_pct(view["confidence"]), min_confidence) if allowed else ""
        known = sid in pair_seen
        crossed = known and bool(eligible) and pair_seen.get(sid) != eligible
        want_open = pos is None and allowed and bool(eligible) and crossed
        # A skipped open leaves this book's memory unchanged so the next tick can retry.
        hold_seen = False
        if want_open and price is None:
            hold_seen = True
            events.append(_book_event(symbol, "skip price", sid))
            notices.append(_skip_notice(symbol, sid, "No cached price"))
        elif want_open:
            cap = int(broker.read_auto()["max_opens_per_hour"])
            if _auto_opens_in_window(broker, clock, sid) >= cap:
                hold_seen = True
                events.append(_book_event(symbol, "skip rate", sid))
                notices.append(_skip_notice(symbol, sid, "Hourly cap"))
            else:
                sl, tp, horizon = _brackets(row, cfg, suggestion, side=eligible)
                if sl is None or tp is None:
                    hold_seen = True
                    events.append(_book_event(symbol, "skip levels", sid))
                    notices.append(_skip_notice(symbol, sid, "Missing stop/target"))
                else:
                    _open_auto(
                        broker,
                        row,
                        cfg,
                        suggestion,
                        side=eligible,
                        price=price,
                        entry_bar=entry_bar,
                        when=when,
                        strategy_id=sid,
                        confidence=_f(view["confidence"]),
                        sl=sl,
                        tp=tp,
                        horizon=horizon,
                    )
                    events.append(_book_event(symbol, f"open {eligible}", sid))
        if allowed and not hold_seen:
            pair_seen[sid] = eligible
    blocked = _champion_blocked(
        broker,
        row,
        symbol,
        views,
        champion=champion,
        live=live,
        allowed=allowed,
        min_confidence=min_confidence,
    )
    _note_block(blocked, symbol=symbol, row=row, cfg=cfg, blocks=blocks, notices=notices)
    return events


def run_auto_paper(
    cfg: dict[str, Any] | None = None,
    *,
    rows: list[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Open/close paper trades for each strategy book. No-op when auto is paused.

    The first allowed reading of a pair, for that strategy, is recorded and
    not filled. An auto open then requires a cross on that book: its
    confidence reaches ``min_confidence`` on a clear BUY or SELL, or the
    eligible side changes. A later tick of the same eligible side does not
    open again. After a close, the same eligible side stays quiet until it
    becomes ineligible and crosses once more.

    Each strategy has its own rolling 60-minute budget (the same
    ``max_opens_per_hour`` number) and they share one confidence minimum.
    Stops, targets, duration, and opposite closes still run at the cap and
    below the confidence minimum. The follow-up open waits until the new
    side is eligible and under that book's cap. Manual orders land on the
    champion book and sit outside this budget. Promoting a champion does not
    clear this memory.

    When ``rows`` is omitted, only the active watchlist pair is built and
    managed. Rows passed in are not filtered. Open books on other pairs stay
    frozen until that pair is active again.
    """
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    with _PAPER_LOCK:
        broker = _paper(cfg)
        broker.reload()
        auto = broker.read_auto()
        min_confidence = int(auto["min_confidence"])
        champion = normalize_champion(auto.get("champion"))
        if rows is None:
            rows = _watch_rows(cfg)
        notices: list[str] = []
        if not auto["enabled"]:
            blocks: list[str] = []
            errors: list[dict[str, str]] = []
            for row in rows:
                pair = str(getattr(row, "pair", "") or "")
                try:
                    _auto_one(
                        broker,
                        row,
                        cfg,
                        now,
                        {},
                        champion=champion,
                        min_confidence=min_confidence,
                        blocks=blocks,
                        notices=notices,
                        trade=False,
                    )
                except Exception as exc:
                    errors.append({"pair": pair.upper(), "error": str(exc)})
            return {
                "events": [],
                "errors": errors,
                "auto_enabled": False,
                "blocks": blocks,
                "notices": notices,
            }
        seen = {key: dict(value) for key, value in auto["seen"].items()}
        original = {key: dict(value) for key, value in seen.items()}
        events: list[str] = []
        errors = []
        blocks = []
        for row in rows:
            pair = str(getattr(row, "pair", "") or "")
            try:
                events.extend(
                    _auto_one(
                        broker,
                        row,
                        cfg,
                        now,
                        seen,
                        champion=champion,
                        min_confidence=min_confidence,
                        blocks=blocks,
                        notices=notices,
                    )
                )
            except Exception as exc:
                errors.append({"pair": pair.upper(), "error": str(exc)})
        if seen != original:
            broker.write_auto(seen=seen)
        return {
            "events": events,
            "errors": errors,
            "auto_enabled": True,
            "blocks": blocks,
            "notices": notices,
        }


def set_auto_settings(
    *,
    enabled: bool | None = None,
    max_opens_per_hour: int | None = None,
    min_confidence: int | None = None,
    champion: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist pause, the per-book hourly cap, the confidence minimum, and the champion."""
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    with _PAPER_LOCK:
        broker = _paper(cfg)
        broker.reload()
        stored: str | None = None
        if champion is not None:
            stored = str(champion).strip().lower()
            if stored not in STRATEGY_IDS:
                raise ValueError("champion must be brief, consensus, or mtf")
        return broker.write_auto(
            enabled=enabled,
            max_opens_per_hour=max_opens_per_hour,
            min_confidence=min_confidence,
            champion=stored,
        )


def set_auto_enabled(enabled: bool, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    return set_auto_settings(enabled=bool(enabled), cfg=cfg)


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
    sid = book_id(row)
    return {
        "id": row.get("id"),
        "pair": pair,
        "strategy_id": sid,
        "strategy_name": str(row.get("strategy_name") or strategy_name(sid)),
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


def _window_r(row: dict[str, Any]) -> float | None:
    side = str(row.get("side") or "").upper()
    mark = _f(row.get("exit_price"))
    return _r_multiple(side, _f(row.get("entry_price")), mark, _f(row.get("sl")))


def _strategy_compare(broker: PaperBroker, now: datetime, auto: dict[str, Any]) -> list[dict[str, Any]]:
    """Rolling closed stats per book. Open count is the book right now, not the window."""
    champion = normalize_champion(auto.get("champion"))
    cap = int(auto["max_opens_per_hour"])
    cutoff = _as_utc(now) - _COMPARE_WINDOW
    closed = list(broker.list_closed())
    open_rows = list(broker.list_positions())
    cards: list[dict[str, Any]] = []
    for item in STRATEGIES:
        sid = item["id"]
        window: list[dict[str, Any]] = []
        for row in closed:
            if book_id(row) != sid:
                continue
            stamp = parse_ts(row.get("exit_time") or row.get("entry_time"))
            if stamp is None or _as_utc(stamp) < cutoff:
                continue
            window.append(row)
        wins = sum(1 for row in window if normalize_outcome(row) == "RIGHT")
        losses = sum(1 for row in window if normalize_outcome(row) == "WRONG")
        scored = wins + losses
        if scored:
            win_text = f"{int(round(100.0 * wins / scored))}%"
        else:
            win_text = "—"
        rs = [value for value in (_window_r(row) for row in window) if value is not None]
        if rs:
            avg = sum(rs) / len(rs)
            exp_value: float | None = round(avg, 4)
            exp_text = f"{avg:+.2f}R"
        else:
            exp_value = None
            exp_text = "—"
        hour = _auto_opens_in_window(broker, now, sid)
        limited = hour >= cap
        cards.append(
            {
                "id": sid,
                "name": item["name"],
                "champion": sid == champion,
                "open_count": sum(1 for row in open_rows if book_id(row) == sid),
                "trade_count": len(window),
                "win_rate_text": win_text,
                "expectancy_r": exp_value,
                "expectancy_text": exp_text,
                "opens_this_hour": hour,
                "rate_limited": limited,
                "rate_status": (
                    f"Rate-limited: {hour}/{cap} opens this hour" if limited else None
                ),
            }
        )
    return cards


def portfolio_payload(
    cfg: dict[str, Any] | None = None,
    *,
    sync: bool = True,
    now: datetime | None = None,
    rows: list[Any] | None = None,
) -> dict[str, Any]:
    """Open positions and recent closed paper trades. Optional auto step first."""
    desk = _desk()
    cfg = cfg if cfg is not None else desk.app_config()
    auto_run: dict[str, Any] = {"events": [], "errors": [], "auto_enabled": True}
    if sync:
        auto_run = run_auto_paper(cfg, rows=rows, now=now)
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
        champion = normalize_champion(auto.get("champion"))
        strategies = _strategy_compare(broker, clock, auto)
        opens_this_hour = _auto_opens_in_window(broker, clock, champion)
        cap = int(auto["max_opens_per_hour"])
        min_confidence = int(auto["min_confidence"])
    active = ""
    try:
        wl = desk.load_wl(cfg)
        refresh = int(wl.refresh_seconds)
        active = active_pair(wl)
    except Exception:
        refresh = int((cfg.get("board") or {}).get("realtime_seconds") or 60)
    rate_limited = opens_this_hour >= cap
    rate_status = f"Rate-limited: {opens_this_hour}/{cap} opens this hour" if rate_limited else None
    block_status = _block_status(
        enabled=bool(auto["enabled"]),
        rate_status=rate_status,
        blocks=list(auto_run.get("blocks") or []),
        min_confidence=min_confidence,
    )
    return {
        "timezone": timezone_name(cfg),
        "auto_enabled": bool(auto["enabled"]),
        "max_opens_per_hour": cap,
        "min_confidence": min_confidence,
        "champion": champion,
        "compare_window": "7d",
        "strategies": strategies,
        "opens_this_hour": opens_this_hour,
        "rate_limited": rate_limited,
        "rate_status": rate_status,
        "block_status": block_status,
        "reasons": _reason_lines(
            block_status,
            list(auto_run.get("notices") or []),
            list(auto_run.get("errors") or []),
        ),
        "active_pair": active,
        "refresh_seconds": max(60, refresh),
        "generated_at_dhaka": fmt_display(clock, cfg, seconds=True),
        "open": open_rows,
        "closed": closed_rows,
        "auto_events": list(auto_run.get("events") or []),
        "auto_errors": list(auto_run.get("errors") or []),
    }
