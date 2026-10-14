"""Download CFTC Legacy Futures-Only COT annual files (2014-2026) and extract the 7 FX contracts.
Writes data/research/cot/raw/deacot{Y}.zip and data/research/cot/cot_fx_legacy.csv (gitignored data)."""
import io, sys, zipfile, urllib.request
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT/"data"/"research"/"cot"/"raw"; RAW.mkdir(parents=True, exist_ok=True)
CODES = {"099741":"EUR","096742":"GBP","097741":"JPY","232741":"AUD","112741":"NZD","090741":"CAD","092741":"CHF"}
frames = []
for y in range(2014, 2027):
    f = RAW/f"deacot{y}.zip"
    if not f.exists() or y >= 2026:
        import requests  # certifi CA bundle (the Windows python store lacks the CFTC chain)
        r = requests.get(f"https://www.cftc.gov/files/dea/history/deacot{y}.zip", headers={"User-Agent": "Mozilla/5.0 forex-lab research"}, timeout=120)
        r.raise_for_status(); f.write_bytes(r.content)
    z = zipfile.ZipFile(f); name = z.namelist()[0]
    d = pd.read_csv(io.BytesIO(z.read(name)), dtype={"CFTC Contract Market Code": str}, low_memory=False)
    d.columns = [c.strip() for c in d.columns]
    d["code"] = d["CFTC Contract Market Code"].str.strip()
    d = d[d.code.isin(CODES)]
    frames.append(pd.DataFrame({"asof": pd.to_datetime(d["As of Date in Form YYYY-MM-DD"]), "ccy": d.code.map(CODES),
        "oi": d["Open Interest (All)"], "nc_long": d["Noncommercial Positions-Long (All)"], "nc_short": d["Noncommercial Positions-Short (All)"]}))
    print(y, len(frames[-1]), flush=True)
out = pd.concat(frames).drop_duplicates(["asof","ccy"]).sort_values(["ccy","asof"])
out["net"] = out.nc_long - out.nc_short
out.to_csv(ROOT/"data"/"research"/"cot"/"cot_fx_legacy.csv", index=False)
print(out.groupby("ccy").asof.agg(["min","max","count"]))
