"""NZDUSD tip remesh: Jetta body thru Aug + live mid Sep tip with synth BA."""
from __future__ import annotations
import json
import shutil
from pathlib import Path
import pandas as pd
from forex_lab.history import SOURCE_JETTA, _save_bars, history_path, load_history_csv, resample_bars

ROOT = Path(r"C:\AI\forex-lab")
PAIR = "NZDUSD"
CUT = pd.Timestamp("2026-08-31 23:00:00")

hist_path = history_path(PAIR, "1h", {})
bak = hist_path.with_name("NZDUSD_1h.csv.bak_pre_tip_20261001")
if not bak.exists():
    shutil.copy2(hist_path, bak)
    print("backup", bak)

hist = load_history_csv(hist_path)
print("before", len(hist), hist.index.min(), "->", hist.index.max())

# Aug half-spread mean from Jetta BA
aug = hist.loc[(hist.index >= "2026-08-01") & (hist.index <= CUT)]
half = ((aug["AskClose"] - aug["BidClose"]) / 2.0).dropna()
half_spread = float(half.mean())
print("aug_n", len(aug), "half_spread", half_spread, "half_n", len(half))

live = pd.read_csv(ROOT / "data" / "NZDUSD_1h.csv", parse_dates=["Datetime"])
live = live.set_index("Datetime").sort_index()
live.index = pd.to_datetime(live.index).tz_localize(None)
live = live[~live.index.duplicated(keep="last")]
tip = live.loc[live.index > CUT].copy()
print("live tip candidates", len(tip), tip.index.min() if len(tip) else None, "->", tip.index.max() if len(tip) else None)

# synth BA from mid OHLC
for side, sign in (("Bid", -1), ("Ask", 1)):
    tip[f"{side}Open"] = tip["Open"] + sign * half_spread
    tip[f"{side}High"] = tip["High"] + sign * half_spread
    tip[f"{side}Low"] = tip["Low"] + sign * half_spread
    tip[f"{side}Close"] = tip["Close"] + sign * half_spread
tip["Spread"] = tip["AskClose"] - tip["BidClose"]

# keep body thru CUT; tip after
body = hist.loc[hist.index <= CUT].copy()
merged = pd.concat([body, tip], axis=0)
merged = merged[~merged.index.duplicated(keep="last")].sort_index()
merged.index.name = "Datetime"

# QA
def hl_bad(prefix=""):
    h, l = f"{prefix}High", f"{prefix}Low"
    if h not in merged.columns:
        return 0
    return int((merged[h] < merged[l]).sum())

neg_spread = int((merged["AskClose"] < merged["BidClose"]).sum()) if "AskClose" in merged.columns else -1
print("qa mid_hl", hl_bad(), "bid_hl", hl_bad("Bid"), "ask_hl", hl_bad("Ask"), "neg_spread", neg_spread)

_save_bars(PAIR, "1h", merged, SOURCE_JETTA, {})
daily = resample_bars(merged, "1d")
_save_bars(PAIR, "1d", daily, SOURCE_JETTA, {})
print("1h", len(merged), merged.index.min(), "->", merged.index.max(), "n_tip", len(tip))
print("1d", len(daily), daily.index.min(), "->", daily.index.max())

# gaps
idx = merged.index.sort_values()
deltas = idx.to_series().diff().dt.total_seconds() / 3600.0
big = deltas[deltas > 72]
print("gaps>72h", len(big))
for i, hours in big.items():
    prev = idx[idx.get_loc(i) - 1]
    print(f"  {hours:.0f}h {prev} -> {i}")

# tip_note into meta
meta_path = hist_path.with_suffix(".meta.json")
meta = json.loads(meta_path.read_text(encoding="utf-8"))
meta["tip_note"] = (
    f"Jetta /2026/9 HTTP 400; tip after {CUT} = live mid + synth BA "
    f"half_spread={half_spread:.10f} (Aug mean); n_tip={len(tip)}; hole_fill_20261001"
)
meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
print("meta", json.dumps(meta, indent=2))

# vol sanity on body
vol = pd.to_numeric(body["Volume"], errors="coerce")
print("body_vol mean", float(vol.mean()), "median", float(vol.median()), "zero", int((vol.fillna(0) <= 0).sum()), "non_int", int((vol != vol.round()).sum()))
