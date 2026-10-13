"""Known FX edges on clean bars (pre-registered: research/known_edges/PREREG_KNOWN_EDGES_20261014.md, commit 5eb89ad).
Research/backtest only. Reads data/history, data/paper_shadow/measured_costs.csv, data/research/fred/*.csv.
Writes data/edge_check/known_edges_20261014.json only."""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"research"/"edge_check"))
import edge_lib as L
PAIRS = ["AUDUSD","EURGBP","EURJPY","EURUSD","GBPJPY","GBPUSD","NZDUSD","USDCAD","USDCHF","USDJPY"]
CCY = {"USD":"US","EUR":"EZ","GBP":"GB","JPY":"JP","AUD":"AU","NZD":"NZ","CAD":"CA","CHF":"CH"}
FULL = ("2015-01-01", "2025-09-30 23:59"); HO = "2025-10-01"; N_TRIALS = 108
COSTS = L.measured_costs()

def hourly(p): return L.load_history(p)[["Open","High","Low","Close"]].astype(float)
def daily_close(h):
    c = h.Close.copy(); c.index = c.index + pd.Timedelta(hours=3)  # day ends 21:00 UTC
    d = c.resample("1D").last().dropna(); return d

def cost_frac(p, q, px): return COSTS.loc[p, f"one_way_{q}"] * L.pip_size(p) / px
def vol_scale(r):
    v = r.ewm(span=60, min_periods=60).std() * np.sqrt(252)
    return (0.10 / v).clip(upper=3.0).shift(1)

def month_end_signal(c, fn):
    me = c.groupby(c.index.to_period("M")).tail(1).index
    s = pd.Series(np.nan, index=c.index); s.loc[me] = [fn(c.loc[:d]) for d in me]
    return s.ffill().fillna(0.0)

def pnl_from_pos(pos, c, p, q):
    r = c.pct_change()
    turn = pos.diff().abs().fillna(pos.abs())
    return (pos.shift(1) * r - turn.shift(1) * cost_frac(p, q, c).shift(1)).fillna(0.0)  # cost paid when trade executes at bar t close

def entries(sig):
    s = np.sign(sig); return int(((s != s.shift(1)) & (s != 0)).sum())

def rates():
    out = {}
    for k, cc in CCY.items():
        f = pd.read_csv(ROOT/"data"/"research"/"fred"/f"IR3TIB01{cc}M156N.csv")
        f.columns = ["date", "v"]; f["v"] = pd.to_numeric(f.v, errors="coerce")
        out[k] = f.set_index(pd.to_datetime(f.date)).v.dropna()
    return out

def build(q):
    C = {p: daily_close(hourly(p)) for p in PAIRS}
    R = rates(); res = {}
    def tsmom(fn):
        pn, tr, pp = [], 0, {}
        for p in PAIRS:
            c = C[p]; sig = month_end_signal(c, fn); pos = sig * vol_scale(c.pct_change())
            x = pnl_from_pos(pos.fillna(0), c, p, q); pn.append(x); tr += entries(sig); pp[p] = x
        return pd.concat(pn, axis=1).fillna(0).mean(axis=1), tr, pp
    res["tsmom_12_1"] = tsmom(lambda s: np.sign(s.iloc[-21] / s.iloc[-252] - 1) if len(s) > 252 else 0.0)
    res["tsmom_3m"] = tsmom(lambda s: np.sign(s.iloc[-1] / s.iloc[-64] - 1) if len(s) > 64 else 0.0)
    def carry_fn(p):
        b, qq = R[p[:3]], R[p[3:]]
        def f(s):
            d = s.index[-1]; lim = (d.to_period("M") - 1).to_timestamp()
            bb, qv = b[b.index <= lim], qq[qq.index <= lim]
            return float(np.sign(bb.iloc[-1] - qv.iloc[-1])) if len(bb) and len(qv) else 0.0
        return f
    pn, tr, pp = [], 0, {}
    for p in PAIRS:
        c = C[p]; sig = month_end_signal(c, carry_fn(p)); pos = sig * vol_scale(c.pct_change())
        x = pnl_from_pos(pos.fillna(0), c, p, q); pn.append(x); tr += entries(sig); pp[p] = x
    res["carry"] = (pd.concat(pn, axis=1).fillna(0).mean(axis=1), tr, pp)
    # XSMOM
    D = pd.DataFrame(C).ffill(); me = D.groupby(D.index.to_period("M")).tail(1).index
    ranks = pd.DataFrame(np.nan, index=D.index, columns=PAIRS)
    for d in me:
        i = D.index.get_loc(d)
        if i < 64: continue
        m = D.iloc[i] / D.iloc[i-63] - 1; o = m.rank()
        ranks.loc[d] = np.where(o > 7, 1.0, np.where(o <= 3, -1.0, 0.0))
    ranks = ranks.ffill().fillna(0.0); pn, tr, pp = [], 0, {}
    for p in PAIRS:
        c = C[p].reindex(D.index).ffill(); sig = ranks[p]; pos = sig * vol_scale(c.pct_change())
        x = pnl_from_pos(pos.fillna(0), c, p, q); pn.append(x); tr += entries(sig); pp[p] = x
    res["xsmom_3m"] = (pd.concat(pn, axis=1).fillna(0).sum(axis=1) / 6.0, tr, pp)
    for n in (20, 55):
        pn, tr, pp = [], 0, {}
        for p in PAIRS:
            c = C[p]; hi = c.shift(1).rolling(n).max(); lo = c.shift(1).rolling(n).min()
            raw = pd.Series(np.where(c > hi, 1.0, np.where(c < lo, -1.0, np.nan)), index=c.index).ffill().fillna(0.0)
            pos = raw * vol_scale(c.pct_change()); x = pnl_from_pos(pos.fillna(0), c, p, q); pn.append(x); tr += entries(raw); pp[p] = x
        res[f"donchian_{n}"] = (pd.concat(pn, axis=1).fillna(0).mean(axis=1), tr, pp)
    # session rules
    for name, sgn in (("session_breakout", 1.0), ("session_meanrev", -1.0)):
        pn, trades, pp = [], [], {}
        for p in PAIRS:
            h = hourly(p); h = h[h.index.dayofweek < 5]; day = h.index.normalize(); hr = h.index.hour
            rows = {}
            for d, g in h.groupby(day):
                a = g[g.index.hour < 7]; s = g[(g.index.hour >= 7) & (g.index.hour < 16)]; ex = g[g.index.hour == 16]
                if len(a) < 4 or s.empty or ex.empty: continue
                hi, lo = a.High.max(), a.Low.min()
                br = s[(s.Close > hi) | (s.Close < lo)]
                if br.empty: continue
                e = br.Close.iloc[0]; dirn = sgn * (1.0 if e > hi else -1.0); x = float(ex.Close.iloc[0])
                rows[d + pd.Timedelta(hours=16)] = dirn * (x / e - 1) - 2 * cost_frac(p, q, e)
            s = pd.Series(rows, dtype=float); s.index = (s.index + pd.Timedelta(hours=3)).normalize()
            pp[p] = s; trades.append(s)
        allt = pd.concat(trades, axis=1)
        res[name] = (allt.fillna(0).sum(axis=1) / len(PAIRS), allt, pp)
    return res

