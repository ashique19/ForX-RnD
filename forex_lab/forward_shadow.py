"""Forward paper shadow of the frozen low-frequency candidates (research/forward_shadow/FROZEN_RULES_20261022.md).

Paper only: no MT5 orders, no desk trading endpoints, never touches the main/explore journals or yaml.
State is event-sourced from its own journal; every row has a unique ``key`` so reruns append nothing twice.
Pure logic lives in ``step``; IO (history, tip cache, BIS rates, read-only MT5 swaps) is in
scripts/forward_shadow_lowfreq.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("lowfreq_20261022_frozen", ROOT / "research" / "lowfreq" / "lowfreq_20261022.py")
lf = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("lowfreq_20261022_frozen", lf)
_spec.loader.exec_module(lf)

PAIRS = list(lf.PAIRS)
RULES = ("MOM120", "CARRY3")
L_MOM = 120
TOP_K = 3
MIN_ABS_DIFF = 0.25
WAIT_DAYS = 3
SCHEMA = "forward_shadow_v1"
# Frozen 2026-10-22 from data/paper_shadow/measured_costs.csv (2 x one-way p90), pips.
RT_PIPS = {"EURUSD": 1.4, "USDJPY": 2.4, "GBPUSD": 2.8, "AUDUSD": 2.6, "NZDUSD": 3.2, "USDCAD": 3.6, "USDCHF": 3.0,
           "EURJPY": 4.0, "EURGBP": 2.8, "GBPJPY": 7.6}


def journal_path() -> Path:
    return Path(os.environ.get("FORX_FORWARD_JOURNAL") or (ROOT / "data" / "forward_shadow" / "forward_journal.jsonl"))


def load(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or journal_path()
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def append(rows: list[dict[str, Any]], path: Path | None = None) -> int:
    p = path or journal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    keys = {r.get("key") for r in load(p)}
    new = [r for r in rows if r.get("key") not in keys]
    with p.open("a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps({"schema": SCHEMA, **r}, default=str) + "\n")
    return len(new)


def _iso(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t


def _pip(pair: str) -> float:
    return 0.01 if pair.endswith("JPY") else 0.0001


# ------------------------------------------------------------------ signals (frozen)
def mom_target(daily: pd.DataFrame, D) -> int | None:
    c = daily["Close"].loc[:D]
    if len(c) <= L_MOM:
        return None
    v = float(c.iloc[-1] / c.iloc[-1 - L_MOM] - 1.0)
    return int(np.sign(v))


def carry_selection(rates: pd.DataFrame, D) -> dict[str, int]:
    diffs = {p: lf.rate_diff(rates, p, D) for p in PAIRS}
    elig = {p: d for p, d in diffs.items() if np.isfinite(d) and abs(d) >= MIN_ABS_DIFF}
    top = sorted(elig, key=lambda p: (-abs(elig[p]), PAIRS.index(p)))[:TOP_K]
    return {p: (int(np.sign(elig[p])) if p in top else 0) for p in PAIRS}


def risk_at(daily: pd.DataFrame, D) -> float | None:
    a = lf.true_range_atr(daily.loc[:D], 14)
    v = float(a.iloc[-1]) if len(a) else float("nan")
    return 2.0 * v if np.isfinite(v) and v > 0 else None


def swap_increment(pair: str, side: int, t0, t1, swaps: dict | None) -> tuple[float, int, int]:
    """(return fraction, rollovers, nights) for weekday 21:00 UTC rollovers in (t0, t1]; Wednesday x3."""
    t0, t1 = _ts(t0), _ts(t1)
    days = pd.date_range(t0.normalize(), t1.normalize(), freq="D") + pd.Timedelta(hours=21)
    ro = days[(days > t0) & (days <= t1) & (days.dayofweek < 5)]
    nights = int(np.where(ro.dayofweek == 2, 3, 1).sum())
    if not len(ro) or not swaps or pair not in swaps:
        return 0.0, int(len(ro)), nights
    s = float(swaps[pair]["swap_long"] if side == 1 else swaps[pair]["swap_short"])
    return nights * s / 100.0 / 360.0, int(len(ro)), nights


def _r(pair, side, entry, px, acc, risk) -> float:
    net = side * (px / entry - 1.0) - RT_PIPS[pair] * _pip(pair) / entry + acc
    return float(net * entry / risk)


# ------------------------------------------------------------------ state
def _state(journal: list[dict], rule: str):
    pos, pending, last_day = {}, {}, None
    for r in journal:
        if r.get("rule") != rule:
            continue
        ev, p = r.get("event"), r.get("pair")
        if ev == "decision":
            pending[p] = r
        elif ev == "fill_exit":
            pos.pop(p, None)
        elif ev == "fill_entry":
            pos[p] = {"side": r["side"], "entry": r["entry"], "entry_time": r["fill_time_utc"], "risk": r["risk"],
                      "acc": 0.0, "last_t": r["fill_time_utc"]}
            pending.pop(p, None)
        elif ev == "fill_done":
            pending.pop(p, None)
        elif ev == "mark" and p in pos:
            pos[p]["acc"] = r["swap_acc_frac"]; pos[p]["last_t"] = r["bar_close_utc"]
        elif ev == "day":
            last_day = r["bar_close_utc"]
    return pos, pending, last_day


def init_row(daily: dict[str, pd.DataFrame], rates, now_utc) -> dict:
    common = sorted(set.intersection(*[set(d.index) for d in daily.values()]))
    D0 = common[-1]
    mom = {p: mom_target(daily[p], D0) for p in PAIRS}
    fridays = [d for d in common if d.dayofweek == 4]
    carry = carry_selection(rates, fridays[-1]) if (rates is not None and fridays) else None
    return {"event": "init", "key": "init", "init_utc": _iso(now_utc), "day0_bar_close_utc": _iso(D0),
            "day0_mom120_targets": mom, "day0_carry3_last_friday_utc": _iso(fridays[-1]) if fridays else None,
            "day0_carry3_selection": carry, "last_bar_close_utc": {p: _iso(d.index[-1]) for p, d in daily.items()},
            "note": "day 0 records state only; first forward decision = first daily close after init_utc"}


def step(journal: list[dict], daily: dict[str, pd.DataFrame], rates, swaps: dict | None, swap_src: str, now_utc) -> list[dict]:
    """New journal rows (deterministic given inputs; existing keys are skipped by append)."""
    init = next((r for r in journal if r.get("event") == "init"), None)
    if init is None:
        return [init_row(daily, rates, now_utc)]
    t_init = _ts(init["init_utc"])
    run = _iso(now_utc)
    out: list[dict] = []
    latest = max(d.index.max() for d in daily.values())
    for rule in RULES:
        pos, pending, last_day = _state(journal + out, rule)
        floor_t = max(t_init, _ts(last_day)) if last_day else t_init
        dates = sorted({t for d in daily.values() for t in d.index if t > floor_t})
        for D in dates:
            have = [p for p in PAIRS if D in daily[p].index]
            if len(have) < len(PAIRS) and (latest - D) < pd.Timedelta(days=WAIT_DAYS):
                break
            rows: list[dict] = []
            sel = carry_selection(rates, D) if (rule == "CARRY3" and D.dayofweek == 4 and rates is not None) else None
            for p in have:
                bar = daily[p].loc[D]
                # (a) pending decision fills at this bar's Open
                if p in pending:
                    dec = pending.pop(p)
                    ft, fpx = pd.Timestamp(bar["open_time"]), float(bar["Open"])
                    cur = pos.get(p)
                    if cur is not None:
                        inc, n_ro, n_n = swap_increment(p, cur["side"], cur["last_t"], ft, swaps)
                        acc = cur["acc"] + inc
                        rows.append({"event": "fill_exit", "key": f"out|{rule}|{p}|{_iso(D)}", "rule": rule, "pair": p,
                                     "side": cur["side"], "entry": cur["entry"], "entry_time_utc": cur["entry_time"],
                                     "exit": fpx, "fill_time_utc": _iso(ft), "risk": cur["risk"], "swap_acc_frac": acc,
                                     "r": _r(p, cur["side"], cur["entry"], fpx, acc, cur["risk"]),
                                     "decision_key": dec["key"], "swap_source": swap_src, "run_utc": run})
                        pos.pop(p)
                    if dec["target"] != 0 and dec.get("risk"):
                        rows.append({"event": "fill_entry", "key": f"in|{rule}|{p}|{_iso(D)}", "rule": rule, "pair": p,
                                     "side": dec["target"], "entry": fpx, "fill_time_utc": _iso(ft), "risk": dec["risk"],
                                     "decision_key": dec["key"], "run_utc": run})
                        pos[p] = {"side": dec["target"], "entry": fpx, "entry_time": _iso(ft), "risk": dec["risk"],
                                  "acc": 0.0, "last_t": _iso(ft)}
                    else:
                        rows.append({"event": "fill_done", "key": f"done|{rule}|{p}|{_iso(D)}", "rule": rule, "pair": p,
                                     "decision_key": dec["key"], "run_utc": run})
                # (b) mark at close
                cur = pos.get(p)
                if cur is not None:
                    inc, n_ro, n_n = swap_increment(p, cur["side"], cur["last_t"], D, swaps)
                    cur["acc"] += inc; cur["last_t"] = _iso(D)
                    rows.append({"event": "mark", "key": f"mark|{rule}|{p}|{_iso(D)}", "rule": rule, "pair": p,
                                 "bar_close_utc": _iso(D), "close": float(bar["Close"]), "side": cur["side"],
                                 "entry": cur["entry"], "risk": cur["risk"], "swap_acc_frac": cur["acc"],
                                 "rollovers": n_ro, "nights": n_n, "swap_source": swap_src,
                                 "unrealized_r": _r(p, cur["side"], cur["entry"], float(bar["Close"]), cur["acc"], cur["risk"]),
                                 "run_utc": run})
                # (c) decision at close
                side_now = pos[p]["side"] if p in pos else 0
                tgt = mom_target(daily[p], D) if rule == "MOM120" else (sel[p] if sel is not None else None)
                if tgt is not None and tgt != side_now:
                    rows.append({"event": "decision", "key": f"dec|{rule}|{p}|{_iso(D)}", "rule": rule, "pair": p,
                                 "bar_close_utc": _iso(D), "target": int(tgt), "from_side": side_now,
                                 "risk": risk_at(daily[p], D), "close": float(bar["Close"]),
                                 "rate_diff_pp": lf.rate_diff(rates, p, D) if rule == "CARRY3" else None, "run_utc": run})
                    pending[p] = rows[-1]
            rows.append({"event": "day", "key": f"day|{rule}|{_iso(D)}", "rule": rule, "bar_close_utc": _iso(D),
                         "missing_pairs": [p for p in PAIRS if p not in have], "run_utc": run})
            out.extend(rows)
    return out


def status(journal: list[dict]) -> dict:
    res = {}
    for rule in RULES:
        pos, pending, last_day = _state(journal, rule)
        res[rule] = {"last_day_utc": last_day, "open": {p: {k: v for k, v in s.items() if k in ("side", "entry", "entry_time", "acc")} for p, s in pos.items()},
                     "pending": {p: {"target": d["target"], "decided_at": d["bar_close_utc"]} for p, d in pending.items()},
                     "closed_trades": sum(1 for r in journal if r.get("rule") == rule and r.get("event") == "fill_exit")}
    return res
