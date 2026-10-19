"""Backfill broker truth + duplicate flags into the exploration journal (append-only).

* Paper closes whose MT5 demo position was already closed at the broker
  (``mt5_close.already_closed``) get an ``annotation`` row of type
  ``broker_reconcile`` with the broker's real exit price/time/reason and R
  from the real fill and the broker SL (read-only MT5 deal history).
* Same-signal-bar duplicate entries opened before the dedupe (commit e7df6ea)
  get an ``annotation`` row of type ``duplicate_entry`` with
  ``exclude_from_explore``. The first entry per (pair, side, signal bar) is kept.
* Never deletes or rewrites a row; idempotent (skips refs already annotated).
  Back up the journal first. Default is a dry run; pass --apply to append.

Run:
  .venv\\Scripts\\python.exe scripts\\explore_broker_reconcile.py [--apply] [--before-utc 2026-10-15T18:46:26Z]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab import explore_book as eb  # noqa: E402
from forex_lab import mt5_exec as mx  # noqa: E402


def signal_bar(open_row: dict, decisions: list[dict]) -> str | None:
    if open_row.get("signal_bar_dhaka"):
        return str(open_row["signal_bar_dhaka"])
    for d in decisions:
        if (d.get("ts_utc") == open_row.get("ts_utc") and d.get("pair") == open_row.get("pair")
                and d.get("take") and d.get("last_signal_dhaka")):
            return str(d["last_signal_dhaka"])
    return None


def duplicate_annotations(ev: list[dict], before_utc: str, now=None) -> list[dict]:
    done = {str(a.get("ref_position_id")) for a in ev
            if a.get("event") == "annotation" and a.get("annotation_type") == "duplicate_entry"}
    decisions = [e for e in ev if e.get("event") == "decision" and e.get("take")]
    first: dict[tuple, dict] = {}
    out = []
    for o in sorted((e for e in ev if e.get("event") == "open"), key=lambda e: str(e.get("ts_utc"))):
        bar = signal_bar(o, decisions)
        if not bar:
            continue
        key = (str(o.get("pair")).upper(), str(o.get("side")).upper(), bar)
        if key not in first:
            first[key] = o
            continue
        pid = str(o.get("position_id"))
        if str(o.get("ts_utc")) >= before_utc or pid in done:
            continue
        out.append({"event": "annotation", **eb._stamps(now), "annotation_type": "duplicate_entry",
                    "reason": "same_signal_bar_reentry_pre_dedupe", "ref_position_id": pid,
                    "ref_open_ts_utc": o.get("ts_utc"), "duplicate_of_position_id": first[key].get("position_id"),
                    "pair": key[0], "side": key[1], "signal_bar_dhaka": bar, "exclude_from_explore": True,
                    "exclude_from_stage2": True,
                    "notes": "re-entry on a signal bar already entered (fixed by the same-bar dedupe in e7df6ea); "
                             "the MT5 demo order (if any) was real and stays in the demo-vs-paper comparison"})
    return out


def broker_annotations(ev: list[dict], mt5, now=None) -> tuple[list[dict], list[dict]]:
    done = {str(a.get("ref_position_id")) for a in ev
            if a.get("event") == "annotation" and a.get("annotation_type") == "broker_reconcile"}
    closes: dict[str, dict] = {}
    for e in ev:
        if e.get("event") == "close":
            closes.setdefault(str(e.get("position_id")), e)
    m_open = {str(e.get("position_id")): e for e in ev if e.get("event") == "mt5_open" and e.get("ok") and e.get("ticket")}
    first_close = [e for e in ev if e.get("event") == "mt5_close" and e.get("ok") and e.get("already_closed")]
    out, skipped = [], []
    for mc in first_close:
        pid = str(mc.get("position_id"))
        c = closes.get(pid)
        if pid in done or c is None or c.get("exit_basis") == "mt5_broker" or pid not in m_open:
            continue
        b = mx.broker_state(mt5, int(m_open[pid]["ticket"]))
        if b.get("state") != "closed":
            skipped.append({"position_id": pid, "ticket": m_open[pid]["ticket"], **b})
            continue
        out.append(mx.broker_annotation(c, m_open[pid], b, now, reason="broker_closed_first_backfill"))
        done.add(pid)
    return out, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--before-utc", default="2026-10-15T18:46:26Z",
                    help="only flag duplicates opened before this (dedupe commit time)")
    a = ap.parse_args()
    ev = eb.load()
    from forex_lab import mt5_feed

    mt5_feed.connect(require_demo=True)
    mt5 = mt5_feed._mt5()
    try:
        broker, skipped = broker_annotations(ev, mt5)
    finally:
        mt5.shutdown()
    dups = duplicate_annotations(ev, a.before_utc)
    if a.apply:
        for row in broker + dups:
            eb.append(row)
    print(json.dumps({"applied": a.apply, "broker_reconcile": len(broker), "duplicate_entry": len(dups),
                      "skipped": skipped,
                      "broker_rows": [{k: r.get(k) for k in ("ref_position_id", "mt5_ticket", "original_exit_reason",
                                                             "original_pnl_r", "corrected_exit_reason", "corrected_pnl_r",
                                                             "corrected_exit_time_utc", "mt5_profit_usd")} for r in broker],
                      "duplicate_rows": [{k: r.get(k) for k in ("ref_position_id", "pair", "side", "signal_bar_dhaka",
                                                                "duplicate_of_position_id")} for r in dups]},
                     indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
