"""COT positioning study (pre-registered: research/cot/PREREG_COT_20261015.md, commit 3cd6418).
Research/backtest only. Reads data/history, data/research/cot/cot_fx_legacy.csv, measured costs, clean-rescore EURUSD trades.
Writes data/research/cot/cot_results_20261015.json only."""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT/"research"/"edge_check"))
import edge_lib as L
PAIRS = ["AUDUSD","EURGBP","EURJPY","EURUSD","GBPJPY","GBPUSD","NZDUSD","USDCAD","USDCHF","USDJPY"]
FULL = ("2015-01-01", "2025-09-30 23:59"); HO = "2025-10-01"; N_TRIALS = 113
COSTS = L.measured_costs()
SHUTDOWN_2025 = {"2025-09-30":"2025-11-19","2025-10-07":"2025-11-21","2025-10-14":"2025-11-25","2025-10-21":"2025-12-02","2025-10-28":"2025-12-05",
    "2025-11-04":"2025-12-09","2025-11-10":"2025-12-10","2025-11-18":"2025-12-12","2025-11-25":"2025-12-15","2025-12-02":"2025-12-17",
    "2025-12-09":"2025-12-19","2025-12-16":"2025-12-23","2025-12-23":"2025-12-29"}

def usable_dates(asof: pd.Series) -> pd.Series:
    hol = set(USFederalHolidayCalendar().holidays("2013-01-01", "2027-12-31"))
    out = []
    for t in asof:
        k = t.strftime("%Y-%m-%d")
        if k in SHUTDOWN_2025: pub = pd.Timestamp(SHUTDOWN_2025[k])
        elif pd.Timestamp("2018-12-18") <= t <= pd.Timestamp("2019-02-26"): pub = pd.Timestamp("2019-03-08")
        else:
            pub = t + pd.Timedelta(days=3)
            if any((t + pd.Timedelta(days=i)) in hol for i in (1, 2, 3)): pub = t + pd.Timedelta(days=6)  # slips to Monday
        mon = pub + pd.Timedelta(days=(7 - pub.dayofweek) % 7 or 7)  # first Monday strictly after publish date
        out.append(mon.normalize())
    return pd.Series(out, index=asof.index)

def cot_table():
    c = pd.read_csv(ROOT/"data"/"research"/"cot"/"cot_fx_legacy.csv", parse_dates=["asof"]).sort_values(["ccy","asof"])
    c["usable"] = usable_dates(c["asof"])
    g = c.groupby("ccy").net
    c["z52"] = (c.net - g.transform(lambda s: s.rolling(52, min_periods=52).mean())) / g.transform(lambda s: s.rolling(52, min_periods=52).std(ddof=1))
    c["d4"] = c.net - g.shift(4)
    return c

def ccy_dir_on_mondays(c, mondays, sig):
    D = {}
    for ccy, g in c.groupby("ccy"):
        g = g.sort_values("usable"); vals = []
        for m in mondays:
            h = g[g.usable <= m]
            if h.empty or (m - h["asof"].iloc[-1]).days > 21: vals.append(0.0); continue
            r = h.iloc[-1]
            if sig == "COT_Z52_CONTRA": v = 0.0 if pd.isna(r.z52) else (-1.0 if r.z52 > 2 else (1.0 if r.z52 < -2 else 0.0))
            elif sig == "COT_D4_MOM": v = 0.0 if pd.isna(r.d4) else float(np.sign(r.d4))
            else: v = 0.0 if pd.isna(r.d4) else -float(np.sign(r.d4))
            vals.append(v)
        D[ccy] = pd.Series(vals, index=mondays)
    D["USD"] = pd.Series(0.0, index=mondays)
    return D

def pair_sig(D, p):
    a, b = p[:3], p[3:]
    if b == "USD": return D[a]
    if a == "USD": return -D[b]
    return (D[a] - D[b]).clip(-1, 1)

def bars(p):
    h = L.load_history(p)[["Open","Close"]].astype(float); h = h[~h.index.duplicated()].sort_index()
    sh = h.copy(); sh.index = sh.index + pd.Timedelta(hours=3)  # trading day ends 21:00 UTC
    day = sh.index.normalize()
    d = pd.DataFrame({"open": sh.Open.groupby(day).first(), "close": sh.Close.groupby(day).last()}).dropna()
    return d[d.index.dayofweek < 5]

def pair_pnl(d, sig_mon, p, q):
    """Weekly Monday-open rebalance; daily pnl with gap split on rebalance days."""
    r = d.close.pct_change()
    scale = (0.10 / (r.ewm(span=60, min_periods=60).std() * np.sqrt(252))).clip(upper=3.0).shift(1)
    mon = d.index[d.index.dayofweek == 0]
    sig = sig_mon.reindex(mon, method="ffill").fillna(0.0)
    target = (sig * scale.reindex(mon)).fillna(0.0)
    pos = target.reindex(d.index).ffill().fillna(0.0)   # position held during day t (set at t's open on Mondays)
    prev = pos.shift(1).fillna(0.0)
    cost = COSTS.loc[p, f"one_way_{q}"] * L.pip_size(p) / d.open
    gap = d.open / d.close.shift(1) - 1; intra = d.close / d.open - 1
    is_reb = d.index.dayofweek == 0
    pnl = np.where(is_reb, prev * gap + pos * intra - (pos - prev).abs() * cost, prev * r)
    # on non-Monday days pos == prev, so prev*r is the held position's return
    return pd.Series(pnl, index=d.index).fillna(0.0), int(((np.sign(sig) != np.sign(sig).shift(1)) & (sig != 0)).sum())

