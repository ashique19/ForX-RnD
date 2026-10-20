"""Pre-registered entry-edge round: H1 cost-to-risk gate, H2 cross-currency strength features, H3 USD de-dup.

Prereg: research/entry_edge/PREREG_ENTRY_EDGE_20261020.md (06dc692). Research only: config/default.yaml is never written
(in-memory override model.compare_logistic=false), no /pipeline, no /model/train, no Replay, no writes to models/ or journals.
Holdout cutoff (research/holdout.json) enforced by forex_lab.holdout inside walk_forward_backtest.
Outputs (ignored): data/edge_check/entry_edge_20261020/. Phases:
  run      - baseline re-sim (must reproduce HTF baseline) + H1 + H2 (WF re-run) trades per pair; no metrics printed
  insample - in-sample tables + verdict (entries < cutoff), H3 built here from baseline trades
  holdout  - scored ONCE (refuses if holdout.json exists or insample.json is missing)
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "research" / "edge_check", ROOT / "research" / "htf"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from forex_lab.backtest import _barrier_distances, _first_touch_exit, _spread_cost_frac, true_range_atr  # noqa: E402
from forex_lab.config_loader import pip_size_for_pair  # noqa: E402
from forex_lab.features import INV_LABEL_MAP, LABEL_MAP  # noqa: E402

PAIRS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY", "EURGBP", "GBPJPY"]
USD_PAIRS = PAIRS[:7]
CCYS = ["USD", "EUR", "JPY", "GBP", "AUD", "NZD", "CAD", "CHF"]
KS = (1, 4, 24)
HTF = ROOT / "data" / "edge_check" / "htf_20261020"
OUT = ROOT / "data" / "edge_check" / "entry_edge_20261020"
VARIANTS = ["baseline", "H1_cost_gate", "H2_ccy_strength", "H3_usd_dedup"]
COST_R_MAX = 0.10
N_TRIALS = 131
HOLD, BUY, SELL = LABEL_MAP["HOLD"], LABEL_MAP["BUY"], LABEL_MAP["SELL"]


# ---------------------------------------------------------------- simulator (== HTF simulate mode None, + exit time)
def simulate(ohlcv, signals, cfg, pair, atr, conf=None):
    horizon = int(cfg.get("horizon", 8))
    path = str((cfg.get("barrier") or {}).get("path", "high_low")).lower()
    sl_atr = float((cfg.get("barrier") or {}).get("sl_atr", 2.0))
    index = ohlcv.index
    n = len(index)
    opn = ohlcv["Open"].to_numpy()
    pos_of = pd.Series(np.arange(n), index=index)
    sig_loc = {int(pos_of[ts]): int(s) for ts, s in signals.items() if ts in pos_of.index}
    conf = conf if conf is not None else pd.Series(dtype=float)
    rows, next_free = [], 0
    for loc in sorted(sig_loc):
        sig = sig_loc[loc]
        if sig == HOLD or loc < next_free:
            continue
        e = loc + 1
        if e >= n:
            continue
        atr_e = float(atr.iloc[loc])
        if not np.isfinite(atr_e) or atr_e <= 0:
            continue
        entry = float(opn[e])
        side = 1 if sig == BUY else -1
        cost = _spread_cost_frac(pair, cfg, entry)
        tp_d, sl_d = _barrier_distances(atr_e, cfg)
        x_loc, x_px, reason = _first_touch_exit(ohlcv, e, horizon, side, entry, tp_d, sl_d, path)
        raw_ret = side * (x_px / entry - 1.0)
        net = raw_ret - cost
        rows.append({"entry_time": index[e], "decision_time": index[loc], "exit_bar_time": index[x_loc],
                     "side": INV_LABEL_MAP[sig], "entry": entry, "exit": x_px, "raw_return": raw_ret, "net_return": net,
                     "exit_reason": reason, "atr": atr_e, "r": net * entry / (sl_atr * atr_e),
                     "confidence": float(conf.get(index[loc], np.nan)) if len(conf) else np.nan})
        next_free = x_loc + 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- H1
def cost_r(pair, cfg_rt_pips, atr):
    pip = pip_size_for_pair(pair, {})
    return (cfg_rt_pips * pip) / (2.0 * atr)


def h1_gate(pred, pair, rt_p90_pips, atr):
    cr = cost_r(pair, rt_p90_pips, atr.reindex(pred.index))
    out = pred.copy()
    out[~(cr <= COST_R_MAX)] = HOLD
    return out


# ---------------------------------------------------------------- H2
def strength_table(hist: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """S_C,k(t): mean signed k-bar log return over pairs containing C that have a bar at t (own bar sequence per pair)."""
    contrib: dict[tuple[str, int], list[pd.Series]] = {(c, k): [] for c in CCYS for k in KS}
    for pair, df in hist.items():
        b, q = pair[:3], pair[3:]
        lc = np.log(df["Close"].astype(float))
        for k in KS:
            r = lc - lc.shift(k)
            contrib[(b, k)].append(r)
            contrib[(q, k)].append(-r)
    cols = {}
    for (c, k), parts in contrib.items():
        if parts:
            cols[f"S_{c}_{k}"] = pd.concat(parts, axis=1).mean(axis=1, skipna=True)
    out = pd.DataFrame(cols).sort_index()
    for c in CCYS:
        for k in KS:
            if f"S_{c}_{k}" not in out.columns:
                out[f"S_{c}_{k}"] = np.nan
    return out


def h2_features(pair: str, idx: pd.DatetimeIndex, st: pd.DataFrame) -> pd.DataFrame:
    b, q = pair[:3], pair[3:]
    s = st.reindex(idx)
    out = {}
    for k in KS:
        out[f"ccy_base_{k}"] = s[f"S_{b}_{k}"]
        out[f"ccy_quote_{k}"] = s[f"S_{q}_{k}"]
        out[f"ccy_diff_{k}"] = s[f"S_{b}_{k}"] - s[f"S_{q}_{k}"]
    return pd.DataFrame(out, index=idx)


# ---------------------------------------------------------------- H3
def usd_dedup(trades: pd.DataFrame) -> pd.DataFrame:
    """trades: one cost level, all pairs. Returns kept trades (crosses untouched)."""
    order = {p: i for i, p in enumerate(PAIRS)}
    t = trades.assign(_conf=trades["confidence"].fillna(-1.0), _po=trades["pair"].map(order))
    t = t.sort_values(["entry_time", "_conf", "_po"], ascending=[True, False, True])
    keep = np.ones(len(t), dtype=bool)
    busy_until = pd.Timestamp.min
    for i, (pair, et, xb) in enumerate(zip(t["pair"], t["entry_time"], t["exit_bar_time"])):
        if pair not in USD_PAIRS:
            continue
        if et < busy_until:
            keep[i] = False
            continue
        busy_until = max(busy_until, xb + pd.Timedelta(hours=1))
    return t[keep].drop(columns=["_conf", "_po"])


# ---------------------------------------------------------------- metrics
def metrics(t: pd.DataFrame) -> dict:
    if t is None or len(t) == 0:
        return {"n": 0, "pf": float("nan"), "total_r": 0.0, "max_dd_r": 0.0, "win_rate": float("nan")}
    t = t.sort_values("entry_time")
    r = t["r"].astype(float)
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    eq = r.cumsum()
    dd = min(float((eq - eq.cummax().clip(lower=0)).min()), 0.0)
    return {"n": int(len(t)), "pf": float(gp / gl) if gl > 0 else float("inf"), "total_r": float(r.sum()),
            "max_dd_r": dd, "win_rate": float((r > 0).mean())}


def welch_greater(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    return float(stats.ttest_ind(a, b, equal_var=False, alternative="greater").pvalue)


def daily_t(t: pd.DataFrame) -> float:
    if len(t) < 2:
        return float("nan")
    d = t.groupby(t["entry_time"].dt.floor("D"))["r"].sum()
    return float(stats.ttest_1samp(d, 0.0, alternative="greater").pvalue)


def removed(base: pd.DataFrame, kept: pd.DataFrame) -> pd.DataFrame:
    k = set(zip(kept["pair"], kept["entry_time"]))
    m = [(p, e) not in k for p, e in zip(base["pair"], base["entry_time"])]
    return base[m]


# ---------------------------------------------------------------- phases
def research_cfg():
    from forex_lab.config_loader import load_config
    cfg = copy.deepcopy(load_config())
    cfg.setdefault("model", {})["compare_logistic"] = False
    return cfg


def run(pairs):
    import edge_lib as L
    import forex_lab.features as F
    from forex_lab.backtest import walk_forward_backtest
    OUT.mkdir(parents=True, exist_ok=True)
    base = research_cfg()
    costs = L.measured_costs()
    hist = {p: L.load_history(p)[["Open", "High", "Low", "Close", "Volume"]].astype(float) for p in PAIRS}
    st = strength_table(hist)
    orig_build = F.build_features
    for pair in pairs:
        if (OUT / f"{pair}_trades.csv").exists():
            print(pair, "done already", flush=True); continue
        df = hist[pair]
        atr = true_range_atr(df, int(base.get("atr_period", 14)))
        preds = pd.read_csv(HTF / f"{pair}_preds.csv", index_col=0, parse_dates=True)
        pred = preds["pred"].astype(int)
        conf = preds["confidence"].astype(float)

        def _wrapped(d, c, pair=None, _orig=orig_build):
            out = _orig(d, c, pair=pair)
            return out.join(h2_features(str(pair).upper(), pd.DatetimeIndex(out.index), st))

        F.build_features = _wrapped
        try:
            _res, _tr, p2 = walk_forward_backtest(df, base, pair)
        finally:
            F.build_features = orig_build
        p2[[c for c in ("pred", "pred_raw", "confidence") if c in p2.columns]].to_csv(OUT / f"{pair}_h2_preds.csv")
        allt = []
        for q in ("p90", "p50"):
            rt = 2 * float(costs.loc[pair, f"one_way_{q}"])
            c = copy.deepcopy(base); c["spread_pips"] = rt; c["commission_pips"] = 0.0
            rt90 = 2 * float(costs.loc[pair, "one_way_p90"])
            allt.append(simulate(df, pred, c, pair, atr, conf).assign(variant="baseline", cost=q))
            allt.append(simulate(df, h1_gate(pred, pair, rt90, atr), c, pair, atr, conf).assign(variant="H1_cost_gate", cost=q))
            allt.append(simulate(df, p2["pred"].astype(int), c, pair, atr, p2["confidence"].astype(float)).assign(variant="H2_ccy_strength", cost=q))
        tr = pd.concat(allt).assign(pair=pair)
        # baseline must reproduce the HTF baseline exactly
        ref = pd.read_csv(HTF / f"{pair}_trades.csv")
        for q in ("p90", "p50"):
            a = tr[(tr.variant == "baseline") & (tr.cost == q)]
            b = ref[(ref.variant == "baseline") & (ref.cost == q)]
            if len(a) != len(b) or abs(a["r"].sum() - b["r"].sum()) > 1e-6:
                sys.exit(f"{pair} {q}: baseline does not reproduce HTF ({len(a)} vs {len(b)}, {a['r'].sum()} vs {b['r'].sum()})")
        tr.to_csv(OUT / f"{pair}_trades.csv", index=False)
        print(pair, "trades written; baseline reproduced; h2 pred bars", len(p2), "vs base", len(preds), flush=True)


def load_trades():
    tr = pd.concat([pd.read_csv(OUT / f"{p}_trades.csv", parse_dates=["entry_time", "decision_time", "exit_bar_time"]) for p in PAIRS])
    h3 = [usd_dedup(tr[(tr.variant == "baseline") & (tr.cost == q)]).assign(variant="H3_usd_dedup") for q in ("p90", "p50")]
    return pd.concat([tr] + h3, ignore_index=True)


def table(window: str):
    cut = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
    tr = load_trades()
    tr = tr[tr.entry_time < cut] if window == "insample" else tr[tr.entry_time >= cut]
    res = {"window": window, "cutoff_utc": str(cut), "span": [str(tr.entry_time.min()), str(tr.entry_time.max())]}
    for q in ("p90", "p50"):
        tq = tr[tr.cost == q]
        tab = {p: {v: metrics(tq[(tq.pair == p) & (tq.variant == v)]) for v in VARIANTS} for p in PAIRS}
        tab["POOLED"] = {v: metrics(tq[tq.variant == v]) for v in VARIANTS}
        res[q] = tab
    t90 = tr[tr.cost == "p90"]
    b = t90[t90.variant == "baseline"]
    bm = res["p90"]["POOLED"]["baseline"]
    ver = {}
    for v in VARIANTS[1:]:
        f = t90[t90.variant == v]
        m = res["p90"]["POOLED"][v]
        if window == "insample":
            other = b if v == "H2_ccy_strength" else removed(b, f)
            p = welch_greater(f["r"], other["r"])
            crit = {"pf_ge_base_plus_0.05": m["pf"] >= bm["pf"] + 0.05, "dd_not_worse": m["max_dd_r"] >= bm["max_dd_r"],
                    "trades_ge_500": m["n"] >= 500, "welch_p": p, "welch_p_lt_0.05/131": bool(p < 0.05 / N_TRIALS),
                    "comparison": "vs baseline trades" if v == "H2_ccy_strength" else f"kept vs removed ({len(other)} removed)"}
        else:
            p = daily_t(f)
            crit = {"pf_gt_1": m["pf"] > 1.0, "pf_gt_base": m["pf"] > bm["pf"], "dd_not_worse": m["max_dd_r"] >= bm["max_dd_r"],
                    "trades_ge_100": m["n"] >= 100, "daily_t_p": p, "daily_p_lt_0.05/3": bool(p < 0.05 / 3)}
        crit["PASS"] = bool(all(val for key, val in crit.items() if isinstance(val, (bool, np.bool_))))
        ver[v] = crit
    res["verdict_p90"] = ver
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["run", "insample", "holdout"])
    ap.add_argument("--pairs", nargs="*", default=PAIRS)
    a = ap.parse_args()
    if a.phase == "run":
        run(a.pairs); return
    if a.phase == "holdout":
        if not (OUT / "insample.json").exists():
            sys.exit("in-sample result must be written first")
        if (OUT / "holdout.json").exists():
            sys.exit("holdout already scored once - refusing to re-score")
    res = table(a.phase)
    (OUT / f"{a.phase}.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    print(json.dumps(res["verdict_p90"], default=float))


if __name__ == "__main__":
    main()
