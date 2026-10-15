"""Mirror exploration paper fills as real MT5 DEMO market orders.

Hard rules:
* Refuses to send anything unless account_info().trade_mode == DEMO; the check
  runs in this module right before every order_send, independent of connect().
* Exploration book only (never Stage-2 / main journal). Fixed 0.01 lot, SL/TP
  taken from the explore open row. One attempt per position (no retry storms).
* Results are appended to the explore journal as ``mt5_open`` / ``mt5_close``
  rows keyed by position_id: ticket, fill price, slippage (pips, + = adverse)
  and spread at send. Credentials are never logged.
* When the paper explore position closes, the matching MT5 position is closed.
* FORX_MT5_EXEC=off disables it (tests set this unless an mt5 module is injected).
"""
from __future__ import annotations

import os
from typing import Any, Iterable

from forex_lab import explore_book as eb

LOT = 0.01
MAGIC = 731905
DEVIATION_POINTS = 20
COMMENT_PREFIX = "forx-exp "


def _pip(pair: str) -> float:
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def _ok_rows(events: list[dict[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    return {str(e.get("position_id")): e for e in events if e.get("event") == kind}


def assert_demo(mt5) -> dict[str, Any]:
    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"refusing: account_info unavailable ({mt5.last_error()})")
    if int(acc.trade_mode) != int(mt5.ACCOUNT_TRADE_MODE_DEMO):
        raise RuntimeError(f"refusing: account trade_mode={acc.trade_mode} is not DEMO")
    return {"trade_mode": int(acc.trade_mode), "server": getattr(acc, "server", None)}


def _filling(mt5, info) -> int:
    fm = int(getattr(info, "filling_mode", 0) or 0)
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def _find_position(mt5, symbol: str, comment: str, order_ticket: int | None):
    pos = mt5.positions_get(symbol=symbol) or ()
    for p in pos:
        if int(getattr(p, "magic", 0)) == MAGIC and (
            str(getattr(p, "comment", "")) == comment or (order_ticket and int(p.ticket) == int(order_ticket))
        ):
            return p
    return None


def open_order(mt5, symbol: str, open_row: dict[str, Any]) -> dict[str, Any]:
    pid = str(open_row["position_id"]); pair = str(open_row["pair"]).upper(); side = str(open_row["side"]).upper()
    base = {"event": "mt5_open", "pair": pair, "symbol": symbol, "side": side, "position_id": pid, "volume": LOT,
            "sl": open_row.get("sl"), "tp": open_row.get("tp"), "magic": MAGIC}
    info = mt5.symbol_info(symbol); tick = mt5.symbol_info_tick(symbol)
    if info is None or tick is None or not tick.bid or not tick.ask:
        return {**base, "ok": False, "error": f"no symbol/tick ({mt5.last_error()})"}
    digits = int(getattr(info, "digits", 5))
    req_price = float(tick.ask if side == "BUY" else tick.bid)
    # Same SL/TP distances as the paper row, anchored on the price we trade at.
    entry_paper = float(open_row.get("entry_mid") or req_price)
    sl_d = float(open_row["sl"]) - entry_paper
    tp_d = float(open_row["tp"]) - entry_paper
    spread = float(tick.ask) - float(tick.bid)
    comment = (COMMENT_PREFIX + pid[-12:])[:31]
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": LOT,
        "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": req_price, "sl": round(req_price + sl_d, digits), "tp": round(req_price + tp_d, digits),
        "deviation": DEVIATION_POINTS, "magic": MAGIC, "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(mt5, info),
    }
    assert_demo(mt5)  # re-checked immediately before the send
    res = mt5.order_send(request)
    row = {**base, "requested_price": req_price, "spread_price": round(spread, 8),
           "spread_pips": round(spread / _pip(pair), 3), "comment": comment}
    if res is None:
        return {**row, "ok": False, "error": f"order_send returned None ({mt5.last_error()})"}
    if int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
        return {**row, "ok": False, "retcode": int(res.retcode), "error": str(getattr(res, "comment", ""))}
    fill = float(res.price or req_price)
    direction = 1.0 if side == "BUY" else -1.0
    pos = _find_position(mt5, symbol, comment, int(res.order))
    sl_new, tp_new = round(fill + sl_d, digits), round(fill + tp_d, digits)
    sltp_ok = None
    if pos is not None and (sl_new != request["sl"] or tp_new != request["tp"]):
        mod = {"action": getattr(mt5, "TRADE_ACTION_SLTP", 6), "symbol": symbol, "position": int(pos.ticket),
               "sl": sl_new, "tp": tp_new, "magic": MAGIC}
        assert_demo(mt5)
        mres = mt5.order_send(mod)
        sltp_ok = mres is not None and int(mres.retcode) == int(mt5.TRADE_RETCODE_DONE)
    return {**row, "ok": True, "sl": sl_new, "tp": tp_new, "sl_sent": request["sl"], "tp_sent": request["tp"],
            "sltp_modified": sltp_ok, "retcode": int(res.retcode), "order_ticket": int(res.order), "deal_ticket": int(res.deal),
            "ticket": int(pos.ticket) if pos is not None else int(res.order), "fill_price": fill,
            "slippage_pips": round((fill - req_price) * direction / _pip(pair), 3),
            "entry_mid_paper": open_row.get("entry_mid")}


def close_order(mt5, mopen: dict[str, Any]) -> dict[str, Any]:
    symbol = str(mopen["symbol"]); pair = str(mopen["pair"]).upper(); side = str(mopen["side"]).upper()
    ticket = int(mopen["ticket"])
    base = {"event": "mt5_close", "pair": pair, "symbol": symbol, "position_id": mopen["position_id"], "ticket": ticket}
    live = mt5.positions_get(ticket=ticket) or ()
    if not live:
        return {**base, "ok": True, "already_closed": True, "note": "position not open at broker (SL/TP hit or closed)"}
    p = live[0]; tick = mt5.symbol_info_tick(symbol); info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        return {**base, "ok": False, "error": f"no tick ({mt5.last_error()})"}
    req_price = float(tick.bid if side == "BUY" else tick.ask)
    request = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(p.volume), "position": ticket,
        "type": mt5.ORDER_TYPE_SELL if side == "BUY" else mt5.ORDER_TYPE_BUY, "price": req_price,
        "deviation": DEVIATION_POINTS, "magic": MAGIC, "comment": str(mopen.get("comment") or "")[:31],
        "type_time": mt5.ORDER_TIME_GTC, "type_filling": _filling(mt5, info),
    }
    assert_demo(mt5)
    res = mt5.order_send(request)
    spread = float(tick.ask) - float(tick.bid)
    row = {**base, "requested_price": req_price, "spread_pips": round(spread / _pip(pair), 3)}
    if res is None or int(res.retcode) != int(mt5.TRADE_RETCODE_DONE):
        return {**row, "ok": False, "retcode": None if res is None else int(res.retcode),
                "error": str(mt5.last_error() if res is None else getattr(res, "comment", ""))}
    fill = float(res.price or req_price)
    direction = 1.0 if side == "BUY" else -1.0
    return {**row, "ok": True, "deal_ticket": int(res.deal), "fill_price": fill,
            "slippage_pips": round((req_price - fill) * direction / _pip(pair), 3),
            "pnl_pips": round((fill - float(mopen.get("fill_price") or fill)) * direction / _pip(pair), 2)}


