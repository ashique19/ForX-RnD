"""Per-pair / per-year bar integrity table (wickless share, median |O-prevC| pips, gaps).
Usage: python research/edge_check/validate_bars.py [history_dir]"""
import sys, glob, os
import pandas as pd
from forex_lab.data_integrity import bar_integrity_stats

def stats(df, pair):
    pip = 0.01 if pair.endswith("JPY") else 0.0001
    o,h,l,c = df["Open"],df["High"],df["Low"],df["Close"]
    wl = ((h <= pd.concat([o,c],axis=1).max(axis=1)+1e-12) & (l >= pd.concat([o,c],axis=1).min(axis=1)-1e-12)).mean()
    gap = ((o - c.shift()).abs()/pip).median()
    dt = df.index.to_series().diff().dt.total_seconds()/3600
    wk = df.index.to_series().dt.dayofweek
    gaps = int(((dt > 1) & ~((dt >= 40) & (dt <= 72))).sum())
    return len(df), round(float(wl),3), round(float(gap),2), gaps

d = sys.argv[1] if len(sys.argv) > 1 else "data/history"
rows = []
for f in sorted(glob.glob(os.path.join(d, "*_1h.csv"))):
    pair = os.path.basename(f).split("_")[0]
    if pair.startswith("BTC"): continue
    df = pd.read_csv(f, parse_dates=["Datetime"], index_col="Datetime")
    for era, sub in (("2015-21", df[:"2021-12-31"]), ("2022+", df["2022-01-01":])):
        if len(sub): rows.append((pair, era) + stats(sub, pair))
out = pd.DataFrame(rows, columns=["pair","era","bars","wickless","med_gap_pips","gaps"])
print(out.to_string(index=False))
