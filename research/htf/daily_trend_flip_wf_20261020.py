"""Pre-registered research test: daily-trend filters (F1/F2) and signal-flip exits (X1/X2/X3) on champion-recipe WF signals.

Prereg: research/htf/PREREG_DAILY_TREND_20261020.md (85b657b) and PREREG_FLIP_EXIT_20261020.md (53c2322).
Research only. config/default.yaml is never written (in-memory override: model.compare_logistic=false). No training writes,
no /pipeline, no /model/train, no Replay. The holdout cutoff in research/holdout.json is enforced by forex_lab.holdout.
Outputs (ignored data): data/edge_check/htf_20261020/. Phases:
  run      - serial per-pair WF predictions + trades for every variant (no metrics are printed)
  insample - in-sample tables + verdict (entries < cutoff)
  holdout  - scored ONCE (refuses if holdout.json already exists)
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

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "research" / "edge_check"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from forex_lab.backtest import _barrier_distances, _first_touch_exit, _spread_cost_frac, true_range_atr  # noqa: E402
from forex_lab.features import INV_LABEL_MAP, LABEL_MAP  # noqa: E402

PAIRS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY", "EURGBP", "GBPJPY"]
OUT = ROOT / "data" / "edge_check" / "htf_20261020"
VARIANTS = ["baseline", "F1_sma20_vs_sma50", "F2_close_vs_sma20", "X1_flip_exit", "X2_flip_or_hold_exit", "X3_flip_reverse"]
MIN_HOURS_PER_DAY = 12
HOLD, BUY, SELL = LABEL_MAP["HOLD"], LABEL_MAP["BUY"], LABEL_MAP["SELL"]


# ---------------------------------------------------------------- daily trend (NY 17:00 days, no lookahead)
def daily_bars(h1: pd.DataFrame) -> pd.DataFrame:
    idx = pd.DatetimeIndex(h1.index)
    ny = idx.tz_localize("UTC").tz_convert("America/New_York")
    key = (ny + pd.Timedelta(hours=7)).floor("D").tz_localize(None)
    g = h1.groupby(key)
    d = g.agg(Close=("Close", "last"))
    d["hours"] = g.size()
    end_ny = pd.DatetimeIndex(d.index).tz_localize("America/New_York") + pd.Timedelta(hours=17)
    d["end_utc"] = end_ny.tz_convert("UTC").tz_localize(None)
    d = d[d["hours"] >= MIN_HOURS_PER_DAY].copy()
    d["sma20"] = d["Close"].rolling(20).mean()
    d["sma50"] = d["Close"].rolling(50).mean()
    d["f1"] = np.sign(d["sma20"] - d["sma50"])  # NaN until 50 days
    d["f2"] = np.sign(d["Close"] - d["sma20"])
    return d


def trend_for_signals(daily: pd.DataFrame, signal_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Trend known at the decision time (signal bar t closes at t+1h): last day with end_utc <= t+1h."""
    q = pd.DataFrame({"t": signal_index, "decide": signal_index + pd.Timedelta(hours=1)}).sort_values("decide")
    dd = daily[["end_utc", "f1", "f2"]].sort_values("end_utc")
    m = pd.merge_asof(q, dd, left_on="decide", right_on="end_utc", direction="backward", allow_exact_matches=True)
    return m.set_index("t")[["f1", "f2", "end_utc"]].reindex(signal_index)


def apply_trend_filter(pred: pd.Series, trend: pd.Series) -> pd.Series:
    """Keep BUY only in up (+1), SELL only in down (-1); undefined/flat -> HOLD."""
    out = pred.copy()
    tr = trend.reindex(pred.index)
    bad = ((pred == BUY) & ~(tr > 0)) | ((pred == SELL) & ~(tr < 0))
    out[bad] = HOLD
    return out