def mirror(new_position_ids: Iterable[str], *, mt5=None, connect=None, resolve=None, now=None) -> dict[str, Any]:
    """Open MT5 DEMO orders for new explore fills; close ones whose paper position closed."""
    new_ids = [str(x) for x in new_position_ids]
    if mt5 is None and os.environ.get("FORX_MT5_EXEC", "on").lower() == "off":
        return {"enabled": False, "reason": "FORX_MT5_EXEC=off"}
    events = eb.load()
    opens = _ok_rows(events, "open"); closes = _ok_rows(events, "close")
    m_open = {k: v for k, v in _ok_rows(events, "mt5_open").items() if v.get("ok")}
    m_close = _ok_rows(events, "mt5_close"); m_close = {k: v for k, v in m_close.items() if v.get("ok")}
    attempted = set(_ok_rows(events, "mt5_open"))
    to_open = [pid for pid in new_ids if pid in opens and pid not in closes and pid not in attempted]
    to_close = [pid for pid, v in m_open.items() if pid in closes and pid not in m_close]
    out: dict[str, Any] = {"enabled": True, "opened": [], "closed": [], "errors": []}
    if not to_open and not to_close:
        return out
    owns = mt5 is None
    try:
        if owns:
            from forex_lab import mt5_feed

            (connect or mt5_feed.connect)(require_demo=True)
            mt5 = mt5_feed._mt5()
            resolve = resolve or mt5_feed.resolve_symbols
        out["account"] = assert_demo(mt5)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        for pid in to_open:
            eb.append({"event": "mt5_open", **eb._stamps(now), "position_id": pid, "pair": opens[pid].get("pair"),
                       "ok": False, "refused": "refusing" in msg, "error": msg})
        out.update({"refused": True, "error": msg})
        return out
    try:
        pairs = sorted({str(opens[p]["pair"]).upper() for p in to_open} | {str(m_open[p]["pair"]).upper() for p in to_close})
        symbols = resolve(pairs) if resolve else {p: p for p in pairs}
        for pid in to_close:
            row = close_order(mt5, m_open[pid]); eb.append({**row, **eb._stamps(now)})
            (out["closed"] if row.get("ok") else out["errors"]).append(row)
        for pid in to_open:
            o = opens[pid]; sym = symbols.get(str(o["pair"]).upper())
            if not sym:
                row = {"event": "mt5_open", "position_id": pid, "pair": o.get("pair"), "ok": False, "error": "symbol not found"}
            else:
                try:
                    row = open_order(mt5, sym, o)
                except Exception as exc:  # noqa: BLE001
                    row = {"event": "mt5_open", "position_id": pid, "pair": o.get("pair"), "ok": False, "error": str(exc)}
            eb.append({**row, **eb._stamps(now)})
            if row.get("ok") and row.get("fill_price"):
                # Paper follows the real fill so paper and demo carry the same R.
                eb.append({"event": "rebase", **eb._stamps(now), "position_id": pid, "pair": o.get("pair"),
                           "basis": "mt5_fill", "entry_mid": row["fill_price"], "sl": row["sl"], "tp": row["tp"],
                           "prev_entry_mid": o.get("entry_mid"), "prev_sl": o.get("sl"), "prev_tp": o.get("tp")})
            (out["opened"] if row.get("ok") else out["errors"]).append(row)
    finally:
        if owns:
            try:
                mt5.shutdown()
            except Exception:  # noqa: BLE001
                pass
    return out


__all__ = ["LOT", "MAGIC", "assert_demo", "close_order", "mirror", "open_order"]