def window(s, a, b=None): return s[(s.index >= a) & ((s.index <= b) if b else True)]

def metrics(x, var_sr, n_tr):
    x = x.dropna()
    if len(x) < 3 or x.std(ddof=1) == 0: return {"trades": int(n_tr)}
    d = L.deflated_sharpe(x, N_TRIALS, var_sr)
    return {"days": int(len(x)), "trades": int(n_tr), "pf": L.profit_factor(x), "sharpe": float(x.mean()/x.std(ddof=1)*np.sqrt(252)),
            "max_dd": L.max_drawdown_additive(x), "total": float(x.sum()), "dsr": d["dsr"]}

def main():
    c = cot_table(); B = {p: bars(p) for p in PAIRS}
    allmon = sorted(set().union(*[set(b.index[b.index.dayofweek == 0]) for b in B.values()]))
    mondays = pd.DatetimeIndex(allmon)
    sigs = ["COT_Z52_CONTRA", "COT_D4_MOM", "COT_D4_CONTRA"]
    res, port = {}, {}
    for s in sigs:
        D = ccy_dir_on_mondays(c, mondays, s)
        for q in ("p50", "p90"):
            pn, trades_full, trades_ho, sigs_p = [], 0, 0, {}
            for p in PAIRS:
                sm = pair_sig(D, p); x, _ = pair_pnl(B[p], sm, p, q); pn.append(x)
                sw = np.sign(sm); ent = (sw != sw.shift(1)) & (sw != 0)
                trades_full += int(window(ent, *FULL).sum()); trades_ho += int(window(ent, HO).sum())
            port[(s, q)] = (pd.concat(pn, axis=1).fillna(0).mean(axis=1), trades_full, trades_ho)
    var_sr = float(np.var([L.sharpe_stats(window(port[(s, "p50")][0], *FULL))["sr"] for s in sigs], ddof=1))
    res["var_sr"] = var_sr; res["n_trials"] = N_TRIALS
    # filter test (EURUSD champion clean-rescore trades)
    eur = c[c.ccy == "EUR"].sort_values("usable")
    filt = {}
    for q in ("p50", "p90"):
        t = pd.read_csv(ROOT/"data"/"edge_check"/f"clean_rescore_EURUSD_{q}.csv", parse_dates=["entry_time"])
        # exit date approximated by entry date + holding unknown -> use entry_time date for daily aggregation
        crowd = []
        for et in t.entry_time:
            h = eur[eur.usable <= et.normalize()]
            crowd.append(0.0 if h.empty or (et.normalize() - h["asof"].iloc[-1]).days > 21 else float(np.sign(h.net.iloc[-1])))
        t["crowd"] = crowd; t["sd"] = np.where(t.side == "BUY", 1.0, -1.0)
        for name, mask in (("CHAMPION_UNFILTERED", np.ones(len(t), bool)), ("FILTER_ALIGNED", t.sd == t.crowd), ("FILTER_AGAINST", t.sd == -t.crowd)):
            tt = t[mask]; daily = tt.groupby(tt.entry_time.dt.normalize()).net_return.sum()
            filt[(name, q)] = (daily, tt)
    def fm(name, q, a, b=None):
        daily, tt = filt[(name, q)]; sub = tt[(tt.entry_time >= a) & ((tt.entry_time <= b) if b else True)]
        m = metrics(window(daily, a, b), var_sr, len(sub)); m["pf_trades"] = L.profit_factor(sub.net_return); return m
    for s in sigs:
        res[s] = {q: {"full": metrics(window(port[(s, q)][0], *FULL), var_sr, port[(s, q)][1])} for q in ("p50", "p90")}
    for n in ("CHAMPION_UNFILTERED", "FILTER_ALIGNED", "FILTER_AGAINST"):
        res[n] = {q: {"full": fm(n, q, *FULL)} for q in ("p50", "p90")}
    out = ROOT/"data"/"research"/"cot"/"cot_results_20261015.json"
    out.write_text(json.dumps(res, indent=1, default=float)); print("FULL written", flush=True)
    for k in [*sigs, "CHAMPION_UNFILTERED", "FILTER_ALIGNED", "FILTER_AGAINST"]:
        f = res[k]; print(k, {q: {m: round(v, 4) if isinstance(v, float) else v for m, v in f[q]["full"].items()} for q in f})
    # holdout, scored once
    for s in sigs:
        for q in ("p50", "p90"): res[s][q]["holdout"] = metrics(window(port[(s, q)][0], HO), var_sr, port[(s, q)][2])
    for n in ("CHAMPION_UNFILTERED", "FILTER_ALIGNED", "FILTER_AGAINST"):
        for q in ("p50", "p90"): res[n][q]["holdout"] = fm(n, q, HO)
    out.write_text(json.dumps(res, indent=1, default=float)); print("HOLDOUT")
    for k in [*sigs, "CHAMPION_UNFILTERED", "FILTER_ALIGNED", "FILTER_AGAINST"]:
        f = res[k]; print(k, {q: {m: round(v, 4) if isinstance(v, float) else v for m, v in f[q]["holdout"].items()} for q in f})

if __name__ == "__main__": main()
