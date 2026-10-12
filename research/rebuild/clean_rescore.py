"""Re-score the FROZEN production models on the clean HistData-rebuilt bars (2026-10-12).
Backtest only: loads models/<PAIR>_xgboost.joblib via forex_lab.model.load_model, predicts, applies the
production signal filters, simulates with forex_lab.backtest._simulate_trades. No fit, no Replay, no writes
outside data/edge_check/. Integrity gate (assert_bar_integrity) must pass or the pair is refused.
Costs: round trip = 2 x measured one-way (p50 / p90) from data/paper_shadow/measured_costs.csv.
Usage: python research/rebuild/clean_rescore.py"""
from __future__ import annotations
import copy, json, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research" / "edge_check"))
import edge_lib as L
from forex_lab.config_loader import load_config
from forex_lab.data_integrity import assert_bar_integrity, DataIntegrityError
from forex_lab.features import LABEL_MAP, build_features
from forex_lab.model import apply_signal_filters, load_model, predict_proba_aligned
from forex_lab.backtest import _attach_policy_columns, _simulate_trades, true_range_atr

PAIRS = ["EURUSD","USDJPY","GBPUSD","AUDUSD","NZDUSD","USDCAD","EURJPY","EURGBP","USDCHF","GBPJPY"]
CUT = pd.Timestamp(json.loads((ROOT/"research/holdout.json").read_text())["cutoff_utc"])
N_TRIALS = 67
OUT = ROOT / "data" / "edge_check"; OUT.mkdir(parents=True, exist_ok=True)

def stats(r: pd.Series, ts: pd.Series, var_sr: float) -> dict:
    r = pd.Series(r.values, index=pd.DatetimeIndex(ts.values)).dropna()
    if len(r) < 2: return {"n": len(r)}
    d = L.deflated_sharpe(r, N_TRIALS, var_sr)
    m = r.groupby(r.index.to_period("M")).sum()
    return {"n": int(len(r)), "pf": L.profit_factor(r), "win_rate": float((r > 0).mean()), "exp_bp": float(r.mean()*1e4),
            "max_dd": L.max_drawdown_additive(r), "sr_trade": d["sr"], "psr0": d["psr0"], "dsr": d["dsr"],
            "share_months_pos": float((m > 0).mean())}

def main():
    base = load_config(); costs = L.measured_costs(); trades, refused, sigstats = {}, {}, {}
    for pair in PAIRS:
        df = L.load_history(pair)[["Open","High","Low","Close","Volume"]].astype(float)
        try: assert_bar_integrity(df, pair, base, context="clean_rescore")
        except DataIntegrityError as e: refused[pair] = str(e); print(pair, "REFUSED", e); continue
        model, cols, mtype = load_model(pair, base)
        feats = build_features(df, base, pair=pair); X = feats[cols].dropna()
        proba = predict_proba_aligned(model, X)
        fr = pd.DataFrame({"pred_raw": model.predict(X)}, index=X.index)
        fr["p_sell"], fr["p_hold"], fr["p_buy"] = proba[:, LABEL_MAP["SELL"]], proba[:, LABEL_MAP["HOLD"]], proba[:, LABEL_MAP["BUY"]]
        fr["confidence"] = proba.max(axis=1); fr["dir_edge"] = (fr.p_buy - fr.p_sell).abs()
        fr = _attach_policy_columns(fr, feats, df, base, pair); fr["pred"] = apply_signal_filters(fr, base)
        sigstats[pair] = {"bars": int(len(fr)), "raw_dir": int((fr.pred_raw != LABEL_MAP["HOLD"]).sum()), "filtered_dir": int((fr.pred != LABEL_MAP["HOLD"]).sum())}
        atr = true_range_atr(df, int(base.get("atr_period", 14))); pip = L.pip_size(pair)
        for q in ("p50", "p90"):
            cfg = copy.deepcopy(base); cfg["spread_pips"] = 2*float(costs.loc[pair, f"one_way_{q}"]); cfg["commission_pips"] = 0.0
            t = _simulate_trades(df, fr["pred"], cfg, pair, atr=atr)
            trades[(pair, q)] = t
            t.to_csv(OUT / f"clean_rescore_{pair}_{q}.csv", index=False)
        print(pair, mtype, sigstats[pair], {q: len(trades[(pair,q)]) for q in ("p50","p90")}, flush=True)
    srs = [L.sharpe_stats(t.net_return)["sr"] for (p,q),t in trades.items() if q=="p50" and len(t) > 2]
    var_sr = float(np.nanvar(srs, ddof=1)) if len(srs) > 1 else float("nan")
    rep = {"run": "2026-10-12", "cutoff_utc": str(CUT), "n_trials": N_TRIALS, "var_sr_across_pairs": var_sr,
           "note": "frozen models were trained on Jetta-corrupted bars; full-history includes their training period (in-sample)", "refused": refused, "signals": sigstats, "per_pair": {}}
    rows = []
    for (pair, q), t in trades.items():
        if t.empty: rep["per_pair"].setdefault(pair, {})[q] = {"n": 0}; continue
        full = stats(t.net_return, t.entry_time, var_sr); ho = t[t.entry_time >= CUT]
        hos = stats(ho.net_return, ho.entry_time, var_sr) if len(ho) > 1 else {"n": int(len(ho))}
        rep["per_pair"].setdefault(pair, {})[q] = {"full": full, "holdout": hos}
        rows.append({"pair": pair, "cost": q, "n": full.get("n"), "pf": full.get("pf"), "dd": full.get("max_dd"), "dsr": full.get("dsr"),
                     "m+": full.get("share_months_pos"), "n_ho": hos.get("n"), "pf_ho": hos.get("pf"), "dd_ho": hos.get("max_dd")})
    (OUT / "clean_rescore_20261012.json").write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    pd.set_option("display.width", 250); print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("var_sr", var_sr)

if __name__ == "__main__": main()
