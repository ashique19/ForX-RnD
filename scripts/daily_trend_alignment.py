"""READ-ONLY small-sample what-if: does the DAILY trend (higher timeframe) separate good hourly trades?

Daily bars are resampled from finished 1h bars in data/<PAIR>_1h.csv (no synthesized prices). The FX
trading day closes at 17:00 New York (the standard rollover). Only daily bars COMPLETED before the
entry are used (no lookahead). Days with fewer than 12 hourly bars are dropped.

Pre-stated trend definitions (fixed before looking at results):
  A  close_vs_sma20 : last completed daily close vs SMA20 of daily closes   (above = up)
  B  sma20_vs_sma50 : SMA20 vs SMA50 of daily closes                        (above = up)
  C  prior_day_body : last completed day's close vs its open                (close > open = up)
  ALL               : A, B and C all agree
A trade is "aligned" when BUY meets an up trend or SELL meets a down trend, otherwise "counter"
(an exactly flat reading counts as counter). No journal, YAML, MT5 or model writes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from forex_lab import explore_book as eb  # noqa: E402

MIN_HOURS_PER_DAY = 12
DEFS = ("A_close_vs_sma20", "B_sma20_vs_sma50", "C_prior_day_body", "ALL_agree")


def daily_from_hourly(h1: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC with the FX day ending 17:00 America/New_York. Index = day end (UTC, naive)."""
    idx = pd.DatetimeIndex(h1.index)
    ny = idx.tz_localize("UTC").tz_convert("America/New_York")
    key = (ny + pd.Timedelta(hours=7)).floor("D")  # trading date: 17:00 NY rolls to the next date
    g = h1.groupby(key.tz_localize(None))
    d = g.agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
    d["hours"] = g.size()
    # trading date D covers 17:00 NY on D-1 .. 17:00 NY on D, so the day ends at 17:00 NY on D
    ends = []
    for td in d.index:
        end_ny = pd.Timestamp(td.year, td.month, td.day, 17, tz="America/New_York")
        ends.append(end_ny.tz_convert("UTC").tz_localize(None))
    d["end_utc"] = ends
    return d[d["hours"] >= MIN_HOURS_PER_DAY]


def trend_at(daily: pd.DataFrame, entry_utc: pd.Timestamp) -> dict:
    """Trend readings from daily bars completed at or before ``entry_utc`` (+1 up, -1 down, 0 flat)."""
    done = daily[daily["end_utc"] <= entry_utc]
    if len(done) < 50:
        return {k: None for k in DEFS} | {"last_day_end_utc": None}
    c = done["Close"]
    sma20, sma50 = c.rolling(20).mean().iloc[-1], c.rolling(50).mean().iloc[-1]
    sgn = lambda x: 1 if x > 0 else (-1 if x < 0 else 0)  # noqa: E731
    a = sgn(c.iloc[-1] - sma20)
    b = sgn(sma20 - sma50)
    cc = sgn(c.iloc[-1] - done["Open"].iloc[-1])
    allv = a if a == b == cc else 0
    return {"A_close_vs_sma20": a, "B_sma20_vs_sma50": b, "C_prior_day_body": cc, "ALL_agree": allv,
            "last_day_end_utc": str(done["end_utc"].iloc[-1])}


def aligned(side: str, trend: int | None) -> str | None:
    if trend is None:
        return None
    want = 1 if str(side).upper() == "BUY" else -1
    return "aligned" if trend == want else "counter"


def stats(rs: list[float]) -> dict:
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    return {"n": len(rs), "win_rate": round(len(wins) / len(rs), 3) if rs else None,
            "total_r": round(sum(rs), 3), "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "pf": round(sum(wins) / sum(losses), 3) if losses else None}


def tag(trades: list[dict], dailies: dict) -> list[dict]:
    out = []
    for t in trades:
        tr = trend_at(dailies[t["pair"]], pd.Timestamp(str(t["t0"]).replace("Z", "")))
        out.append({**t, **{f"trend_{k}": tr[k] for k in DEFS}, **{k: aligned(t["side"], tr[k]) for k in DEFS},
                    "last_day_end_utc": tr["last_day_end_utc"]})
    return out


def group_table(rows: list[dict]) -> dict:
    res = {}
    for k in DEFS:
        g = {}
        for lab in ("aligned", "counter"):
            g[lab] = stats([r["pnl_r"] for r in rows if r.get(k) == lab and r.get("pnl_r") is not None])
        g["untagged"] = sum(1 for r in rows if r.get(k) is None)
        res[k] = g
    return res


def _hourly(pair: str, now: pd.Timestamp) -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data" / f"{pair}_1h.csv", index_col=0, parse_dates=True)
    df.index = pd.DatetimeIndex(df.index)
    df = df[["Open", "High", "Low", "Close"]].astype(float).sort_index()
    return df[(df.index + pd.Timedelta(hours=1)) <= now]


def report(now: pd.Timestamp | None = None) -> dict:
    import stop_payoff_analysis as spa  # noqa: PLC0415
    from forex_lab import paper_shadow as ps  # noqa: PLC0415

    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    ev = eb.load()
    ex = [{"position_id": t["position_id"], "pair": t["pair"], "side": t["side"], "t0": t["t0"], "pnl_r": t["pnl_r"],
           "verdict": t["verdict"]} for t in spa.explore_trades(ev)]
    mev = ps.load_events()
    try:
        import news_tag_report as ntr  # noqa: PLC0415

        rmap = {r["position_id"]: r for r in ntr.main_rows(mev)}
    except Exception:  # noqa: BLE001
        rmap = {}
    refs = ps.excluded_refs(mev)
    main = [{"position_id": str(o["position_id"]), "pair": o["pair"], "side": o["side"], "t0": o["ts_utc"],
             "pnl_r": (rmap.get(str(o["position_id"])) or {}).get("pnl_r"), "excluded": ps.is_excluded(o, refs)}
            for o in mev if o.get("event") == "open" and o.get("conf") is not None]
    pairs = sorted({t["pair"] for t in ex + main})
    dailies = {p: daily_from_hourly(_hourly(p, now)) for p in pairs}
    ex_t = tag(ex, dailies)
    cb_t = [t for t in ex_t if t["verdict"] != "would_not_fire"]
    main_t = tag(main, dailies)
    return {"generated_utc": str(now), "SMALL_SAMPLE_WHAT_IF_NOT_A_GATE": True,
            "explore_33": group_table(ex_t), "closed_bar_valid": group_table(cb_t), "closed_bar_valid_n": len(cb_t),
            "main_fills": [{k: m.get(k) for k in ("pair", "side", "t0", "pnl_r", "excluded", *DEFS)} for m in main_t],
            "explore_trades": [{k: t.get(k) for k in ("pair", "side", "t0", "pnl_r", "verdict", *DEFS)} for t in ex_t]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    print(json.dumps(report(), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())