# ---------------------------------------------------------------- simulator (baseline == forex_lab.backtest._simulate_trades)
def simulate(ohlcv, signals, cfg, pair, atr, raw=None, conf=None, floor=0.60, mode=None):
    """mode None = harness-identical. 'x1' flip exit, 'x2' flip-or-hold exit, 'x3' flip exit + reverse on filtered opposite signal."""
    horizon = int(cfg.get("horizon", 8))
    path = str((cfg.get("barrier") or {}).get("path", "high_low")).lower()
    sl_atr = float((cfg.get("barrier") or {}).get("sl_atr", 2.0))
    index = ohlcv.index
    n = len(index)
    opn = ohlcv["Open"].to_numpy()
    pos_of = pd.Series(np.arange(n), index=index)
    sig_loc = {int(pos_of[ts]): int(s) for ts, s in signals.items() if ts in pos_of.index}
    if mode:
        raw_loc = pd.Series(raw, dtype=float).reindex(index).to_numpy()
        conf_loc = pd.Series(conf, dtype=float).reindex(index).to_numpy()
    queue = sorted(sig_loc)
    rows, next_free, qi, forced = [], 0, 0, None
    while qi < len(queue) or forced is not None:
        if forced is not None:
            loc, sig = forced
            forced, is_forced = None, True
        else:
            loc = queue[qi]
            qi += 1
            sig, is_forced = sig_loc[loc], False
        if sig == HOLD or (not is_forced and loc < next_free):
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
        rev = None
        if mode:
            opp = SELL if side == 1 else BUY
            for s in range(e, min(e + horizon - 1, n - 1)):
                if s >= x_loc:
                    break  # barrier/timeout already ended the trade at or before bar s
                r, c = raw_loc[s], conf_loc[s]
                if not np.isfinite(r):
                    continue
                flip = (int(r) == opp and c >= floor) if mode in ("x1", "x3") else (int(r) != sig)
                if flip:
                    x_loc, x_px, reason = s + 1, float(opn[s + 1]), "flip"
                    if mode == "x3" and sig_loc.get(s) == opp:
                        rev = (s, opp)
                    break
        raw_ret = side * (x_px / entry - 1.0)
        net = raw_ret - cost
        rows.append({"entry_time": index[e], "decision_time": index[loc], "side": INV_LABEL_MAP[sig], "entry": entry,
                     "exit": x_px, "raw_return": raw_ret, "net_return": net, "win": int(net > 0), "exit_reason": reason,
                     "atr": atr_e, "r": net * entry / (sl_atr * atr_e)})
        next_free = x_loc if reason == "flip" else x_loc + 1
        forced = rev
    return pd.DataFrame(rows)

# ---------------------------------------------------------------- metrics (R units)
def metrics(t: pd.DataFrame) -> dict:
    if t is None or len(t) == 0:
        return {"n": 0, "pf": float("nan"), "total_r": 0.0, "max_dd_r": 0.0, "win_rate": float("nan"), "pf_ret": float("nan")}
    t = t.sort_values("entry_time")
    r = t["r"].astype(float)
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    eq = r.cumsum()
    dd = float((eq - eq.cummax().clip(lower=0)).min()) if len(eq) else 0.0
    dd = min(dd, 0.0)
    nr = t["net_return"].astype(float)
    return {"n": int(len(t)), "pf": float(gp / gl) if gl > 0 else float("inf"), "total_r": float(r.sum()), "max_dd_r": dd,
            "win_rate": float((r > 0).mean()),
            "pf_ret": float(nr[nr > 0].sum() / -nr[nr < 0].sum()) if (nr < 0).any() else float("inf")}


