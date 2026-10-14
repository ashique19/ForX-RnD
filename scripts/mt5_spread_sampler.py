"""Sample real MT5 (OANDA demo) bid/ask spreads every 5 minutes -> data/costs/mt5_spreads.csv.
Read-only: no orders. Each sample also records the 5-minute tick spread median/p90. Long-running; start detached.
Columns: ts_utc, hour_utc, weekday, pair, symbol, bid, ask, spread_pips, tick_n, tick_spread_p50, tick_spread_p90"""
from __future__ import annotations
import csv, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from forex_lab import mt5_feed as F

OUT = ROOT / "data" / "costs" / "mt5_spreads.csv"; LOG = ROOT / "data" / "costs" / "mt5_sampler.log"
COLS = ["ts_utc","hour_utc","weekday","pair","symbol","bid","ask","spread_pips","tick_n","tick_spread_p50","tick_spread_p90"]
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 else 300

def log(msg):
    with LOG.open("a", encoding="utf-8") as f: f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC ") + msg + "\n")

def sample(syms):
    q = F.quotes(syms); now = datetime.now(timezone.utc); rows = []
    for _, r in q[q.ok].iterrows():
        pip = 0.01 if r.pair.endswith("JPY") else 0.0001
        try:
            t = F.recent_ticks(r.symbol, 5); s = ((t.ask - t.bid) / pip) if len(t) else None
        except Exception:
            s = None
        rows.append([now.strftime("%Y-%m-%d %H:%M:%S"), now.hour, now.weekday(), r.pair, r.symbol, r.bid, r.ask, r.spread_pips,
                     0 if s is None else len(s), "" if s is None or not len(s) else round(float(s.median()), 3),
                     "" if s is None or not len(s) else round(float(s.quantile(0.9)), 3)])
    new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new: w.writerow(COLS)
        w.writerows(rows)
    return len(rows)

def main():
    log(f"start interval={INTERVAL}s")
    syms = None
    while True:
        try:
            if syms is None:
                acc = F.connect(require_demo=True); syms = F.resolve_symbols()
                log(f"connected demo={acc['is_demo']} symbols={syms}")
            n = sample(syms); log(f"sampled {n}")
        except Exception as e:
            log("error " + repr(e)); syms = None
            try: F.shutdown()
            except Exception: pass
        time.sleep(INTERVAL)

if __name__ == "__main__": main()
