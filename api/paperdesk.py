"""Paper Buy/Sell/Close for the Decision desk. Journal only — never a live venue."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from forex_lab.broker import BrokerError, make_broker, position_for_pair
from forex_lab.clock import fmt_display
from forex_lab.data import load_cached_ohlcv
from forex_lab.ui.board import paper_submit_allowed, paper_submit_block_reason, paper_submit_risk_defaults

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
    )
    snap = paper_snapshot(symbol, cfg, row)
    return {
        "ok": True,
        "pair": symbol,
        "side": action,
        "message": f"Paper {action} recorded @ {price:.5f} — local journal only.",
        "paper": snap,
    }
