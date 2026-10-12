"""Assemble staged HistData bid bars into repo history format (data/history_rebuild/final/).
- Open/High/Low/Close and Bid* = HistData BID (NY time -> UTC already applied).
- Ask* and Spread left empty: no ask in HistData. Spreads come from data/measured_costs.csv.
- Volume carried from the old file on matching timestamps (Jetta volume was not delta-coded);
  HistData ASCII volume is always 0. Bars with no old volume get 0 (as HistData reports).
- EURUSD: rows before 2022-01-01 kept from the old (clean Dukascopy) file.
- Tail after the HistData end: appended from the desk refresh CSV data/<PAIR>_1h.csv (yfinance) only.
Never writes data/history. Usage: python research/edge_check/assemble_rebuild.py PAIR [...]"""
import sys, json, pathlib
import pandas as pd
from forex_lab.history import load_history_csv
ST = pathlib.Path("data/history_rebuild"); FIN = ST / "final"; FIN.mkdir(exist_ok=True)
COLS = ["Open","High","Low","Close","Volume","BidOpen","BidHigh","BidLow","BidClose","AskOpen","AskHigh","AskLow","AskClose","Spread"]

def build(pair):
    old = load_history_csv(pathlib.Path(f"data/history/{pair}_1h.csv"))
    new = load_history_csv(ST / f"{pair}_1h.csv")[["Open","High","Low","Close"]]
    for c in ["Open","High","Low","Close"]: new["Bid"+c] = new[c]
    new["Volume"] = old["Volume"].reindex(new.index).fillna(0.0)
    tail_src = "none"
    desk = pathlib.Path(f"data/{pair}_1h.csv")
    if desk.exists():
        d = load_history_csv(desk); d = d[d.index > new.index.max()][["Open","High","Low","Close","Volume"]]
        if len(d): new = pd.concat([new, d]); tail_src = f"yfinance desk csv {len(d)} bars"
    if pair == "EURUSD":
        keep = old[old.index < "2022-01-01"]
        new = pd.concat([keep, new[new.index >= "2022-01-01"]])
    out = new.reindex(columns=COLS).sort_index(); out = out[~out.index.duplicated(keep="last")]
    out.index.name = "Datetime"; out.to_csv(FIN / f"{pair}_1h.csv")
    meta = {"pair": pair, "interval": "1h", "source": "histdata_bid" + ("+dukascopy_pre2022" if pair=="EURUSD" else ""),
            "rows": len(out), "start": str(out.index.min()), "end": str(out.index.max()), "bid_ask": pair=="EURUSD" and False,
            "tail": tail_src, "note": "rebuild 2026-10-11: Jetta decode corruption replaced; no ask in bars, spreads from data/measured_costs.csv"}
    (FIN / f"{pair}_1h.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(pair, len(out), out.index.min(), out.index.max(), tail_src)

for p in sys.argv[1:]: build(p.upper())
