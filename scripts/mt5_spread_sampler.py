"""Sample real MT5 (OANDA demo) bid/ask spreads every 5 minutes -> data/costs/mt5_spreads.csv.
Read-only: quotes and ticks only, never an order. Each sample also records the 5-minute tick spread median/p90.
Long-running; started detached by scripts/ensure_mt5_sampler.ps1 (logon + every 15 min task).
Columns: ts_utc, hour_utc, weekday, pair, symbol, bid, ask, spread_pips, tick_n, tick_spread_p50, tick_spread_p90

Health (2026-10-19): a sample with 0 valid quotes during FX hours is a FAILURE (stale MT5 session), not a
success: the sampler reconnects, and after MAX_BAD failures in a row it exits so the guard starts a fresh one.
It writes data/costs/mt5_sampler.heartbeat on every healthy sample (and while idling over the FX weekend);
the guard restarts a sampler whose heartbeat is stale."""
from __future__ import annotations
import csv, sys, time
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from forex_lab import mt5_feed as F

OUT = ROOT / "data" / "costs" / "mt5_spreads.csv"; LOG = ROOT / "data" / "costs" / "mt5_sampler.log"
HEART = ROOT / "data" / "costs" / "mt5_sampler.heartbeat"
COLS = ["ts_utc","hour_utc","weekday","pair","symbol","bid","ask","spread_pips","tick_n","tick_spread_p50","tick_spread_p90"]
INTERVAL = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 300
MAX_BAD = 4  # consecutive failed samples (~20 min) before exiting for a clean restart


def log(msg):
    with LOG.open("a", encoding="utf-8") as f: f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC ") + msg + "\n")


def beat(state: str):
    HEART.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + " " + state + "\n", encoding="utf-8")


def fx_open(now: datetime) -> bool:
    """FX week: Sun 17:00 -> Fri 17:00 America/New_York (shared DST-aware helper)."""
    from forex_lab.freshness import fx_session_open

    return fx_session_open(now)


def sample(syms):
    q = F.quotes(syms); now = datetime.now(timezone.utc); rows = []
    for _, r in q[q.ok].iterrows():
        if not (r.bid and r.ask and r.bid > 0 and r.ask > 0):
            continue
        pip = 0.01 if r.pair.endswith("JPY") else 0.0001
        try:
            t = F.recent_ticks(r.symbol, 5); s = ((t.ask - t.bid) / pip) if len(t) else None
        except Exception:
            s = None
        rows.append([now.strftime("%Y-%m-%d %H:%M:%S"), now.hour, now.weekday(), r.pair, r.symbol, r.bid, r.ask, r.spread_pips,
                     0 if s is None else len(s), "" if s is None or not len(s) else round(float(s.median()), 3),
                     "" if s is None or not len(s) else round(float(s.quantile(0.9)), 3)])
    if rows:
        new = not OUT.exists()
        with OUT.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new: w.writerow(COLS)
            w.writerows(rows)
    return len(rows)


def _diag() -> str:
    try:
        mt5 = F._mt5(); ti = mt5.terminal_info()
        return f"last_error={mt5.last_error()} terminal_connected={getattr(ti, 'connected', None)}"
    except Exception as e:  # noqa: BLE001
        return f"diag failed {e!r}"


def main():
    log(f"start interval={INTERVAL}s")
    syms = None; bad = 0; idle_logged = None
    while True:
        now = datetime.now(timezone.utc)
        if not fx_open(now):
            if idle_logged != now.strftime("%Y-%m-%d %H"):
                idle_logged = now.strftime("%Y-%m-%d %H"); log("FX closed; idle")
            beat("fx_closed"); time.sleep(INTERVAL); continue
        try:
            if syms is None:
                acc = F.connect(require_demo=True); syms = F.resolve_symbols()
                log(f"connected demo={acc['is_demo']} symbols={syms}")
            n = sample(syms)
            if n == 0:
                raise RuntimeError("0 valid quotes during FX hours (stale MT5 session) " + _diag())
            bad = 0; beat(f"ok {n}"); log(f"sampled {n}")
        except Exception as e:
            bad += 1; log(f"error[{bad}/{MAX_BAD}] " + repr(e)); syms = None
            try: F.shutdown()
            except Exception: pass
            if bad >= MAX_BAD:
                log("exiting so the guard starts a fresh sampler"); sys.exit(3)
        time.sleep(INTERVAL)


if __name__ == "__main__": main()
