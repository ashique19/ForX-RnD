"""READ-ONLY audit: which past paper entries were decided on a forming or partial 1h bar.

Background: until the 2026-10-19 partial-bar fix (0641a94) the desk CSVs could hold the
current (forming) hour, bars saved mid-hour were never overwritten, and the MT5 terminal's
H1 history could lag the live tick, so board calls were sometimes scored on incomplete bars.

Method (no journal / data / model writes; MT5 is read through the read-only feed):
  * signal bar per entry: explore open ``signal_bar_dhaka``, else the explore ``decision``
    row nearest the open (<= 5 min) ``last_signal_dhaka``. Main rows carry no bar, so the
    explore row of the same pair within 5 min is used, else the entry hour (assumed
    forming: the pre-fix board always scored its latest, i.e. forming, bar).
  * ``forming``: entry before the signal bar's hour closed. ``stale_tip``: bar closed but
    it was still the board's newest bar at entry (``last_bar == last_signal``) within 2 h
    of its close, so the cache had not been refreshed past it (cannot prove partial).
  * truth frame per pair = fresh MT5 H1 (OHLC) with volume = MT5 tick volume x the pair's
    pre-2026-10-14 cache/MT5 ratio (the scale the frozen models and the cache used before
    the MT5 volume-scale flip; see docs/experiment_log.md 2026-10-19 audit).
  * the cache's MT5 volume scale flipped (cache/MT5 ~0.25-0.56 -> 1.0) for bars from
    2026-10-14 on 9 of 10 pairs; the scale in force at each entry is not journaled, so two
    variants are replayed: ``pre_flip`` (one ratio for all bars) and ``flipped`` (ratio
    before 2026-10-14, raw MT5 volume after), and the one that reproduces the journal wins.
  * re-scored with the current frozen model: ``closed_bar`` (closed H1 bar) and
    ``as_used`` (forming entries: the bar rebuilt from MT5 M1 bars from the bar's open up
    to the entry minute = what a forming bar held at entry; stale tips whose full bar does
    not reproduce the journal: the earliest M1 cut-off minute that does = evidence the
    stored bar was partial).
  * fidelity: ``as_used`` (or ``true`` for closed bars) vs the journaled conf/side. Only
    entries whose replay reproduces the journal within ``FIDELITY_TOL`` get a verdict;
    the rest are ``unverifiable``.
  * verdict: the call still fires on the closed bar = same direction and conf >= the
    floor the entry used. R = reconciled demo-fill R (explore_book.closed_r) / main R.
Prints a table or JSON (``--json``).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DHAKA = ZoneInfo("Asia/Dhaka")
FIDELITY_TOL = 0.02
FLIP_DATE = "2026-10-14"
PAIRS = ["AUDUSD", "EURGBP", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]


def parse_dhaka(value) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace(" Asia/Dhaka", "").strip())
    except ValueError:
        return None
    return (t.replace(tzinfo=DHAKA) if t.tzinfo is None else t).astimezone(timezone.utc)


def parse_utc(value) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t).astimezone(timezone.utc)


def bar_status(entry_utc: datetime, bar_utc: datetime, last_bar_utc: datetime | None = None) -> str:
    if entry_utc < bar_utc + timedelta(hours=1):
        return "forming"
    if last_bar_utc is not None and last_bar_utc == bar_utc and entry_utc < bar_utc + timedelta(hours=3):
        return "stale_tip"
    return "closed"


def still_fires(side_used, raw_true, conf_true, floor) -> bool:
    if not side_used or raw_true is None or conf_true is None or floor is None:
        return False
    return str(raw_true).upper() == str(side_used).upper() and float(conf_true) >= float(floor)


def faithful(side_used, conf_used, raw_rep, conf_rep, tol: float = FIDELITY_TOL) -> bool:
    if conf_used is None or conf_rep is None or raw_rep is None or not side_used:
        return False
    return str(raw_rep).upper() == str(side_used).upper() and abs(float(conf_rep) - float(conf_used)) <= tol


def nearest_row(rows: list[dict], pair: str, t: datetime, window_s: float = 300.0) -> dict | None:
    cand = [r for r in rows if str(r.get("pair") or "").upper() == pair and parse_utc(r.get("ts_utc"))
            and abs((parse_utc(r.get("ts_utc")) - t).total_seconds()) <= window_s]
    return min(cand, key=lambda r: abs((parse_utc(r.get("ts_utc")) - t).total_seconds())) if cand else None


def partial_from_m1(m1, bar_utc: datetime, entry_utc: datetime):
    """OHLCV of the forming bar as of ``entry_utc`` from M1 bars (UTC index) that had closed by then."""
    import pandas as pd

    b = pd.Timestamp(bar_utc.replace(tzinfo=None)); e = pd.Timestamp(entry_utc.replace(tzinfo=None))
    sel = m1[(m1.index >= b) & (m1.index + pd.Timedelta(minutes=1) <= e)]
    if sel.empty:
        return None
    return {"Open": float(sel["Open"].iloc[0]), "High": float(sel["High"].max()), "Low": float(sel["Low"].min()),
            "Close": float(sel["Close"].iloc[-1]), "Volume": float(sel["Volume"].sum()), "minutes": int(len(sel))}


def _mt5_frames(pairs):
    import pandas as pd
    from forex_lab import data as D
    from forex_lab import mt5_feed as F
    from forex_lab.config_loader import load_config

    cfg = load_config()
    h1, m1, info = {}, {}, {}
    F.connect(require_demo=True)
    try:
        mt5 = F._mt5()
        syms = F.resolve_symbols(pairs)
        off = D.expected_mt5_server_offset_hours()
        now_srv = datetime.now(timezone.utc) + timedelta(hours=off, minutes=5)
        for p in pairs:
            sym = syms.get(p)
            cache = D.load_cached_ohlcv(p, cfg, "1h")
            if not sym or cache is None:
                continue
            bars, _, stale = F.bars_1h_synced(sym, 800)
            bars = bars[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            bars.index = pd.DatetimeIndex(bars.index) - pd.Timedelta(hours=off)
            j = cache.join(bars, how="inner", rsuffix="_m")
            j = j[(j.Volume_m > 0) & (j.index < pd.Timestamp(FLIP_DATE))]
            ratio = float((j.Volume / j.Volume_m).median()) if len(j) >= 50 else 1.0
            truth = cache.copy()
            ov = truth.index.intersection(bars.index)
            truth.loc[ov, ["Open", "High", "Low", "Close"]] = bars.loc[ov, ["Open", "High", "Low", "Close"]].values
            truth.loc[ov, "Volume"] = (bars.loc[ov, "Volume"] * ratio).round().values
            flipped = truth.copy()
            late = ov[ov >= pd.Timestamp(FLIP_DATE)]
            flipped.loc[late, "Volume"] = bars.loc[late, "Volume"].values
            h1[p] = truth
            h1[p + "|flipped"] = flipped
            r = mt5.copy_rates_range(sym, mt5.TIMEFRAME_M1, now_srv - timedelta(days=16), now_srv)
            if r is not None and len(r):
                d = pd.DataFrame(r); d.index = pd.to_datetime(d["time"], unit="s") - pd.Timedelta(hours=off)
                m1[p] = d.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close",
                                          "tick_volume": "Volume"})[["Open", "High", "Low", "Close", "Volume"]].astype(float)
            info[p] = {"volume_ratio_pre_flip": round(ratio, 4), "h1_bars": int(len(bars)), "h1_stale": bool(stale),
                       "m1_bars": int(len(m1.get(p, []))), "m1_from_utc": str(m1[p].index.min()) if p in m1 else None}
    finally:
        F.shutdown()
    return cfg, h1, m1, info


class Scorer:
    def __init__(self, cfg, frames):
        self.cfg, self.frames, self.cache = cfg, frames, {}

    def score(self, pair: str, bar_utc: datetime, override: dict | None = None):
        import pandas as pd
        from forex_lab.signals import generate_signals

        key = (pair, bar_utc, None if override is None else tuple(sorted(override.items())))
        if key in self.cache:
            return self.cache[key]
        res = (None, None)
        df = self.frames.get(pair)
        ts = pd.Timestamp(bar_utc.replace(tzinfo=None))
        if df is not None and ts in df.index:
            d = df.loc[:ts].tail(1500).copy()
            if override:
                for c in ("Open", "High", "Low", "Close", "Volume"):
                    d.loc[ts, c] = override[c]
            try:
                s = generate_signals(d, self.cfg, pair, interval="1h")
                last = s.iloc[-1]
                if pd.Timestamp(last["datetime"]) == ts:
                    res = (str(last["raw_signal"]), float(last["confidence"]))
            except Exception:  # noqa: BLE001
                res = (None, None)
        self.cache[key] = res
        return res


def _main_r(main_ev):
    spec = importlib.util.spec_from_file_location("ntr_audit", ROOT / "scripts" / "news_tag_report.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return {r["position_id"]: r for r in mod.main_rows(main_ev)}


def agg(rows):
    rs = [r["counted_r"] for r in rows if isinstance(r.get("counted_r"), (int, float))]
    w = sum(x for x in rs if x > 0); lo = -sum(x for x in rs if x < 0)
    return {"entries": len(rows), "closed_counted": len(rs), "sum_r": round(sum(rs), 4),
            "pf": round(w / lo, 3) if lo else None,
            "pips_net": round(sum(r.get("pips_net") or 0.0 for r in rows if isinstance(r.get("counted_r"), (int, float))), 1)}


def audit() -> dict:
    from forex_lab import explore_book as eb
    from forex_lab import paper_shadow as ps

    ev = eb.load(); main_ev = ps.load_events()
    decisions = [e for e in ev if e.get("event") == "decision"]
    xopens = [e for e in ev if e.get("event") == "open"]
    cfg, h1, m1, info = _mt5_frames(PAIRS)
    sc = Scorer(cfg, h1)
    flip_ts = datetime.fromisoformat(FLIP_DATE).replace(tzinfo=timezone.utc)
    r_x = {r["position_id"]: r for r in eb.closed_r(ev)}
    r_m = _main_r(main_ev)
    refs = ps.excluded_refs(main_ev)
    rows = []

    def one(book, o, floor):
        t = parse_utc(o.get("ts_utc")); pair = str(o.get("pair")).upper(); side = str(o.get("side")).upper()
        bar = parse_dhaka(o.get("signal_bar_dhaka")); src = "open.signal_bar_dhaka"
        d = nearest_row(decisions, pair, t)
        if bar is None and d is not None:
            bar, src = parse_dhaka(d.get("last_signal_dhaka")), "decision.last_signal_dhaka"
        if bar is None and book == "main":
            x = nearest_row(xopens, pair, t)
            if x is not None and parse_dhaka(x.get("signal_bar_dhaka")):
                bar, src = parse_dhaka(x.get("signal_bar_dhaka")), "explore_open.signal_bar_dhaka"
        if bar is None:
            bar, src = t.replace(minute=0, second=0, microsecond=0), "assumed_entry_hour"
        last_bar = parse_dhaka(d.get("last_bar_dhaka")) if d is not None else None
        st = bar_status(t, bar, last_bar)
        best = None
        for variant in ("pre_flip", "flipped"):
            key = pair if variant == "pre_flip" else pair + "|flipped"
            vscale = 1.0 if (variant == "flipped" and bar >= flip_ts) else info.get(pair, {}).get("volume_ratio_pre_flip", 1.0)
            raw_c, conf_c = sc.score(key, bar)
            used, cut = None, None
            if st == "forming" and pair in m1:
                part = partial_from_m1(m1[pair], bar, t)
                if part:
                    cut = part["minutes"]
                    used = sc.score(key, bar, {**{k: part[k] for k in ("Open", "High", "Low", "Close")},
                                               "Volume": round(part["Volume"] * vscale)})
            elif st == "stale_tip" and not faithful(side, o.get("conf"), raw_c, conf_c) and pair in m1:
                for mnt in range(1, 60):
                    part = partial_from_m1(m1[pair], bar, bar + timedelta(minutes=mnt))
                    if not part:
                        continue
                    u = sc.score(key, bar, {**{k: part[k] for k in ("Open", "High", "Low", "Close")},
                                            "Volume": round(part["Volume"] * vscale)})
                    if faithful(side, o.get("conf"), *u):
                        used, cut = u, mnt
                        break
            rep_raw, rep_conf = used if used else (raw_c, conf_c)
            fid = faithful(side, o.get("conf"), rep_raw, rep_conf)
            cand = {"variant": variant, "closed": (raw_c, conf_c), "used": used, "cut": cut, "fid": fid,
                    "err": abs(rep_conf - float(o.get("conf"))) if rep_conf is not None and o.get("conf") is not None else 9}
            if best is None or (cand["fid"], -cand["err"]) > (best["fid"], -best["err"]):
                best = cand
        raw_t, conf_t = best["closed"]; fid = best["fid"]
        fires = still_fires(side, raw_t, conf_t, floor)
        partial_proven = st == "stale_tip" and fid and best["used"] is not None
        if st == "closed":
            verdict = "unaffected"
        elif not fid:
            verdict = "unverifiable"
        elif st == "stale_tip" and not partial_proven:
            verdict = "closed_bar_reproduced"
        else:
            verdict = "still_fires" if fires else "would_not_fire"
        rr = (r_x if book == "explore" else r_m).get(str(o.get("position_id")))
        rows.append({
            "book": book, "position_id": o.get("position_id"), "pair": pair, "side": side, "entry_utc": o.get("ts_utc"),
            "signal_bar_utc": bar.strftime("%Y-%m-%dT%H:%MZ"), "bar_source": src,
            "bar_age_min": round((t - bar).total_seconds() / 60), "bar_status": st,
            "m1_minutes_in_used_bar": best["cut"], "floor": floor, "conf_used": o.get("conf"),
            "as_used_replay": list(best["used"]) if best["used"] else None, "closed_bar": [raw_t, conf_t],
            "volume_variant": best["variant"], "replay_faithful": fid, "partial_proven": bool(partial_proven),
            "fires_on_closed_bar": fires if fid else None, "verdict": verdict,
            "counted_r": None if rr is None else rr.get("pnl_r"), "pips_net": None if rr is None else rr.get("pips_net"),
            "broker_truth": None if rr is None else rr.get("broker_truth"),
            "excluded": bool(book == "main" and ps.is_excluded(o, refs)),
        })

    for o in xopens:
        fl = o.get("threshold") if o.get("threshold") is not None else eb.EXPLORE_CONFIG["min_conf"]
        one("explore", o, float(fl))
    for o in [e for e in main_ev if e.get("event") == "open" and e.get("conf") is not None]:
        one("main", o, 0.60)

    # paper exits decided on the bar path: re-run explore_book.path_exit on the true (MT5) bars
    import pandas as pd
    opens_by = {str(e.get("position_id")): e for e in xopens}
    exits = []
    for c in [e for e in ev if e.get("event") == "close"]:
        basis = c.get("paper_exit_basis") or c.get("exit_basis")
        bar_rec = c.get("paper_exit_bar_utc") or c.get("exit_bar_utc")
        o = opens_by.get(str(c.get("position_id"))); df = h1.get(str(c.get("pair")).upper())
        if basis != "bar_path_barrier" or not bar_rec or o is None or df is None:
            continue
        bars = df.copy(); bars.index = pd.DatetimeIndex(bars.index).tz_localize("UTC")
        hit = eb.path_exit(o, bars, pd.Timestamp(parse_utc(c.get("ts_utc"))), eb.EXPLORE_CONFIG)
        rec = [c.get("paper_exit_reason") or c.get("exit_reason"), bar_rec]
        tru = [None, None] if not hit or hit.get("defer") else [hit["reason"], hit["bar_utc"].strftime("%Y-%m-%dT%H:%M:%SZ")]
        exits.append({"position_id": c.get("position_id"), "pair": c.get("pair"), "decided_utc": c.get("ts_utc"),
                      "recorded": rec, "true_bars": tru, "differs": rec != tru})

    ex = [r for r in rows if r["book"] == "explore"]
    aff = [r for r in ex if r["verdict"] not in ("unaffected", "closed_bar_reproduced")]
    summary = {
        "explore_entries": len(ex),
        "explore_by_status": {s: sum(1 for r in ex if r["bar_status"] == s) for s in ("forming", "stale_tip", "closed")},
        "explore_by_verdict": {v: sum(1 for r in ex if r["verdict"] == v) for v in ("unaffected", "closed_bar_reproduced", "still_fires", "would_not_fire", "unverifiable")},
        "explore_R_closed_bar_reproduced": agg([r for r in ex if r["verdict"] == "closed_bar_reproduced"]),
        "explore_R_affected": agg(aff),
        "explore_R_unaffected": agg([r for r in ex if r["verdict"] in ("unaffected", "closed_bar_reproduced")]),
        "explore_R_excluding_would_not_fire": agg([r for r in ex if r["verdict"] != "would_not_fire"]),
        "explore_R_would_not_fire": agg([r for r in ex if r["verdict"] == "would_not_fire"]),
        "explore_R_still_fires": agg([r for r in ex if r["verdict"] == "still_fires"]),
        "explore_R_unverifiable": agg([r for r in ex if r["verdict"] == "unverifiable"]),
        "explore_scorecard_reconciled": eb.sum_r(ev),
        "main": [{k: r[k] for k in ("pair", "side", "entry_utc", "bar_status", "conf_used", "as_used_replay", "closed_bar",
                                    "volume_variant", "replay_faithful", "verdict", "counted_r", "excluded")} for r in rows if r["book"] == "main"],
    }
    return {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "fidelity_tol": FIDELITY_TOL,
            "mt5": info, "entries": rows, "exits": exits, "summary": summary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only partial/forming-bar entry audit")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rep = audit()
    if a.json:
        sys.stdout.write(json.dumps(rep, indent=1, default=str) + "\n"); return 0
    for r in rep["entries"]:
        print(f"{r['book']:7} {r['entry_utc']} {r['pair']} {r['side']:4} bar={r['signal_bar_utc']} age={r['bar_age_min']:>3} "
              f"{r['bar_status']:9} used={r['conf_used']} as_used={r['as_used_replay']} closed={r['closed_bar']} "
              f"{r['volume_variant']} cut={r['m1_minutes_in_used_bar']} faithful={r['replay_faithful']} -> {r['verdict']} R={r['counted_r']}")
    print(json.dumps({"summary": rep["summary"], "mt5": rep["mt5"], "exits_checked": len(rep["exits"]),
                      "exits_differ": [e for e in rep["exits"] if e["differs"]]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())