def verdict(tab: dict, min_pair: int, min_pool: int) -> dict:
    out = {}
    b = tab["POOLED"]["baseline"]
    for v in VARIANTS[1:]:
        f = tab["POOLED"][v]
        improved = [p for p in PAIRS if tab[p][v]["n"] >= min_pair and tab[p][v]["pf"] >= tab[p]["baseline"]["pf"] + 0.02]
        c1 = f["pf"] >= b["pf"] + 0.05
        c2 = len(improved) >= 6
        c3 = f["max_dd_r"] >= b["max_dd_r"]
        c4 = f["n"] >= min_pool
        out[v] = {"pooled_pf_plus_0.05": bool(c1), "pairs_improved": improved, "n_pairs_improved": len(improved),
                  "pairs_ge_6": bool(c2), "dd_not_worse": bool(c3), "min_trades": bool(c4), "PASS": bool(c1 and c2 and c3 and c4)}
    return out


# ---------------------------------------------------------------- phases
def research_cfg():
    from forex_lab.config_loader import load_config
    cfg = copy.deepcopy(load_config())
    cfg.setdefault("model", {})["compare_logistic"] = False
    return cfg


def run(pairs):
    import edge_lib as L
    from forex_lab.backtest import walk_forward_backtest
    OUT.mkdir(parents=True, exist_ok=True)
    base = research_cfg()
    costs = L.measured_costs()
    floor = float((base.get("signals") or {}).get("min_confidence", 0.60))
    for pair in pairs:
        if (OUT / f"{pair}_trades.csv").exists():
            print(pair, "done already", flush=True); continue
        df = L.load_history(pair)[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        _res, _tr, preds = walk_forward_backtest(df, base, pair)
        preds[[c for c in ("pred", "pred_raw", "confidence", "p_buy", "p_sell", "p_hold") if c in preds.columns]].to_csv(OUT / f"{pair}_preds.csv")
        atr = true_range_atr(df, int(base.get("atr_period", 14)))
        trend = trend_for_signals(daily_bars(df), pd.DatetimeIndex(preds.index))
        pred = preds["pred"].astype(int)
        raw = preds["pred_raw"].astype(int) if "pred_raw" in preds.columns else pred
        sig = {"baseline": pred, "F1_sma20_vs_sma50": apply_trend_filter(pred, trend["f1"]),
               "F2_close_vs_sma20": apply_trend_filter(pred, trend["f2"])}
        allt = []
        for q in ("p90", "p50"):
            c = copy.deepcopy(base); c["spread_pips"] = 2 * float(costs.loc[pair, f"one_way_{q}"]); c["commission_pips"] = 0.0
            for v, s in sig.items():
                allt.append(simulate(df, s, c, pair, atr).assign(variant=v, cost=q))
            for v, m in (("X1_flip_exit", "x1"), ("X2_flip_or_hold_exit", "x2"), ("X3_flip_reverse", "x3")):
                allt.append(simulate(df, pred, c, pair, atr, raw=raw, conf=preds["confidence"], floor=floor, mode=m).assign(variant=v, cost=q))
        pd.concat(allt).assign(pair=pair).to_csv(OUT / f"{pair}_trades.csv", index=False)
        print(pair, "trades written", len(preds), "pred bars", flush=True)


def table(window: str):
    cut = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
    tr = pd.concat([pd.read_csv(OUT / f"{p}_trades.csv", parse_dates=["entry_time", "decision_time"]) for p in PAIRS])
    tr = tr[tr.entry_time < cut] if window == "insample" else tr[tr.entry_time >= cut]
    res = {}
    for q in ("p90", "p50"):
        tq = tr[tr.cost == q]
        tab = {p: {v: metrics(tq[(tq.pair == p) & (tq.variant == v)]) for v in VARIANTS} for p in PAIRS}
        tab["POOLED"] = {v: metrics(tq[tq.variant == v]) for v in VARIANTS}
        res[q] = tab
    mp, mpool = (30, 500) if window == "insample" else (15, 100)
    res["verdict_p90"] = verdict(res["p90"], mp, mpool)
    res["window"] = window; res["cutoff_utc"] = str(cut)
    res["span"] = [str(tr.entry_time.min()), str(tr.entry_time.max())]
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
    print(json.dumps(res, default=float))


if __name__ == "__main__":
    main()