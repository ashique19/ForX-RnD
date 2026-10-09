"""Score every logged board call (Study #3). Read-only; separate from Stage-2.

Each call with a BUY/SELL raw side is labeled once 8 later CLOSED bars exist:
triple barrier on the live 1h cache with the call's own ATR stop/target
(the board's), entry at the next bar's open, exit at TP/SL (same-bar tie =
SL first, conservative) or the close of bar ``horizon``. Cost = measured
round trip (2 x one-way p50 by default) from data/paper_shadow/measured_costs.csv.

Outputs hit rate / expectancy by confidence bucket and pair. Never writes a
journal, never feeds training.

  python scripts/score_calls.py                      # data/paper_shadow/calls.jsonl
  python scripts/score_calls.py --backfill-signals signals/latest_signals.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forex_lab.call_log import load_calls  # noqa: E402

BUCKETS = [0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 1.0001]
COSTS = ROOT / "data" / "paper_shadow" / "measured_costs.csv"


def pip_size(pair: str) -> float:
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def load_costs(quantile: str = "p50") -> dict[str, float]:
    if not COSTS.exists():
        return {}
    c = pd.read_csv(COSTS)
    col = f"one_way_{quantile}"
    return {str(r.pair).upper(): float(r[col]) for _, r in c.iterrows() if pd.notna(r.get(col))}


def load_ohlcv(pair: str) -> pd.DataFrame | None:
    p = ROOT / "data" / f"{pair.upper()}_1h.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df[~df.index.duplicated(keep="last")].sort_index()


def score_call(
    call: dict[str, Any],
    ohlcv: pd.DataFrame | None,
    *,
    cost_rt_pips: float,
    now_utc: pd.Timestamp,
    horizon: int = 8,
) -> dict[str, Any]:
    """Label one call. status in {scored, pending, unscorable}."""
    side = str(call.get("raw_signal") or "").upper()
    out = {"call_id": call.get("call_id"), "pair": call.get("pair"), "side": side,
           "confidence": call.get("confidence"), "main_eligible": call.get("signal") in {"BUY", "SELL"}}
    if side not in {"BUY", "SELL"}:
        return {**out, "status": "unscorable", "why": "not_directional"}
    stop, target = call.get("stop"), call.get("target")
    if stop is None or target is None:
        return {**out, "status": "unscorable", "why": "no_sl_tp"}
    if ohlcv is None or ohlcv.empty:
        return {**out, "status": "unscorable", "why": "no_bars"}
    sbar = pd.Timestamp(str(call.get("signal_bar_utc")).replace("Z", ""))
    idx = ohlcv.index
    loc = idx.searchsorted(sbar)
    if loc >= len(idx) or idx[loc] != sbar:
        return {**out, "status": "unscorable", "why": "signal_bar_not_in_cache"}
    h = int(call.get("horizon_bars") or horizon)
    path = ohlcv.iloc[loc + 1: loc + 1 + h]
    # bar t+h must be CLOSED: its start + 1h <= now
    if len(path) < h or path.index[-1] + pd.Timedelta(hours=1) > now_utc:
        return {**out, "status": "pending", "why": f"{len(path)}/{h} later bars"}
    pip = pip_size(str(call.get("pair")))
    entry = float(path["Open"].iloc[0])
    sgn = 1.0 if side == "BUY" else -1.0
    # keep the board's barrier DISTANCES, re-anchored on the actual next-open entry
    ref = float(call.get("price") or entry)
    tp_d, sl_d = abs(float(target) - ref), abs(float(stop) - ref)
    tp_px, sl_px = entry + sgn * tp_d, entry - sgn * sl_d
    exit_px, why = float(path["Close"].iloc[-1]), "timeout"
    for _, bar in path.iterrows():
        hi, lo = float(bar["High"]), float(bar["Low"])
        hit_sl = lo <= sl_px if side == "BUY" else hi >= sl_px
        hit_tp = hi >= tp_px if side == "BUY" else lo <= tp_px
        if hit_sl:
            exit_px, why = sl_px, "sl"
            break
        if hit_tp:
            exit_px, why = tp_px, "tp"
            break
    pips = (sgn * (exit_px - entry)) / pip - cost_rt_pips
    r_mult = pips * pip / sl_d if sl_d > 0 else np.nan
    return {**out, "status": "scored", "exit": why, "pips": float(pips), "r": float(r_mult),
            "win": bool(pips > 0), "cost_rt_pips": cost_rt_pips}


def bucket(conf: object) -> str:
    try:
        c = float(conf)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"
    for lo, hi in zip(BUCKETS[:-1], BUCKETS[1:]):
        if lo <= c < hi:
            return f"[{lo:.2f},{min(hi, 1.0):.2f})"
    return "n/a"


def aggregate(scored: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    df = pd.DataFrame([s for s in scored if s.get("status") == "scored"])
    if df.empty:
        return []
    if key == "bucket":
        df["bucket"] = df["confidence"].map(bucket)
    rows = []
    for k, g in df.groupby(key):
        gains, losses = g.pips[g.pips > 0].sum(), -g.pips[g.pips < 0].sum()
        rows.append({key: k, "n": int(len(g)), "hit_rate": float(g.win.mean()),
                     "expectancy_pips": float(g.pips.mean()), "expectancy_r": float(g.r.mean()),
                     "pf": float(gains / losses) if losses > 0 else None})
    return rows


def calls_from_signals_csv(path: Path, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """As-of backfill: turn historical signal rows into calls with the board's
    ATR barrier computed only from bars up to the signal bar."""
    from forex_lab.call_log import call_id
    from forex_lab.config_loader import load_config
    from forex_lab.features import true_range_atr

    cfg = cfg or load_config()
    b = cfg.get("barrier") or {}
    tp_atr, sl_atr = float(b.get("tp_atr", 2.0)), float(b.get("sl_atr", 2.0))
    sig = pd.read_csv(path)
    calls: list[dict[str, Any]] = []
    for pair, g in sig.groupby("pair"):
        ohlcv = load_ohlcv(str(pair))
        if ohlcv is None:
            continue
        atr = true_range_atr(ohlcv, int(cfg.get("atr_period", 14)))  # causal rolling ATR
        for _, r in g.iterrows():
            ts = pd.Timestamp(r["datetime"])
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            if ts not in ohlcv.index or not np.isfinite(atr.get(ts, np.nan)):
                continue
            side = str(r.get("raw_signal") or r.get("signal") or "").upper()
            px, a = float(ohlcv.loc[ts, "Close"]), float(atr.loc[ts])
            sgn = 1.0 if side == "BUY" else -1.0
            sbar = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            calls.append({"call_id": call_id(str(pair), sbar, side), "pair": str(pair).upper(), "signal_bar_utc": sbar,
                          "raw_signal": side, "signal": str(r.get("signal") or "").upper(),
                          "confidence": float(r["confidence"]) if pd.notna(r.get("confidence")) else None,
                          "price": px, "stop": px - sgn * sl_atr * a if side in {"BUY", "SELL"} else None,
                          "target": px + sgn * tp_atr * a if side in {"BUY", "SELL"} else None,
                          "horizon_bars": int(cfg.get("horizon") or 8)})
    return calls


def score_all(calls: list[dict[str, Any]], *, quantile: str = "p50", now_utc: pd.Timestamp | None = None) -> dict[str, Any]:
    now_utc = now_utc or pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    costs = load_costs(quantile)
    cache: dict[str, pd.DataFrame | None] = {}
    scored = []
    for c in calls:
        pair = str(c.get("pair") or "").upper()
        if pair not in cache:
            cache[pair] = load_ohlcv(pair)
        one_way = costs.get(pair)
        if one_way is None:
            scored.append({"call_id": c.get("call_id"), "pair": pair, "status": "unscorable", "why": "no_measured_cost"})
            continue
        scored.append(score_call(c, cache[pair], cost_rt_pips=2.0 * one_way, now_utc=now_utc))
    st = pd.Series([s["status"] for s in scored]).value_counts().to_dict() if scored else {}
    return {"n_calls": len(calls), "status_counts": st, "cost_quantile": quantile,
            "by_bucket": aggregate(scored, "bucket"), "by_pair": aggregate(scored, "pair"),
            "main_eligible_vs_not": aggregate(scored, "main_eligible"), "scored": scored}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backfill-signals", type=Path, default=None, help="score a historical signals CSV instead (as-of history)")
    ap.add_argument("--cost", choices=["p50", "p90"], default="p50")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.backfill_signals:
        calls = calls_from_signals_csv(a.backfill_signals)
        label = f"HISTORY backfill (as-of) from {a.backfill_signals}"
    else:
        calls = load_calls()
        label = "live board call log"
    res = score_all(calls, quantile=a.cost)
    res["label"] = label
    if a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "scored"}, indent=1, default=float))
        return 0
    print(f"{label}: {res['n_calls']} calls; {res['status_counts']}; cost={a.cost} measured round trip")
    for key in ("by_bucket", "by_pair", "main_eligible_vs_not"):
        print(f"-- {key}")
        for r in res[key]:
            print("  " + "  ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}" for k, v in r.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
