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
* Broker truth: before paper applies its own exit to a mirrored position,
  ``reconcile_broker`` reads MT5 deal history (read-only). If the broker
  already closed it (SL/TP/manual), the paper close is recorded at the
  broker's real price/time/reason. Paper exits only positions still open at
  the broker, and ``mirror`` then closes them at the broker too.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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


_NY = ZoneInfo("America/New_York")
DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = (1, 3)  # OUT, OUT_BY
# MT5 DEAL_REASON_*: CLIENT/MOBILE/WEB = manual, EXPERT = our own script close.
DEAL_REASONS = {0: "manual", 1: "manual", 2: "manual", 3: "expert", 4: "sl", 5: "tp", 6: "stop_out"}


def server_to_utc(server_epoch: float) -> datetime:
    """MT5 deal/tick epochs are the broker wall clock (New York time + 7h:
    UTC+3 in summer, UTC+2 in winter). Convert to real UTC."""
    wall = datetime(1970, 1, 1) + timedelta(seconds=float(server_epoch))
    return (wall - timedelta(hours=7)).replace(tzinfo=_NY).astimezone(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def broker_state(mt5, ticket: int) -> dict[str, Any]:
    """Read-only: {'state': 'closed', exit fields} | {'state': 'open'} | {'state': 'unknown'}."""
    t = int(ticket)
    deals = mt5.history_deals_get(position=t)
    if deals is None:
        if mt5.positions_get(ticket=t):
            return {"state": "open"}
        return {"state": "unknown", "error": f"history_deals_get None ({mt5.last_error()})"}
    deals = list(deals)
    ins = [d for d in deals if int(getattr(d, "entry", -1)) == DEAL_ENTRY_IN]
    outs = [d for d in deals if int(getattr(d, "entry", -1)) in DEAL_ENTRY_OUT]
    v_in = sum(float(d.volume) for d in ins)
    v_out = sum(float(d.volume) for d in outs)
    if outs and v_out > 0 and (not ins or v_out + 1e-9 >= v_in):
        last = max(outs, key=lambda d: (int(getattr(d, "time_msc", 0) or 0), int(getattr(d, "time", 0) or 0)))
        tms = int(getattr(last, "time_msc", 0) or 0)
        when = server_to_utc(tms / 1000.0 if tms else float(last.time))
        money = sum(float(getattr(d, k, 0.0) or 0.0) for d in deals for k in ("profit", "commission", "swap", "fee"))
        code = int(getattr(last, "reason", -1))
        return {
            "state": "closed",
            "exit_price": sum(float(d.price) * float(d.volume) for d in outs) / v_out,
            "exit_time_utc": _iso(when),
            "exit_reason": DEAL_REASONS.get(code, f"reason_{code}"),
            "deal_reason_code": code,
            "exit_deal_ticket": int(getattr(last, "ticket", 0) or 0),
            "entry_price_broker": (sum(float(d.price) * float(d.volume) for d in ins) / v_in) if v_in else None,
            "profit_usd": round(money, 2),
        }
    if mt5.positions_get(ticket=t):
        return {"state": "open"}
    return {"state": "unknown", "error": "no exit deal and no live position"}


def _broker_sl(mopen: dict[str, Any], pos: dict[str, Any] | None) -> float | None:
    """SL the broker actually carried: re-anchored SL when the modify succeeded, else the one sent."""
    keys = ("sl_sent", "sl") if mopen.get("sltp_modified") is False else ("sl", "sl_sent")
    for k in keys:
        v = eb._f(mopen.get(k))
        if v is not None:
            return v
    return eb._f((pos or {}).get("sl"))


def broker_pnl(side: str, pair: str, entry: float, sl: float | None, exit_price: float) -> dict[str, Any]:
    direction = 1.0 if str(side).upper() == "BUY" else -1.0
    pnl = (exit_price - entry) * direction
    risk = abs(entry - sl) if sl is not None else 0.0
    return {"pnl_price": round(pnl, 8), "pnl_r": round(pnl / risk, 4) if risk else None,
            "pnl_pips": round(pnl / eb.pip_size(pair), 2)}


def broker_close_row(pid: str, pos: dict[str, Any], mopen: dict[str, Any], b: dict[str, Any], now=None) -> dict[str, Any]:
    """Paper close at the broker's real exit (price/time/reason), R off the real fill and broker SL."""
    entry = eb._f(b.get("entry_price_broker")) or eb._f(mopen.get("fill_price")) or eb._f(pos.get("entry_mid"))
    sl = _broker_sl(mopen, pos)
    pnl = broker_pnl(str(pos.get("side")), str(pos.get("pair")), float(entry), sl, float(b["exit_price"]))
    when = datetime.fromisoformat(b["exit_time_utc"].replace("Z", "+00:00"))
    return {
        "event": "close", **eb._stamps(now), "pair": pos.get("pair"), "side": pos.get("side"), "position_id": pid,
        "entry_mid": entry, "exit_mid": float(b["exit_price"]), "exit_reason": b["exit_reason"], **pnl,
        "exit_basis": "mt5_broker", "price_basis": "broker_fill_bid_ask", "risk_sl": sl,
        "exit_time_utc": b["exit_time_utc"], "exit_time_dhaka": when.astimezone(eb.DHAKA).strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka"),
        "exit_bar_utc": None, "mt5_ticket": int(mopen["ticket"]), "mt5_exit_deal": b.get("exit_deal_ticket"),
        "mt5_profit_usd": b.get("profit_usd"),
    }


def broker_annotation(close_row: dict[str, Any], mopen: dict[str, Any], b: dict[str, Any], now=None,
                      reason: str = "broker_closed_first", pos: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append-only supersede of a paper close by the broker's real close (never rewrites the close row)."""
    entry = eb._f(b.get("entry_price_broker")) or eb._f(mopen.get("fill_price")) or eb._f(close_row.get("entry_mid"))
    sl = _broker_sl(mopen, pos)
    pnl = broker_pnl(str(close_row.get("side") or mopen.get("side")), str(close_row.get("pair")), float(entry), sl,
                     float(b["exit_price"]))
    return {
        "event": "annotation", **eb._stamps(now), "annotation_type": "broker_reconcile", "reason": reason,
        "ref_position_id": close_row.get("position_id"), "ref_close_ts_utc": close_row.get("ts_utc"),
        "original_pnl_r": close_row.get("pnl_r"), "original_exit_reason": close_row.get("exit_reason"),
        "original_exit_mid": close_row.get("exit_mid"), "original_entry_mid": close_row.get("entry_mid"),
        "corrected_exit_reason": b["exit_reason"], "corrected_exit_mid": float(b["exit_price"]),
        "corrected_exit_time_utc": b["exit_time_utc"], "corrected_entry": entry, "corrected_sl": sl,
        "corrected_pnl_r": pnl["pnl_r"], "corrected_pnl_pips": pnl["pnl_pips"],
        "mt5_ticket": int(mopen["ticket"]), "mt5_exit_deal": b.get("exit_deal_ticket"),
        "mt5_profit_usd": b.get("profit_usd"), "exclude_from_stage2": True,
        "notes": "MT5 demo position was already closed at the broker when paper tried to close it; broker fill is the truth",
    }


def _owned_mt5(mt5, connect):
    from forex_lab import mt5_feed

    (connect or mt5_feed.connect)(require_demo=True)
    return mt5_feed._mt5()


def reconcile_broker(*, mt5=None, connect=None, now=None) -> dict[str, Any]:
    """Before paper exits: close paper positions the broker already closed (read-only to MT5).

    Returns ``hold``: mirrored positions whose broker state is unknown; paper
    must not apply its own exit to those this pass.
    """
    if mt5 is None and os.environ.get("FORX_MT5_EXEC", "on").lower() == "off":
        return {"enabled": False, "reason": "FORX_MT5_EXEC=off", "hold": []}
    events = eb.load()
    openp = eb.open_positions(events)
    m_open = {k: v for k, v in _ok_rows(events, "mt5_open").items() if v.get("ok") and v.get("ticket")}
    targets = [pid for pid in openp if pid in m_open]
    out: dict[str, Any] = {"enabled": True, "checked": len(targets), "closed": [], "live": [], "hold": [], "errors": []}
    if not targets:
        return out
    owns = mt5 is None
    try:
        if owns:
            mt5 = _owned_mt5(mt5, connect)
    except Exception as exc:  # noqa: BLE001
        out.update({"hold": targets, "error": str(exc)})
        return out
    try:
        for pid in targets:
            mo = m_open[pid]
            try:
                b = broker_state(mt5, int(mo["ticket"]))
            except Exception as exc:  # noqa: BLE001
                b = {"state": "unknown", "error": str(exc)}
            if b["state"] == "open":
                out["live"].append(pid)
            elif b["state"] == "closed":
                row = broker_close_row(pid, openp[pid], mo, b, now)
                eb.append(row)
                eb.append({"event": "mt5_close", **eb._stamps(now), "pair": mo.get("pair"), "symbol": mo.get("symbol"),
                           "position_id": pid, "ticket": int(mo["ticket"]), "ok": True, "already_closed": True,
                           "broker_truth": True, "fill_price": b["exit_price"], "exit_reason": b["exit_reason"],
                           "exit_time_utc": b["exit_time_utc"], "deal_ticket": b.get("exit_deal_ticket"),
                           "profit_usd": b.get("profit_usd"), "pnl_pips": row["pnl_pips"]})
                out["closed"].append({"position_id": pid, "pair": row["pair"], "exit_reason": row["exit_reason"],
                                      "pnl_r": row["pnl_r"], "profit_usd": b.get("profit_usd")})
            else:
                out["hold"].append(pid)
                out["errors"].append({"position_id": pid, **b})
    finally:
        if owns:
            try:
                mt5.shutdown()
            except Exception:  # noqa: BLE001
                pass
    return out


def close_order(mt5, mopen: dict[str, Any]) -> dict[str, Any]:
    symbol = str(mopen["symbol"]); pair = str(mopen["pair"]).upper(); side = str(mopen["side"]).upper()
    ticket = int(mopen["ticket"])
    base = {"event": "mt5_close", "pair": pair, "symbol": symbol, "position_id": mopen["position_id"], "ticket": ticket}
    live = mt5.positions_get(ticket=ticket) or ()
    if not live:
        row = {**base, "ok": True, "already_closed": True, "note": "position not open at broker (SL/TP hit or closed)"}
        try:
            b = broker_state(mt5, ticket) if hasattr(mt5, "history_deals_get") else {"state": "unknown"}
        except Exception:  # noqa: BLE001
            b = {"state": "unknown"}
        if b.get("state") == "closed":
            row.update({"broker_truth": True, "fill_price": b["exit_price"], "exit_reason": b["exit_reason"],
                        "exit_time_utc": b["exit_time_utc"], "deal_ticket": b.get("exit_deal_ticket"),
                        "profit_usd": b.get("profit_usd"), "_broker": b})
        return row
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
            row = close_order(mt5, m_open[pid]); b = row.pop("_broker", None)
            eb.append({**row, **eb._stamps(now)})
            if b:
                # Raced: broker closed it before paper's close reached MT5 -> supersede (append-only).
                eb.append(broker_annotation(closes[pid], m_open[pid], b, now))
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


__all__ = ["LOT", "MAGIC", "assert_demo", "broker_annotation", "broker_state", "close_order", "mirror",
           "open_order", "reconcile_broker", "server_to_utc"]
