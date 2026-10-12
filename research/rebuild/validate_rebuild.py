"""Validate staged HistData rebuild (raw 1h and assembled final/) -> data/history_rebuild/validation.json.
Pass: wickless < 3% and median |Open-prevClose| < 0.3 pip (whole file and each era). Never writes data/history."""
import json, glob, os, pathlib
import pandas as pd
WL_MAX, GAP_MAX = 0.03, 0.3
def stats(df, pair):
    pip = 0.01 if pair.endswith("JPY") else 0.0001
    o,h,l,c = df["Open"],df["High"],df["Low"],df["Close"]
    mx = pd.concat([o,c],axis=1).max(axis=1); mn = pd.concat([o,c],axis=1).min(axis=1)
    wl = float(((h <= mx+1e-12) & (l >= mn-1e-12)).mean())
    gap = float(((o - c.shift()).abs()/pip).median())
    dt = df.index.to_series().diff().dt.total_seconds()/3600
    gaps = int(((dt > 1) & ~((dt >= 40) & (dt <= 72))).sum())
    big = int((dt > 72).sum())
    return {"bars": len(df), "start": str(df.index.min()), "end": str(df.index.max()),
            "wickless": round(wl,4), "med_gap_pips": round(gap,3), "gaps": gaps, "gaps_gt72h": big}
ST = pathlib.Path("data/history_rebuild"); out = {}
for kind, d in (("raw1h", ST), ("final", ST/"final")):
    for f in sorted(glob.glob(str(d/"*_1h.csv"))):
        pair = os.path.basename(f).split("_")[0]
        df = pd.read_csv(f, parse_dates=["Datetime"], index_col="Datetime")
        r = {"all": stats(df, pair)}
        for era, sub in (("2015-21", df[:"2021-12-31"]), ("2022+", df["2022-01-01":])):
            if len(sub) > 100: r[era] = stats(sub, pair)
        r["pass"] = all(v["wickless"] < WL_MAX and v["med_gap_pips"] < GAP_MAX for k,v in r.items() if isinstance(v, dict))
        out.setdefault(pair, {})[kind] = r
        print(f"{kind:6} {pair} " + " | ".join(f"{k}: n={v['bars']} wl={v['wickless']:.3f} gap={v['med_gap_pips']:.2f} gaps={v['gaps']}" for k,v in r.items() if isinstance(v, dict)) + f" PASS={r['pass']}")
(ST/"validation.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
