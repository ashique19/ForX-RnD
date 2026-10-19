"""READ-ONLY stop / payoff analysis of closed exploration trades (and the 4 main model fills).

Answers: is the loss problem wide stops, stops not kept, time exits, or payoff? Reports win rate,
avg win/loss (R, pips), payoff, exit reasons, stop/target distances (pips, ATR), R:R at entry,
stop honoring (losses beyond -1.1R, broker SL fill slippage), MT5 demo $ at target vs actual $,
MAE/MFE on finished 1h bars, and a SMALL-SAMPLE WHAT-IF of other stops (ATR multiples, fractions of
the target distance, breakeven at +1R) with costs. The what-if is NOT a gate result.

Never writes journals, YAML, MT5 settings or CSVs. MT5 use is read-only (history_deals_get,
order_calc_profit) and optional (--no-mt5).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab import explore_book as eb  # noqa: E402

ATR_PERIOD = 14
BE_TRIGGER_R = 1.0
ATR_VARIANTS = (1.0, 1.25, 1.5, 1.75)
TP_FRACTIONS = (0.5, 0.75, 1.0)
LOSS_LIMIT_R = -1.1


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(str(x).replace("Z", "+00:00"))
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo else t


def true_range_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    pc = df["Close"].shift(1)
    tr = pd.concat([(df["High"] - df["Low"]).abs(), (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def finished(bars: pd.DataFrame, now: pd.Timestamp) -> pd.DataFrame:
    """Finished 1h bars only (bar end <= now)."""
    return bars[(bars.index + pd.Timedelta(hours=1)) <= now]


def window(bars: pd.DataFrame, t0: pd.Timestamp, horizon: int) -> pd.DataFrame:
    """Bars the live path_exit would scan: first full bar after entry .. the bar whose end reaches expiry."""
    start = t0.floor("h") + pd.Timedelta(hours=1)
    expiry = t0 + pd.Timedelta(hours=horizon)
    w = bars[bars.index >= start]
    ends = w.index + pd.Timedelta(hours=1)
    k = int((ends < expiry).sum()) + 1
    return w.iloc[:k]


def simulate(side: str, entry: float, sl_dist: float, tp_dist: float, bars: pd.DataFrame, *,
             be_trigger: float | None = None) -> dict | None:
    """First touch on finished bars; SL wins a same-bar SL/TP tie (conservative, same as path_exit).

    be_trigger: once a bar's favorable extreme reaches entry + be_trigger * sl_dist, the stop moves to
    entry from the NEXT bar on. Returns None if the window is empty (not enough finished bars).
    """
    if bars is None or bars.empty:
        return None
    d = 1.0 if side == "BUY" else -1.0
    sl, tp = entry - d * sl_dist, entry + d * tp_dist
    be_on = False
    for ts, b in bars.iterrows():
        hi, lo, cl = float(b["High"]), float(b["Low"]), float(b["Close"])
        stop = entry if be_on else sl
        adverse = lo <= stop if d > 0 else hi >= stop
        favor = hi >= tp if d > 0 else lo <= tp
        if adverse:
            return {"reason": "be" if be_on else "sl", "exit": stop, "bar": ts}
        if favor:
            return {"reason": "tp", "exit": tp, "bar": ts}
        if be_trigger is not None and not be_on:
            ext = hi if d > 0 else lo
            if (ext - entry) * d >= be_trigger * sl_dist:
                be_on = True
    return {"reason": "duration", "exit": float(bars["Close"].iloc[-1]), "bar": bars.index[-1]}


def excursions(side: str, entry: float, bars: pd.DataFrame) -> tuple[float, float]:
    """(MAE, MFE) in price units, both >= 0, over the given bars."""
    if bars is None or bars.empty:
        return float("nan"), float("nan")
    d = 1.0 if side == "BUY" else -1.0
    if d > 0:
        return max(0.0, entry - float(bars["Low"].min())), max(0.0, float(bars["High"].max()) - entry)
    return max(0.0, float(bars["High"].max()) - entry), max(0.0, entry - float(bars["Low"].min()))


def summarize(rs: list[float], pips: list[float]) -> dict:
    n = len(rs)
    wins = [(r, p) for r, p in zip(rs, pips) if r > 0]
    losses = [(r, p) for r, p in zip(rs, pips) if r <= 0]
    aw = sum(r for r, _ in wins) / len(wins) if wins else None
    al = sum(r for r, _ in losses) / len(losses) if losses else None
    gw, gl = sum(r for r, _ in wins), -sum(r for r, _ in losses)
    return {"n": n, "win_rate": round(len(wins) / n, 3) if n else None,
            "avg_win_r": None if aw is None else round(aw, 3), "avg_loss_r": None if al is None else round(al, 3),
            "avg_win_pips": round(sum(p for _, p in wins) / len(wins), 1) if wins else None,
            "avg_loss_pips": round(sum(p for _, p in losses) / len(losses), 1) if losses else None,
            "payoff": round(aw / -al, 3) if aw is not None and al not in (None, 0) else None,
            "total_r": round(sum(rs), 3), "total_pips_net": round(sum(pips), 1),
            "pf": round(gw / gl, 3) if gl > 0 else None,
            "breakeven_win_rate": round(-al / (aw - al), 3) if aw is not None and al not in (None, 0) else None}


def _reason_bucket(r: str | None) -> str:
    r = str(r or "")
    return {"sl": "stop", "stop_out": "stop", "tp": "target", "duration": "time"}.get(r, "other")


def _latest(ev, kind, pid, key="position_id"):
    rows = [e for e in ev if e.get("event") == kind and str(e.get(key)) == pid]
    return rows[-1] if rows else None


def explore_trades(ev: list[dict]) -> list[dict]:
    closed = {r["position_id"]: r for r in eb.closed_r(ev)}
    verdict = {}
    for a in ev:
        if a.get("event") == "annotation" and a.get("annotation_type") == "signal_bar_audit":
            verdict[str(a.get("ref_position_id"))] = a.get("verdict")
    out = []
    for pid, r in closed.items():
        o = _latest(ev, "open", pid)
        c = next(e for e in ev if e.get("event") == "close" and str(e.get("position_id")) == pid)
        reb = _latest(ev, "rebase", pid)
        mo = next((e for e in ev if e.get("event") == "mt5_open" and str(e.get("position_id")) == pid and e.get("ok")), None)
        anns = [a for a in ev if a.get("event") == "annotation" and str(a.get("ref_position_id")) == pid
                and isinstance(a.get("corrected_pnl_r"), (int, float))]
        ann = anns[-1] if anns else None
        src = reb or o
        entry, sl, tp = float(src["entry_mid"]), float(src["sl"]), float(src["tp"])
        if c.get("exit_basis") == "mt5_broker":
            reason = c.get("exit_reason")
        elif ann is not None and ann.get("corrected_exit_reason") in ("sl", "tp", "stop_out"):
            reason = ann["corrected_exit_reason"]
        else:
            reason = c.get("exit_reason")
        out.append({"position_id": pid, "pair": o["pair"], "side": o["side"], "t0": o["ts_utc"], "entry": entry, "sl": sl,
                    "tp": tp, "horizon": int(o.get("horizon_bars") or 8), "cost_pips": float(o.get("assumed_cost_pips") or 1.4),
                    "pnl_r": r["pnl_r"], "pips_net": r["pips_net"], "reason": reason, "verdict": verdict.get(pid),
                    "broker_truth": r["broker_truth"], "mt5_open": mo, "close": c, "annotation": ann})
    return out


def _bars(pair: str, cache: dict, now: pd.Timestamp) -> pd.DataFrame:
    if pair not in cache:
        df = pd.read_csv(ROOT / "data" / f"{pair}_1h.csv", index_col=0, parse_dates=True)
        df.index = pd.DatetimeIndex(df.index).tz_localize(None) if getattr(df.index, "tz", None) else pd.DatetimeIndex(df.index)
        cache[pair] = finished(df[["Open", "High", "Low", "Close"]].astype(float).sort_index(), now)
    return cache[pair]


def enrich(trades: list[dict], now: pd.Timestamp) -> None:
    cache: dict = {}
    for t in trades:
        pip = eb.pip_size(t["pair"])
        bars = _bars(t["pair"], cache, now)
        t0 = _ts(t["t0"])
        hist = bars[(bars.index + pd.Timedelta(hours=1)) <= t0]
        atr = float(true_range_atr(hist).iloc[-1]) if len(hist) > ATR_PERIOD else float("nan")
        rng = (hist["High"] - hist["Low"]).tail(24)
        t["atr_pips"] = atr / pip
        t["noise_1h_pips"] = float(rng.median()) / pip if len(rng) else float("nan")
        t["sl_pips"] = abs(t["entry"] - t["sl"]) / pip
        t["tp_pips"] = abs(t["tp"] - t["entry"]) / pip
        t["sl_atr"] = t["sl_pips"] / t["atr_pips"] if t["atr_pips"] else float("nan")
        t["tp_atr"] = t["tp_pips"] / t["atr_pips"] if t["atr_pips"] else float("nan")
        mo = t.get("mt5_open") or {}
        t["spread_pips"] = float(mo.get("spread_pips")) if mo.get("spread_pips") is not None else 1.0
        w = window(bars, t0, t["horizon"])
        t["bars_in_window"] = len(w)
        t["window_complete"] = len(w) > 0 and (w.index[-1] + pd.Timedelta(hours=1)) >= t0 + pd.Timedelta(hours=t["horizon"])
        t["_w"] = w
        base = simulate(t["side"], t["entry"], abs(t["entry"] - t["sl"]), abs(t["tp"] - t["entry"]), w)
        t["replay_reason"] = base["reason"] if base else None
        if base:
            held = w[w.index <= base["bar"]]
            mae, mfe = excursions(t["side"], t["entry"], held)
        else:
            mae = mfe = float("nan")
        t["mae_pips"], t["mfe_pips"] = mae / pip, mfe / pip
        t["mae_r"], t["mfe_r"] = t["mae_pips"] / t["sl_pips"], t["mfe_pips"] / t["sl_pips"]


def variants(t: dict) -> dict[str, tuple[float, float]]:
    """name -> (sl_dist_price, be_trigger)"""
    pip = eb.pip_size(t["pair"])
    sl0, tp = abs(t["entry"] - t["sl"]), abs(t["tp"] - t["entry"])
    atr = t["atr_pips"] * pip
    v = {"current (planned SL)": (sl0, None), "current + breakeven at +1R": (sl0, BE_TRIGGER_R),
         "current + breakeven at +0.5R": (sl0, 0.5)}
    for m in ATR_VARIANTS:
        v[f"SL {m:g} ATR"] = (m * atr, None)
    for f in TP_FRACTIONS:
        v[f"SL {f:g} x TP distance"] = (f * tp, None)
    return v


def whatif(trades: list[dict]) -> dict:
    res: dict[str, dict] = {}
    names = list(variants(trades[0]).keys()) if trades else []
    for name in names:
        rs_new, rs_orig, pips, reasons, flags_spread, flags_noise, skipped = [], [], [], {}, 0, 0, 0
        for t in trades:
            pip = eb.pip_size(t["pair"])
            sl_d, be = variants(t)[name]
            tp_d = abs(t["tp"] - t["entry"])
            if not (sl_d > 0) or math.isnan(sl_d):
                skipped += 1; continue
            s = simulate(t["side"], t["entry"], sl_d, tp_d, t["_w"], be_trigger=be)
            if s is None:
                skipped += 1; continue
            d = 1.0 if t["side"] == "BUY" else -1.0
            net = (s["exit"] - t["entry"]) * d / pip - t["cost_pips"]
            pips.append(net)
            rs_new.append(net / (sl_d / pip))
            rs_orig.append(net / t["sl_pips"])
            b = s["reason"]
            reasons[b] = reasons.get(b, 0) + 1
            flags_spread += int(sl_d / pip < 1.5 * t["spread_pips"])
            flags_noise += int(sl_d / pip < t["noise_1h_pips"])
        s_new = summarize(rs_new, pips)
        res[name] = {**s_new, "total_r_fixed_lot": round(sum(rs_orig), 3), "exits": reasons, "skipped": skipped,
                     "stop_inside_1.5x_spread": flags_spread, "stop_inside_median_1h_range": flags_noise}
    return res


def mt5_dollars(trades: list[dict]) -> dict:
    """Read-only: planned $ at TP/SL (order_calc_profit, 0.01 lot) vs actual deal $ for mirrored trades."""
    try:
        import MetaTrader5 as mt5
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}
    if not mt5.initialize():
        return {"available": False, "error": str(mt5.last_error())}
    rows = []
    try:
        for t in trades:
            mo = t.get("mt5_open")
            if not mo or not mo.get("ticket"):
                continue
            sym, ticket = mo.get("symbol"), int(mo["ticket"])
            deals = mt5.history_deals_get(position=ticket) or ()
            outs = [d for d in deals if int(d.entry) in (1, 3)]
            if not outs:
                continue
            actual = round(sum(float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, "fee", 0.0))
                               for d in deals), 2)
            fill = float(mo.get("fill_price") or t["entry"])
            sl_b = float(mo.get("sl_sent") if mo.get("sltp_modified") is False else mo.get("sl"))
            tp_b = float(mo.get("tp_sent") if mo.get("sltp_modified") is False else mo.get("tp"))
            ot = mt5.ORDER_TYPE_BUY if t["side"] == "BUY" else mt5.ORDER_TYPE_SELL
            win = mt5.order_calc_profit(ot, sym, float(mo.get("volume") or 0.01), fill, tp_b)
            loss = mt5.order_calc_profit(ot, sym, float(mo.get("volume") or 0.01), fill, sl_b)
            last = max(outs, key=lambda d: int(d.time_msc))
            pip = eb.pip_size(t["pair"])
            d = 1.0 if t["side"] == "BUY" else -1.0
            reason = {4: "sl", 5: "tp", 3: "expert", 0: "manual", 1: "manual", 2: "manual", 6: "stop_out"}.get(int(last.reason), str(last.reason))
            row = {"position_id": t["position_id"], "pair": t["pair"], "side": t["side"], "ticket": ticket,
                   "planned_win_usd": None if win is None else round(win, 2),
                   "planned_loss_usd": None if loss is None else round(loss, 2), "actual_usd": actual,
                   "broker_exit_reason": reason, "exit_price": float(last.price), "planned_sl": sl_b, "planned_tp": tp_b,
                   "stop_vs_target_dist": round(abs(fill - sl_b) / abs(tp_b - fill), 3) if tp_b != fill else None}
            if reason == "sl":
                row["sl_slippage_pips"] = round((sl_b - float(last.price)) * d / pip, 2)  # + = filled worse than SL
            row["loss_exceeds_planned_win"] = bool(win is not None and actual < 0 and -actual > win + 1e-9)
            rows.append(row)
    finally:
        mt5.shutdown()
    return {"available": True, "rows": rows}


def main_fills(now: pd.Timestamp) -> list[dict]:
    from forex_lab import paper_shadow as ps

    ev = ps.load_events()
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import news_tag_report as ntr  # noqa: PLC0415

        rmap = {r["position_id"]: r for r in ntr.main_rows(ev)}
    except Exception:  # noqa: BLE001
        rmap = {}
    refs = ps.excluded_refs(ev)
    out = []
    cache: dict = {}
    for o in ev:
        if o.get("event") != "open" or o.get("conf") is None:
            continue
        pid = str(o["position_id"]); pip = eb.pip_size(o["pair"])
        entry, sl, tp = float(o["entry_mid"]), float(o["sl"]), float(o["tp"])
        bars = _bars(o["pair"], cache, now)
        t0 = _ts(o["ts_utc"])
        hist = bars[(bars.index + pd.Timedelta(hours=1)) <= t0]
        atr = float(true_range_atr(hist).iloc[-1]) / pip
        w = window(bars, t0, int(o.get("horizon_bars") or 8))
        s = simulate(o["side"], entry, abs(entry - sl), abs(tp - entry), w)
        c = next((e for e in ev if e.get("event") == "close" and str(e.get("position_id")) == pid), None)
        r = rmap.get(pid)
        out.append({"pair": o["pair"], "side": o["side"], "entry_utc": o["ts_utc"], "excluded": ps.is_excluded(o, refs),
                    "sl_pips": round(abs(entry - sl) / pip, 1), "tp_pips": round(abs(tp - entry) / pip, 1),
                    "sl_atr": round(abs(entry - sl) / pip / atr, 2), "tp_atr": round(abs(tp - entry) / pip / atr, 2),
                    "journal_exit": c.get("exit_reason") if c else None, "pnl_r": r.get("pnl_r") if r else None,
                    "pips_net": r.get("pips_net") if r else None, "bar_replay_exit": s["reason"] if s else None})
    return out


def report(now: pd.Timestamp | None = None, use_mt5: bool = True) -> dict:
    now = now or pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    ev = eb.load()
    trades = explore_trades(ev)
    enrich(trades, now)
    rs = [t["pnl_r"] for t in trades]
    pips = [t["pips_net"] if t["pips_net"] is not None else 0.0 for t in trades]

    def exits(ts):
        g: dict[str, dict] = {}
        for t in ts:
            b = _reason_bucket(t["reason"])
            x = g.setdefault(b, {"n": 0, "r": 0.0, "pips_net": 0.0, "wins": 0})
            x["n"] += 1; x["r"] = round(x["r"] + t["pnl_r"], 3); x["pips_net"] = round(x["pips_net"] + (t["pips_net"] or 0), 1)
            x["wins"] += int(t["pnl_r"] > 0)
        return g

    cb = [t for t in trades if t["verdict"] != "would_not_fire"]
    med = lambda xs: round(float(pd.Series(xs).median()), 2) if xs else None  # noqa: E731
    winners = [t for t in trades if t["pnl_r"] > 0]
    losers = [t for t in trades if t["pnl_r"] <= 0]
    dur_l = [t for t in trades if _reason_bucket(t["reason"]) == "time"]
    out = {
        "generated_utc": str(now), "bars": "data/<PAIR>_1h.csv (MT5 broker OHLC, finished bars only); data/history ends 2026-10-09/12 so it cannot cover these trades",
        "explore": {"summary": summarize(rs, pips), "exits": exits(trades)},
        "closed_bar_valid": {"summary": summarize([t["pnl_r"] for t in cb], [t["pips_net"] or 0 for t in cb]), "exits": exits(cb)},
        "distances": {"median_sl_pips": med([t["sl_pips"] for t in trades]), "median_tp_pips": med([t["tp_pips"] for t in trades]),
                      "median_sl_atr": med([t["sl_atr"] for t in trades]), "median_tp_atr": med([t["tp_atr"] for t in trades]),
                      "min_sl_atr": round(min(t["sl_atr"] for t in trades), 2), "max_sl_atr": round(max(t["sl_atr"] for t in trades), 2),
                      "median_rr_tp_over_sl": med([t["tp_pips"] / t["sl_pips"] for t in trades]),
                      "median_atr_pips": med([t["atr_pips"] for t in trades]), "median_1h_range_pips": med([t["noise_1h_pips"] for t in trades]),
                      "median_cost_pips": med([t["cost_pips"] for t in trades]),
                      "median_cost_over_tp": med([t["cost_pips"] / t["tp_pips"] for t in trades])},
        "stops_honored": {"losses_beyond_-1.1R": [{"pair": t["pair"], "pnl_r": t["pnl_r"], "reason": t["reason"], "t0": t["t0"]}
                                                  for t in trades if t["pnl_r"] < LOSS_LIMIT_R],
                          "worst_r": round(min(rs), 3)},
        "mae_mfe": {"winners_n": len(winners), "winners_median_mae_r": med([t["mae_r"] for t in winners]),
                    "winners_max_mae_r": round(max(t["mae_r"] for t in winners), 2) if winners else None,
                    "winners_mae_over_0.5R": sum(1 for t in winners if t["mae_r"] > 0.5),
                    "losers_n": len(losers), "losers_median_mfe_r": med([t["mfe_r"] for t in losers]),
                    "losers_mfe_at_least_0.5R": sum(1 for t in losers if t["mfe_r"] >= 0.5),
                    "losers_mfe_at_least_1R": sum(1 for t in losers if t["mfe_r"] >= 1.0),
                    "time_exits_n": len(dur_l), "time_exits_median_mfe_r": med([t["mfe_r"] for t in dur_l]),
                    "time_exits_median_mae_r": med([t["mae_r"] for t in dur_l])},
        "replay_check": {"reason_matches_journal": sum(1 for t in trades if t["replay_reason"] == t["reason"]),
                         "n": len(trades), "incomplete_windows": sum(1 for t in trades if not t["window_complete"])},
        "whatif_SMALL_SAMPLE_NOT_A_GATE": whatif(trades),
        "trades": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items()
                    if k in ("position_id", "pair", "side", "t0", "pnl_r", "pips_net", "reason", "replay_reason", "verdict",
                             "sl_pips", "tp_pips", "sl_atr", "atr_pips", "mae_r", "mfe_r", "cost_pips", "spread_pips")} for t in trades],
    }
    out["mt5_dollars"] = mt5_dollars(trades) if use_mt5 else {"available": False, "error": "--no-mt5"}
    out["main_fills"] = main_fills(now)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-mt5", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(report(use_mt5=not a.no_mt5), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())