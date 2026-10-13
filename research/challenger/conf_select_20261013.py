"""Pre-registered min_confidence selection (research/challenger/PREREG_CONF_THRESHOLD_20261013.md, commit 5c7058d).
In-memory threshold override only; yaml untouched; no training. Selection uses the pre-holdout slice only; the holdout is
scored once, for the selected threshold. Output: data/challengers/20261013/conf_select.json"""
from __future__ import annotations
import copy, json, sys, warnings
from pathlib import Path
import pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"research"/"edge_check")); sys.path.insert(0, str(ROOT/"research"/"challenger"))
import edge_lib as L
from forex_lab.config_loader import load_config
from forex_lab.data_integrity import assert_bar_integrity
from forex_lab.model import load_model
from forex_lab.backtest import _simulate_trades, true_range_atr
from clean_challenger_20261013 import predict
GRID = [0.40, 0.45, 0.50, 0.55, 0.60]; N, VAR = 100, 0.002631; CUT = pd.Timestamp("2025-10-01")
SLICE = {"USDJPY": "2023-08-16 17:00", "EURUSD": "2023-09-01 04:00", "EURJPY": "2023-08-16 11:00"}
OUT = ROOT/"data"/"challengers"/"20261013"

def st(t):
    if len(t) < 2: return {"n": int(len(t))}
    r = pd.Series(t.net_return.values, index=pd.DatetimeIndex(t.entry_time.values)); d = L.deflated_sharpe(r, N, VAR)
    return {"n": int(len(r)), "pf": L.profit_factor(r), "max_dd": L.max_drawdown_additive(r), "dsr": d["dsr"], "exp_bp": float(r.mean()*1e4)}

def main():
    base = load_config(); costs = L.measured_costs(); res = {}
    chal_cfg = copy.deepcopy(base); chal_cfg.setdefault("paths", {})["models_dir"] = "data/challengers/20261013"
    for pair, s0 in SLICE.items():
        df = L.load_history(pair)[["Open","High","Low","Close","Volume"]].astype(float); assert_bar_integrity(df, pair, base, context="conf_select")
        atr = true_range_atr(df, int(base.get("atr_period", 14)))
        for book, mcfg in (("challenger", chal_cfg), ("champion", base)):
            model, cols, _ = load_model(pair, mcfg); trades = {}
            for th in GRID:
                c = copy.deepcopy(base); c.setdefault("signals", {})["min_confidence"] = th
                sig = predict(model, cols, df, c, pair)
                for q in ("p50", "p90"):
                    cq = copy.deepcopy(c); cq["spread_pips"] = 2*float(costs.loc[pair, f"one_way_{q}"]); cq["commission_pips"] = 0.0
                    trades[(th, q)] = _simulate_trades(df, sig, cq, pair, atr=atr)
            sel_rows = {}
            for th in GRID:
                sl = {q: st(trades[(th, q)][(trades[(th, q)].entry_time >= s0) & (trades[(th, q)].entry_time < CUT)]) for q in ("p50", "p90")}
                sl["eligible"] = sl["p90"]["n"] >= 50 and sl["p90"].get("max_dd", -1) >= -0.10
                sel_rows[th] = sl
            elig = [th for th in GRID if sel_rows[th]["eligible"]]
            pick = None
            if elig:
                best = max(sel_rows[th]["p90"]["pf"] for th in elig)
                pick = max(th for th in elig if sel_rows[th]["p90"]["pf"] >= best - 0.01)
            rec = {"selection_slice": {str(k): v for k, v in sel_rows.items()}, "selected": pick}
            if pick is not None:
                rec["holdout"] = {q: st(trades[(pick, q)][trades[(pick, q)].entry_time >= CUT]) for q in ("p50", "p90")}
            res[f"{pair}_{book}"] = rec
            print(pair, book, "selected", pick, json.dumps(rec.get("holdout"), default=float), flush=True)
            for th in GRID:
                r = sel_rows[th]; print(f"   th={th} n={r['p90']['n']} pf50={r['p50'].get('pf',float('nan')):.3f} pf90={r['p90'].get('pf',float('nan')):.3f} dd90={r['p90'].get('max_dd',float('nan')):.3f} elig={r['eligible']}", flush=True)
    (OUT/"conf_select.json").write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")

if __name__ == "__main__": main()