def metrics(x, var_sr, n_tr):
    x = x.dropna()
    if len(x) < 3: return {}
    d = L.deflated_sharpe(x, N_TRIALS, var_sr)
    return {"days": int(len(x)), "trades": int(n_tr), "pf": L.profit_factor(x), "sharpe": float(x.mean()/x.std(ddof=1)*np.sqrt(252)),
            "max_dd": L.max_drawdown_additive(x), "total": float(x.sum()), "dsr": d["dsr"], "sr_daily": d["sr"]}

def window(s, a, b=None): return s[(s.index >= a) & ((s.index <= b) if b else True)]
def ntr(tr, a, b=None):
    return int(window(tr, a, b).notna().sum().sum()) if isinstance(tr, pd.DataFrame) else tr

def main():
    out = {}; built = {q: build(q) for q in ("p50", "p90")}
    srs = [L.sharpe_stats(window(built["p50"][k][0], *FULL))["sr"] for k in built["p50"]]
    var_sr = float(np.var(srs, ddof=1)); out["var_sr"] = var_sr
    # trade counts for momentum/donchian rules are counted over full history (signal entries); session counts are per window
    for k in built["p50"]:
        out[k] = {}
        for q in ("p50", "p90"):
            x, tr, pp = built[q][k]
            out[k][q] = {"full": metrics(window(x, *FULL), var_sr, ntr(tr, *FULL)),
                         "per_pair_pf_full": {p: L.profit_factor(window(v, *FULL)) for p, v in pp.items()}}
    print("FULL WINDOW written first"); 
    for k in out:
        if k == "var_sr": continue
        f50, f90 = out[k]["p50"]["full"], out[k]["p90"]["full"]
        print(f"{k:17} PF {f50['pf']:.3f}/{f90['pf']:.3f} Sh {f50['sharpe']:.2f}/{f90['sharpe']:.2f} DD {f50['max_dd']:.3f}/{f90['max_dd']:.3f} tr {f50['trades']} DSR {f50['dsr']:.3f}/{f90['dsr']:.3f}")
    (ROOT/"data"/"edge_check"/"known_edges_20261014.json").write_text(json.dumps(out, indent=1, default=float))
    print("HOLDOUT (scored once)")
    for k in out:
        if k == "var_sr": continue
        for q in ("p50", "p90"):
            x, tr, pp = built[q][k]
            out[k][q]["holdout"] = metrics(window(x, HO), var_sr, ntr(tr, HO) if isinstance(tr, pd.DataFrame) else entries_window(k, q, built))
        h50, h90 = out[k]["p50"]["holdout"], out[k]["p90"]["holdout"]
        print(f"{k:17} PF {h50['pf']:.3f}/{h90['pf']:.3f} Sh {h50['sharpe']:.2f}/{h90['sharpe']:.2f} DD {h50['max_dd']:.3f}/{h90['max_dd']:.3f} tr {h50['trades']} DSR {h50['dsr']:.3f}/{h90['dsr']:.3f}")
    (ROOT/"data"/"edge_check"/"known_edges_20261014.json").write_text(json.dumps(out, indent=1, default=float))

def entries_window(k, q, built):
    # count position entries within the holdout from per-pair pnl activity proxy: recompute from signals is costly; report -1 = see full count
    return -1

if __name__ == "__main__": main()
