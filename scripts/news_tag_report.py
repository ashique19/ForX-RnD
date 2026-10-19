"""Observe-only: results for trades near high-impact news (|news_within_min| <= 60) vs the rest.

Explore book: the reconciled closed set from explore_book.closed_r (demo-fill
truth: broker closes, demo fills, duplicates and orphans excluded), the same
logic explore_sum_r.py reports. Main shadow book: closed positions not
excluded from Stage-2; exit from the latest corrected annotation if any, else
the close row; R off the journaled SL; net pips minus the row's assumed cost.
Buckets: near / far (both currencies covered by the calendar, nothing within
the window) / unknown (calendar did not cover a currency at that time).
Pre-registered rule (docs/experiment_log.md): consider a blackout filter only
with >= 30 near trades AND near PF < 0.8 while far PF >= 1.0 on demo fills.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab import explore_book as eb  # noqa: E402
from forex_lab import news_tag as nt  # noqa: E402
from forex_lab import paper_shadow as ps  # noqa: E402

BUCKETS = ("near", "far", "unknown")


def _tags(ev: list[dict]) -> dict[str, dict]:
    tags = {str(o.get("position_id")): o for o in ev if o.get("event") == "open" and "news_within_min" in o}
    for a in ev:
        if a.get("event") == "annotation" and (a.get("annotation_type") == "news_tag" or a.get("reason") == "news_tag"):
            tags.setdefault(str(a.get("ref_position_id")), a)
    return tags


def stats(rows: list[dict]) -> dict:
    rs = [r["pnl_r"] for r in rows if isinstance(r.get("pnl_r"), (int, float))]
    wins = [x for x in rs if x > 0]; losses = [-x for x in rs if x < 0]
    return {"n": len(rows), "win_rate": round(len(wins) / len(rs), 3) if rs else None,
            "pf": round(sum(wins) / sum(losses), 3) if losses else (None if not wins else float("inf")),
            "sum_r": round(sum(rs), 3) if rs else 0.0,
            "net_pips": round(sum(r.get("pips_net") or 0.0 for r in rows), 1)}


def explore_rows(ev: list[dict]) -> list[dict]:
    tags = _tags(ev)
    return [{**r, "bucket": nt.bucket(tags.get(r["position_id"]))} for r in eb.closed_r(ev)]


def main_rows(ev: list[dict]) -> list[dict]:
    refs = ps.excluded_refs(ev)
    tags = _tags(ev)
    opens = {str(o.get("position_id")): o for o in ev if o.get("event") == "open" and not ps.is_excluded(o, refs)}
    corr: dict[str, dict] = {}
    for a in ev:
        if a.get("event") == ps.ANNOTATION_EVENT and a.get("corrected_exit_mid") is not None:
            corr[str(a.get("ref_position_id"))] = a
    out, seen = [], set()
    for c in ev:
        pid = str(c.get("position_id"))
        if c.get("event") != "close" or pid in seen or pid not in opens:
            continue
        seen.add(pid)
        o = opens[pid]
        entry, sl = eb._f(o.get("entry_mid")), eb._f(o.get("sl"))
        a = corr.get(pid)
        ex = eb._f(a.get("corrected_exit_mid")) if a else eb._f(c.get("exit_mid"))
        if entry is None or ex is None:
            continue
        d = 1.0 if str(o.get("side")).upper() == "BUY" else -1.0
        pnl = (ex - entry) * d
        risk = abs(entry - sl) if sl is not None else 0.0
        gross = pnl / eb.pip_size(o.get("pair"))
        cost = eb._f(o.get("assumed_cost_pips")) or 0.0
        out.append({"position_id": pid, "pair": o.get("pair"), "pnl_r": round(pnl / risk, 4) if risk else None,
                    "pips_net": round(gross - cost, 2), "bucket": nt.bucket(tags.get(pid))})
    # main closes may also arrive only as a corrected annotation (close_not_journaled)
    for pid, a in corr.items():
        if pid in seen or pid not in opens or a.get("reason") != "close_not_journaled":
            continue
        o = opens[pid]; entry, sl, ex = eb._f(o.get("entry_mid")), eb._f(o.get("sl")), eb._f(a.get("corrected_exit_mid"))
        if entry is None or ex is None:
            continue
        d = 1.0 if str(o.get("side")).upper() == "BUY" else -1.0
        pnl = (ex - entry) * d; risk = abs(entry - sl) if sl is not None else 0.0
        out.append({"position_id": pid, "pair": o.get("pair"), "pnl_r": round(pnl / risk, 4) if risk else None,
                    "pips_net": round(pnl / eb.pip_size(o.get("pair")) - (eb._f(o.get("assumed_cost_pips")) or 0.0), 2),
                    "bucket": nt.bucket(tags.get(pid))})
    return out


def report(explore_ev: list[dict], main_ev: list[dict]) -> dict:
    out: dict = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "window_min": nt.WINDOW_MIN, "books": {}}
    for name, rows in (("explore_reconciled_demo_fill", explore_rows(explore_ev)), ("main_shadow", main_rows(main_ev))):
        out["books"][name] = {b: stats([r for r in rows if r["bucket"] == b]) for b in BUCKETS}
        out["books"][name]["all"] = stats(rows)
    e = out["books"]["explore_reconciled_demo_fill"]
    near, far = e["near"], e["far"]
    trigger = near["n"] >= 30 and near["pf"] is not None and near["pf"] < 0.8 and far["pf"] is not None and far["pf"] >= 1.0
    out["decision_rule"] = {"rule": "blackout filter considered only if near n>=30 AND near PF<0.8 AND far PF>=1.0 (explore, demo fills)",
                            "near_n": near["n"], "met": bool(trigger),
                            "verdict": "consider filter (needs user OK)" if trigger else "no filter (rule not met)"}
    return out


def _fmt(rep: dict) -> str:
    lines = [f"News tag report (window +/-{rep['window_min']} min), {rep['generated_utc']}"]
    for book, b in rep["books"].items():
        lines.append(f"\n{book}")
        lines.append(f"  {'bucket':8} {'n':>4} {'win%':>6} {'PF':>7} {'R':>8} {'net pips':>9}")
        for k in (*BUCKETS, "all"):
            s = b[k]
            wr = "-" if s["win_rate"] is None else f"{100 * s['win_rate']:.0f}"
            pf = "-" if s["pf"] is None else f"{s['pf']:.2f}"
            lines.append(f"  {k:8} {s['n']:>4} {wr:>6} {pf:>7} {s['sum_r']:>8.2f} {s['net_pips']:>9.1f}")
    d = rep["decision_rule"]
    lines.append(f"\nDecision rule: {d['rule']}\n  near n={d['near_n']} -> {d['verdict']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rep = report(eb.load(), ps.load_events())
    print(json.dumps(rep, indent=1, default=str) if a.json else _fmt(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
