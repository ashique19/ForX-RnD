"""Pre-registered research test: profit-lock (PREREG_PROFIT_LOCK_20261020.md, 701e799) and dynamic-target (PREREG_DYNAMIC_TARGET_20261020.md, 8eaecd9) variants + family-wide Bonferroni test.

Reuses saved WF predictions (no retrain). Closed-bar logic only. Research only; yaml never written.
Phases: run | insample | holdout (holdout scored once) | family (monthly paired t-tests for all 13 variants, in-sample only).
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

from forex_lab.backtest import _spread_cost_frac, true_range_atr  # noqa: E402
from forex_lab.features import INV_LABEL_MAP  # noqa: E402

PAIRS, OUT, HOLD, BUY, SELL = M.PAIRS, M.OUT, M.HOLD, M.BUY, M.SELL
LOCK_VARIANTS = ["baseline", "PL_A_lock05", "PL_B_closeback06", "PL_C_partial08", "DT1_tp_reset", "DT2_tp_outward_agree"]
MODES = (None, "A", "B", "C", "D1", "D2")
N_FAMILY = 15
ACT, LOCK, BACK = 0.8, 0.5, 0.6  # in R (1R = sl_atr x ATR)


def lock_exit(ohlcv, e, horizon, side, entry, r_dist, tp_r, mode, atr_arr=None, raw_arr=None, sig=None, tp_atr=2.0):
    """Return (exit_loc, raw_return, reason). Distances in price via r_dist (=1R). mode None|A|B|C."""
    n = len(ohlcv); end = min(n, e + horizon)
    hi, lo, cl, op = (ohlcv[c].to_numpy() for c in ("High", "Low", "Close", "Open"))
    d = lambda x: side * (x - entry) / r_dist  # noqa: E731  (in R)
    ret = lambda r: side * ((entry + side * r * r_dist) / entry - 1.0)  # noqa: E731
    stop_r, activated, pending, part = -1.0, False, False, None
    for i in range(e, end):
        if pending:  # PL_B: exit at this bar's Open
            r_exit = d(op[i]); raw = side * (op[i] / entry - 1.0)
            return i, raw, "closeback"
        fav, adv = (hi[i], lo[i]) if side == 1 else (lo[i], hi[i])
        hit_sl = d(adv) <= stop_r
        hit_tp = d(fav) >= tp_r
        if mode == "C" and part is None and d(fav) >= ACT and not hit_sl:
            part = ret(ACT)
        if hit_sl:
            raw = ret(stop_r); why = "sl_conflict" if hit_tp else ("lock_sl" if stop_r > -1.0 else "sl")
            return i, (raw if part is None else 0.5 * part + 0.5 * raw), why
        if hit_tp:
            raw = ret(tp_r)
            return i, (raw if part is None else 0.5 * part + 0.5 * raw), "tp"
        if mode == "B" and activated and d(cl[i]) < BACK and i + 1 < n:
            pending = True
            if i + 1 >= end:  # next Open is outside the horizon window: still exit there
                return i + 1, side * (op[i + 1] / entry - 1.0), "closeback"
        if mode in ("D1", "D2") and np.isfinite(atr_arr[i]) and atr_arr[i] > 0:
            new_tp = tp_atr * atr_arr[i] / r_dist
            if mode == "D1":
                tp_r = new_tp
            elif np.isfinite(raw_arr[i]) and int(raw_arr[i]) == sig and new_tp > tp_r:
                tp_r = new_tp
        if d(fav) >= ACT:
            activated = True
            if mode == "A":
                stop_r = max(stop_r, LOCK)
    raw = side * (cl[end - 1] / entry - 1.0)
    return end - 1, (raw if part is None else 0.5 * part + 0.5 * raw), "timeout"


def simulate_lock(ohlcv, signals, cfg, pair, atr, mode=None, raw=None):
    horizon = int(cfg.get("horizon", 8)); b = cfg.get("barrier") or {}
    sl_atr, tp_atr = float(b.get("sl_atr", 2.0)), float(b.get("tp_atr", 2.0))
    index, n = ohlcv.index, len(ohlcv); opn = ohlcv["Open"].to_numpy()
    pos_of = pd.Series(np.arange(n), index=index)
    atr_arr = atr.reindex(index).to_numpy(dtype=float)
    raw_arr = pd.Series(raw if raw is not None else signals, dtype=float).reindex(index).to_numpy()
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
        x_loc, raw_ret, why = lock_exit(ohlcv, e, horizon, side, entry, sl_atr * atr_e, tp_atr / sl_atr, mode,
                                        atr_arr=atr_arr, raw_arr=raw_arr, sig=sig, tp_atr=tp_atr)
        raw = raw_ret
        net = raw - _spread_cost_frac(pair, cfg, entry)
        rows.append({"entry_time": index[e], "decision_time": ts, "side": INV_LABEL_MAP[sig], "entry": entry, "raw_return": raw,
                     "net_return": net, "win": int(net > 0), "exit_reason": why, "atr": atr_e, "r": net * entry / (sl_atr * atr_e)})
        next_free = x_loc + 1
    return pd.DataFrame(rows)


def run(pairs):
    import edge_lib as L
    base = M.research_cfg(); costs = L.measured_costs()
    for pair in pairs:
        dst = OUT / f"{pair}_lock_trades.csv"
        if dst.exists():
            print(pair, "done already", flush=True); continue
        preds = pd.read_csv(OUT / f"{pair}_preds.csv", index_col=0, parse_dates=True)
        df = L.load_history(pair)[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        atr = true_range_atr(df, int(base.get("atr_period", 14))); pred = preds["pred"].astype(int)
        out = []
        for q in ("p90", "p50"):
            c = copy.deepcopy(base); c["spread_pips"] = 2 * float(costs.loc[pair, f"one_way_{q}"]); c["commission_pips"] = 0.0
            rawp = preds["pred_raw"].astype(int) if "pred_raw" in preds.columns else pred
            for v, m in zip(LOCK_VARIANTS, MODES):
                out.append(simulate_lock(df, pred, c, pair, atr, m, raw=rawp).assign(variant=v, cost=q))
        pd.concat(out).assign(pair=pair).to_csv(dst, index=False)
        print(pair, "lock trades written", flush=True)


def table(window):
    cut = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
    tr = pd.concat([pd.read_csv(OUT / f"{p}_lock_trades.csv", parse_dates=["entry_time", "decision_time"]) for p in PAIRS])
    tr = tr[tr.entry_time < cut] if window == "insample" else tr[tr.entry_time >= cut]
    res = {"window": window, "cutoff_utc": str(cut), "span": [str(tr.entry_time.min()), str(tr.entry_time.max())]}
    for q in ("p90", "p50"):
        tq = tr[tr.cost == q]
        tab = {p: {v: M.metrics(tq[(tq.pair == p) & (tq.variant == v)]) for v in LOCK_VARIANTS} for p in PAIRS}
        tab["POOLED"] = {v: M.metrics(tq[tq.variant == v]) for v in LOCK_VARIANTS}
        res[q] = tab
    mp, mpool = (30, 500) if window == "insample" else (15, 100)
    tab = res["p90"]; b = tab["POOLED"]["baseline"]; ver = {}
    for v in LOCK_VARIANTS[1:]:
        f = tab["POOLED"][v]
        imp = [p for p in PAIRS if tab[p][v]["n"] >= mp and tab[p][v]["pf"] >= tab[p]["baseline"]["pf"] + 0.02]
        c = [f["pf"] >= b["pf"] + 0.05, len(imp) >= 6, f["max_dd_r"] >= b["max_dd_r"], f["n"] >= mpool]
        ver[v] = {"pooled_pf_plus_0.05": c[0], "pairs_improved": imp, "pairs_ge_6": c[1], "dd_not_worse": c[2], "min_trades": c[3], "PASS": all(c)}
    res["verdict_p90"] = ver
    return res


def monthly_paired_p(tv: pd.DataFrame, tb: pd.DataFrame) -> dict:
    from scipy import stats
    mv = tv.groupby(tv.entry_time.dt.to_period("M"))["r"].sum()
    mb = tb.groupby(tb.entry_time.dt.to_period("M"))["r"].sum()
    idx = mv.index.union(mb.index)
    diff = mv.reindex(idx, fill_value=0.0) - mb.reindex(idx, fill_value=0.0)
    t, p2 = stats.ttest_1samp(diff.values, 0.0)
    p1 = p2 / 2 if t > 0 else 1 - p2 / 2
    return {"months": int(len(diff)), "mean_diff_r": float(diff.mean()), "t": float(t), "p_one_sided": float(p1), "bonferroni_pass": bool(p1 < 0.05 / N_FAMILY)}


def family():
    cut = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
    out = {"alpha": 0.05 / N_FAMILY, "n_variants": N_FAMILY}
    for fname, cost, variants in (("trades", "p90", M.VARIANTS[1:]), ("hold_trades", "p90_swap1", ["H24", "H48", "H120", "H120_TP4", "H120_TRAIL2"]),
                                  ("lock_trades", "p90", LOCK_VARIANTS[1:])):
        tr = pd.concat([pd.read_csv(OUT / f"{p}_{fname}.csv", parse_dates=["entry_time"]) for p in PAIRS])
        tr = tr[(tr.entry_time < cut) & (tr.cost == cost)]
        for v in variants:
            out[v] = monthly_paired_p(tr[tr.variant == v], tr[tr.variant == "baseline"])
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("phase", choices=["run", "insample", "holdout", "family"]); ap.add_argument("--pairs", nargs="*", default=PAIRS)
    a = ap.parse_args()
    if a.phase == "run":
        run(a.pairs); return
    if a.phase == "family":
        res = family(); (OUT / "family_insample.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8"); print(json.dumps(res)); return
    if a.phase == "holdout":
        if not (OUT / "lock_insample.json").exists():
            sys.exit("in-sample result must be written first")
        if (OUT / "lock_holdout.json").exists():
            sys.exit("holdout already scored once - refusing to re-score")
    res = table(a.phase)
    (OUT / f"lock_{a.phase}.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    print(json.dumps(res, default=float))


if __name__ == "__main__":
    main()