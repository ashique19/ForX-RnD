"""Merge Jetta hole-fill into EURUSD H1 history with Dukascopy-unit volumes.

Jetta remesh Volume is ~2.812x Dukascopy tick counts (median same-ts ratio on
bak_pre_jetta overlap n=7110, measured 2026-09-30). Divide fill Volume by
JETTA_TO_DUKA_VOLUME_FACTOR before merge so history stays in Dukascopy units.
See _JETTA_VOLUME_SCALE_20260930.txt.
"""
from pathlib import Path
import numpy as np
import pandas as pd

# Median (Jetta Volume / Dukascopy tick Volume) on same-timestamp overlap.
JETTA_TO_DUKA_VOLUME_FACTOR = 2.812045180934712

hist_path = Path(r"C:\AI\forex-lab\data\history\EURUSD_1h.csv")
fill_path = Path(r"C:\AI\forex-lab\data\history\_EURUSD_1h_hole_fill_jetta.csv")

hist = pd.read_csv(hist_path)
fill = pd.read_csv(fill_path)
print("hist", len(hist), hist["Datetime"].iloc[0], "->", hist["Datetime"].iloc[-1])
print("fill", len(fill), fill["Datetime"].iloc[0], "->", fill["Datetime"].iloc[-1])

dt = pd.to_datetime(hist["Datetime"])
gaps = dt.diff().dt.total_seconds() / 3600
big = gaps[gaps > 24]
print("gaps>24h before", len(big), "max_h", float(big.max()) if len(big) else 0)

for df in (hist, fill):
    for c in df.columns:
        if c != "Datetime":
            df[c] = pd.to_numeric(df[c], errors="coerce")

# Rescale Jetta fill Volume -> Dukascopy tick units (integer counts).
fill_vol = pd.to_numeric(fill["Volume"], errors="coerce").fillna(0.0)
fill["Volume"] = np.round(fill_vol / JETTA_TO_DUKA_VOLUME_FACTOR).clip(lower=0.0)
print(
    "fill Volume rescaled /",
    JETTA_TO_DUKA_VOLUME_FACTOR,
    "mean",
    float(fill["Volume"].mean()),
)

merged = pd.concat([hist, fill], ignore_index=True)
merged["Datetime"] = pd.to_datetime(merged["Datetime"])
merged = merged.sort_values("Datetime")
merged = merged.drop_duplicates(subset=["Datetime"], keep="last")
merged["Datetime"] = merged["Datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")

dt2 = pd.to_datetime(merged["Datetime"])
gaps2 = dt2.diff().dt.total_seconds() / 3600
big2 = gaps2[gaps2 > 24]
print("merged", len(merged), merged["Datetime"].iloc[0], "->", merged["Datetime"].iloc[-1])
print("gaps>24h after", len(big2), "max_h", float(big2.max()) if len(big2) else 0)
if len(big2):
    for i in big2.nlargest(5).index:
        print(" gap", merged["Datetime"].iloc[i - 1], "->", merged["Datetime"].iloc[i], "hours", float(gaps2.loc[i]))

merged.to_csv(hist_path, index=False)
print("wrote", hist_path, "bytes", hist_path.stat().st_size)
print("backup", r"C:\AI\forex-lab\data\history\EURUSD_1h.csv.bak_pre_jetta_20260930_010124")
