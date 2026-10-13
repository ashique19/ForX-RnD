"""Clean-bar challengers 2026-10-13 (approved by Ashiqul 10-13 23:29 Dhaka): USDJPY, EURUSD, EURJPY.
CHALLENGER ONLY. Fits forex_lab.model.train_models on clean bars < holdout cutoff into data/challengers/20261013/
(cfg paths.models_dir overridden in memory; yaml untouched; never models/, never --promote, never champion files).
Scores challenger vs frozen champion (models/<PAIR>_xgboost.joblib) with the production predict+filter path and
forex_lab.backtest._simulate_trades, at measured round-trip costs (2 x one-way p50/p90).
Windows: pre-holdout OOS (challenger's own 20% validation slice, < cutoff) and holdout (>= cutoff).
Also runs forex_lab.backtest.walk_forward_backtest on pre-cutoff clean bars (family baseline, in-memory fits only).
Usage: python research/challenger/clean_challenger_20261013.py [PAIR ...]"""
from __future__ import annotations
import copy, json, sys, time, warnings, traceback
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research" / "edge_check"))
import edge_lib as L
from forex_lab.config_loader import load_config
from forex_lab.data_integrity import assert_bar_integrity
from forex_lab.features import LABEL_MAP, build_features, make_dataset
from forex_lab.holdout import load_cutoff, train_mask
from forex_lab.model import apply_signal_filters, load_model, predict_proba_aligned, train_models
from forex_lab.backtest import _attach_policy_columns, _simulate_trades, true_range_atr, walk_forward_backtest

OUT = ROOT / "data" / "challengers" / "20261013"; OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "run.log"; N_TRIALS = 70  # 67 prior + these 3
PROD = (ROOT / "models").resolve()

def log(*a):
    s = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    print(s, flush=True); LOG.open("a", encoding="utf-8").write(s + "\n")

def predict(model, cols, df, cfg, pair):
    feats = build_features(df, cfg, pair=pair); X = feats[cols].dropna()
    pr = predict_proba_aligned(model, X)
    fr = pd.DataFrame({"pred_raw": model.predict(X)}, index=X.index)
    fr["p_sell"], fr["p_hold"], fr["p_buy"] = pr[:, LABEL_MAP["SELL"]], pr[:, LABEL_MAP["HOLD"]], pr[:, LABEL_MAP["BUY"]]
    fr["confidence"] = pr.max(axis=1); fr["dir_edge"] = (fr.p_buy - fr.p_sell).abs()
    fr = _attach_policy_columns(fr, feats, df, cfg, pair); fr["pred"] = apply_signal_filters(fr, cfg)
    return fr["pred"]

def stats(t, var_sr):
    if t is None or len(t) < 2: return {"n": 0 if t is None else int(len(t))}
    r = pd.Series(t.net_return.values, index=pd.DatetimeIndex(t.entry_time.values))
    d = L.deflated_sharpe(r, N_TRIALS, var_sr)
    return {"n": int(len(r)), "pf": L.profit_factor(r), "win_rate": float((r > 0).mean()), "exp_bp": float(r.mean()*1e4),
            "total_ret": float(r.sum()), "max_dd": L.max_drawdown_additive(r), "sr": d["sr"], "dsr": d["dsr"], "psr0": d["psr0"]}

def run(pair, base, costs, cut, var_sr):
    df = L.load_history(pair)[["Open","High","Low","Close","Volume"]].astype(float)
    assert_bar_integrity(df, pair, base, context="challenger")
    pre = df[df.index < cut]
    cfg = copy.deepcopy(base); cfg.setdefault("paths", {})["models_dir"] = str(OUT.relative_to(ROOT)).replace("\\", "/")
    from forex_lab.model import model_dir
    assert model_dir(cfg).resolve() != PROD and PROD not in model_dir(cfg).resolve().parents, "refusing: would write production models"
    log(pair, "fit challenger on", len(pre), "bars", pre.index.min(), "..", pre.index.max(), "->", model_dir(cfg))
    X, y, _ = make_dataset(pre, cfg, pair=pair)
    keep = train_mask(X.index, horizon=int(cfg.get("horizon") or 0), bar_index=pre.index).to_numpy()
    Xk = X[keep]; split = int(len(Xk) * 0.8); val_start = Xk.index[split]; fit_end = Xk.index[split - 1]
    assert Xk.index.max() < cut
    res = train_models(pre, cfg, pair)
    log(pair, "challenger fit rows", res["n_train"], "val rows", res["n_val"], "fit_end", fit_end, "val_start", val_start)
    chal, ccols, _ = load_model(pair, cfg); champ, kcols, _ = load_model(pair, base)
    sig = {"challenger": predict(chal, ccols, df, base, pair), "champion": predict(champ, kcols, df, base, pair)}
    atr = true_range_atr(df, int(base.get("atr_period", 14)))
    out = {"pair": pair, "fit_end": str(fit_end), "val_start": str(val_start), "cutoff": str(cut), "val_accuracy": {k: res[k]["val_accuracy"] for k in ("xgboost","logistic")}, "books": {}}
    for book, s in sig.items():
        for q in ("p50", "p90"):
            c = copy.deepcopy(base); c["spread_pips"] = 2*float(costs.loc[pair, f"one_way_{q}"]); c["commission_pips"] = 0.0
            t = _simulate_trades(df, s, c, pair, atr=atr)
            t.to_csv(OUT / f"trades_{pair}_{book}_{q}.csv", index=False)
            out["books"][f"{book}_{q}"] = {"pre_holdout_oos": stats(t[(t.entry_time >= val_start) & (t.entry_time < cut)], var_sr),
                                           "holdout": stats(t[t.entry_time >= cut], var_sr)}
    log(pair, "scored; walk-forward baseline on pre-cutoff clean bars")
    try:
        c = copy.deepcopy(base); c["spread_pips"] = 2*float(costs.loc[pair, "one_way_p50"]); c["commission_pips"] = 0.0
        c.setdefault("model", {})["compare_logistic"] = False
        wf, wt, _ = walk_forward_backtest(pre, c, pair)
        out["walk_forward_pre_holdout_p50"] = {"folds": wf["folds"], **stats(wt.rename(columns={}), var_sr)}
    except Exception as e:
        out["walk_forward_pre_holdout_p50"] = {"error": repr(e)}
    (OUT / f"result_{pair}.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    log(pair, "DONE", json.dumps({k: {w: {m: round(v[w].get(m, float('nan')), 3) for m in ("pf","max_dd")} | {"n": v[w].get("n")} for w in v} for k, v in out["books"].items()}, default=float))

def main():
    base = load_config(); costs = L.measured_costs(); cut = load_cutoff(); assert cut is not None, "holdout lock missing"
    var_sr = float(json.loads((ROOT/"data/edge_check/clean_rescore_20261012.json").read_text())["var_sr_across_pairs"])
    for p in (sys.argv[1:] or ["USDJPY", "EURUSD", "EURJPY"]):
        try: run(p.upper(), base, costs, cut, var_sr)
        except Exception: log(p, "ERROR", traceback.format_exc())
    log("ALL DONE")

if __name__ == "__main__": main()
