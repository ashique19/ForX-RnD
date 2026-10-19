"""Pre-registered research test: let daily-trend-aligned trades run longer (PREREG_TREND_HOLD_20261020.md, 9ac69ea).

Reuses the saved WF predictions from daily_trend_flip_wf_20261020.py (no retrain). F1-aligned trades get the variant,
everything else keeps TP2/SL2/8 bars. A conservative swap (1.0 pip per 17:00 NY rollover, Wednesday x3, never credited) is charged
to every trade. Research only; yaml never written; MT5 not queried. Phases: run | insample | holdout (holdout scored once).
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "research" / "edge_check"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
_spec = importlib.util.spec_from_file_location("htf_wf", Path(__file__).with_name("daily_trend_flip_wf_20261020.py"))
M = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(M)

from forex_lab.backtest import _first_touch_exit, _spread_cost_frac, true_range_atr  # noqa: E402
from forex_lab.config_loader import pip_size_for_pair  # noqa: E402
from forex_lab.features import INV_LABEL_MAP  # noqa: E402

PAIRS, OUT, HOLD, BUY, SELL = M.PAIRS, M.OUT, M.HOLD, M.BUY, M.SELL
BASE = (8, 2.0, 2.0, None)  # horizon, tp_atr (None = no TP), sl_atr, trail_atr
HOLD_VARIANTS = {"baseline": None, "H24": (24, 2.0, 2.0, None), "H48": (48, 2.0, 2.0, None), "H120": (120, 2.0, 2.0, None),
                 "H120_TP4": (120, 4.0, 2.0, None), "H120_TRAIL2": (120, None, 2.0, 2.0)}


def rollover_weights(start, end) -> tuple[np.ndarray, np.ndarray]:
    """17:00 New York rollover instants (UTC naive) with weights (Wednesday = 3), Mon-Fri only."""
    days = pd.date_range(pd.Timestamp(start).normalize() - pd.Timedelta(days=2), pd.Timestamp(end).normalize() + pd.Timedelta(days=2), freq="D")
    days = days[days.dayofweek < 5]
    inst = (days.tz_localize("America/New_York") + pd.Timedelta(hours=17)).tz_convert("UTC").tz_localize(None)
    w = np.where(days.dayofweek == 2, 3.0, 1.0)
    return inst.values, np.r_[0.0, np.cumsum(w)]


def nights(entry_t, exit_t, inst, cumw) -> np.ndarray:
    a = np.searchsorted(inst, np.asarray(entry_t, dtype="datetime64[ns]"), side="right")
    b = np.searchsorted(inst, np.asarray(exit_t, dtype="datetime64[ns]"), side="right")
    return cumw[b] - cumw[a]


def _trail_exit(ohlcv, e, horizon, side, entry, sl_d, trail_d):
    n = len(ohlcv); end = min(n, e + horizon)
    hi, lo, cl = ohlcv["High"].to_numpy(), ohlcv["Low"].to_numpy(), ohlcv["Close"].to_numpy()
    stop = entry - sl_d if side == 1 else entry + sl_d
    best = -np.inf if side == 1 else np.inf
    for i in range(e, end):
        if side == 1 and lo[i] <= stop:
            return i, float(stop), "trail_sl"
        if side == -1 and hi[i] >= stop:
            return i, float(stop), "trail_sl"
        if side == 1:
            best = max(best, hi[i]); stop = max(stop, best - trail_d)
        else:
            best = min(best, lo[i]); stop = min(stop, best + trail_d)
    return end - 1, float(cl[end - 1]), "timeout"


def simulate_hold(ohlcv, signals, cfg, pair, atr, aligned, params, swap_pips=0.0):
    """aligned: Series(bool) on signal index. params: tuple for aligned trades or None (all baseline)."""
    path = str((cfg.get("barrier") or {}).get("path", "high_low")).lower()
    index, n = ohlcv.index, len(ohlcv)
    opn = ohlcv["Open"].to_numpy()
    pos_of = pd.Series(np.arange(n), index=index)
    al = aligned.reindex(signals.index).fillna(False)
    rows, next_free = [], 0
    for ts, s in signals.items():
        sig = int(s)
        if sig == HOLD or ts not in pos_of.index:
            continue
        loc = int(pos_of[ts])
        if loc < next_free or loc + 1 >= n:
            continue
        atr_e = float(atr.iloc[loc])
        if not np.isfinite(atr_e) or atr_e <= 0:
            continue
        e = loc + 1; entry = float(opn[e]); side = 1 if sig == BUY else -1
        h, tp_a, sl_a, tr_a = params if (params is not None and bool(al.get(ts, False))) else BASE
        if tr_a is not None:
            x_loc, x_px, reason = _trail_exit(ohlcv, e, h, side, entry, sl_a * atr_e, tr_a * atr_e)
        else:
            x_loc, x_px, reason = _first_touch_exit(ohlcv, e, h, side, entry, tp_a * atr_e, sl_a * atr_e, path)
        raw = side * (x_px / entry - 1.0)
        rows.append({"entry_time": index[e], "decision_time": ts, "exit_time": index[x_loc] + pd.Timedelta(hours=1),
                     "side": INV_LABEL_MAP[sig], "entry": entry, "exit": x_px, "raw_return": raw,
                     "spread_frac": _spread_cost_frac(pair, cfg, entry), "exit_reason": reason, "atr": atr_e,
                     "aligned": bool(al.get(ts, False)), "bars": x_loc - e + 1})
        next_free = x_loc + 1
    t = pd.DataFrame(rows)
    if len(t):
        inst, cumw = rollover_weights(index[0], index[-1])
        t["nights"] = nights(t.entry_time.values, t.exit_time.values, inst, cumw)
        pip = pip_size_for_pair(pair, cfg)
        t["swap_frac"] = t["nights"] * swap_pips * pip / t["entry"]
        t["net_return"] = t["raw_return"] - t["spread_frac"] - t["swap_frac"]
        t["r"] = t["net_return"] * t["entry"] / (2.0 * t["atr"])  # 1R = the 2 ATR initial stop in every variant
        t["win"] = (t["net_return"] > 0).astype(int)
    return t


def run(pairs):
    import edge_lib as L
    base = M.research_cfg(); costs = L.measured_costs()
    for pair in pairs:
        dst = OUT / f"{pair}_hold_trades.csv"
        if dst.exists():
            print(pair, "done already", flush=True); continue
        preds = pd.read_csv(OUT / f"{pair}_preds.csv", index_col=0, parse_dates=True)
        df = L.load_history(pair)[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        atr = true_range_atr(df, int(base.get("atr_period", 14)))
        pred = preds["pred"].astype(int)
        f1 = M.trend_for_signals(M.daily_bars(df), pd.DatetimeIndex(preds.index))["f1"]
        aligned = ((pred == BUY) & (f1 > 0)) | ((pred == SELL) & (f1 < 0))
        out = []
        for q, sw in (("p90", 1.0), ("p90", 2.0), ("p50", 1.0)):
            c = copy.deepcopy(base); c["spread_pips"] = 2 * float(costs.loc[pair, f"one_way_{q}"]); c["commission_pips"] = 0.0
            for v, prm in HOLD_VARIANTS.items():
                out.append(simulate_hold(df, pred, c, pair, atr, aligned, prm, swap_pips=sw).assign(variant=v, cost=f"{q}_swap{sw:g}"))
        pd.concat(out).assign(pair=pair).to_csv(dst, index=False)
        print(pair, "hold trades written", flush=True)


def table(window):
    cut = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
    tr = pd.concat([pd.read_csv(OUT / f"{p}_hold_trades.csv", parse_dates=["entry_time", "decision_time", "exit_time"]) for p in PAIRS])
    tr = tr[tr.entry_time < cut] if window == "insample" else tr[tr.entry_time >= cut]
    V = list(HOLD_VARIANTS)
    res = {"window": window, "cutoff_utc": str(cut), "span": [str(tr.entry_time.min()), str(tr.entry_time.max())]}
    for q in sorted(tr.cost.unique()):
        tq = tr[tr.cost == q]
        tab = {p: {v: M.metrics(tq[(tq.pair == p) & (tq.variant == v)]) for v in V} for p in PAIRS}
        tab["POOLED"] = {v: M.metrics(tq[tq.variant == v]) for v in V}
        tab["POOLED_aligned_share"] = float(tq[tq.variant == "baseline"].aligned.mean())
        tab["POOLED_mean_nights"] = {v: float(tq[tq.variant == v].nights.mean()) for v in V}
        res[q] = tab
    mp, mpool = (30, 500) if window == "insample" else (15, 100)
    tab = res["p90_swap1"]; b = tab["POOLED"]["baseline"]; ver = {}
    for v in V[1:]:
        f = tab["POOLED"][v]
        imp = [p for p in PAIRS if tab[p][v]["n"] >= mp and tab[p][v]["pf"] >= tab[p]["baseline"]["pf"] + 0.02]
        c = [f["pf"] >= b["pf"] + 0.05, len(imp) >= 6, f["max_dd_r"] >= b["max_dd_r"], f["n"] >= mpool]
        ver[v] = {"pooled_pf_plus_0.05": c[0], "pairs_improved": imp, "pairs_ge_6": c[1], "dd_not_worse": c[2], "min_trades": c[3], "PASS": all(c)}
    res["verdict_p90_swap1"] = ver
    return res


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("phase", choices=["run", "insample", "holdout"]); ap.add_argument("--pairs", nargs="*", default=PAIRS)
    a = ap.parse_args()
    if a.phase == "run":
        run(a.pairs); return
    if a.phase == "holdout":
        if not (OUT / "hold_insample.json").exists():
            sys.exit("in-sample result must be written first")
        if (OUT / "hold_holdout.json").exists():
            sys.exit("holdout already scored once - refusing to re-score")
    res = table(a.phase)
    (OUT / f"hold_{a.phase}.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    print(json.dumps(res, default=float))


if __name__ == "__main__":
    main()