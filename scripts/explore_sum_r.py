"""Print the canonical exploration sum_r (closed, annotation-aware): raw and reconciled.

raw            = close rows exactly as journaled, no annotations
pre_reconcile  = annotations except the 2026-10-19 broker_reconcile / duplicate_entry / demo_fill types
pre_fill_booking = all annotations except demo_fill (paper exits restated at real demo fills)
reconciled     = every annotation (THE canonical number; same as explore_book.sum_r())

Extra views (2026-10-20; appended AFTER the keys above, which keep their order and values):
post_fix_only   = reconciled basis, only positions opened after the partial-bar fix deploy (0641a94)
closed_bar_view = reconciled basis, excluding entries the signal_bar_audit tagged would_not_fire
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.explore_book import RECONCILE_TYPES, load, open_positions, sum_r  # noqa: E402

# Commit time of 0641a94 (partial-bar heal), 2026-10-19 20:47:39 Asia/Dhaka; desk restarted 20:45 Dhaka.
FIX_DEPLOY_UTC = "2026-10-19T14:47:39Z"
AUDIT_TYPE = "signal_bar_audit"


def _keep_positions(ev: list[dict], keep: set[str]) -> list[dict]:
    """Rows of the kept positions only (opens/closes by position_id, annotations by ref_position_id)."""
    out = []
    for e in ev:
        pid = e.get("position_id") if e.get("event") != "annotation" else e.get("ref_position_id")
        if pid is None or str(pid) in keep:
            out.append(e)
    return out


def post_fix_view(ev: list[dict], cutoff_utc: str = FIX_DEPLOY_UTC) -> dict:
    opens = [e for e in ev if e.get("event") == "open"]
    keep = {str(e.get("position_id")) for e in opens if str(e.get("ts_utc") or "") > cutoff_utc}
    return {**sum_r(_keep_positions(ev, keep)), "entries": len(keep), "cutoff_utc": cutoff_utc}


def closed_bar_view(ev: list[dict]) -> dict:
    verdict: dict[str, str] = {}
    for a in ev:
        if a.get("event") == "annotation" and a.get("annotation_type") == AUDIT_TYPE:
            verdict[str(a.get("ref_position_id"))] = str(a.get("verdict"))
    opens = {str(e.get("position_id")) for e in ev if e.get("event") == "open"}
    drop = {p for p, v in verdict.items() if v == "would_not_fire"}
    keep = opens - drop
    return {**sum_r(_keep_positions(ev, keep)), "entries": len(keep), "excluded_would_not_fire": len(drop & opens),
            "audited": len(set(verdict) & opens)}


def report(ev: list[dict]) -> dict:
    rec = sum_r(ev)
    return {**rec, "open": len(open_positions(ev)),
            "raw": sum_r(ev, use_annotations=False),
            "pre_reconcile": sum_r(ev, ignore_types=RECONCILE_TYPES),
            "pre_fill_booking": sum_r(ev, ignore_types=("demo_fill",)),
            "reconciled": rec,
            "post_fix_only": post_fix_view(ev),
            "closed_bar_view": closed_bar_view(ev)}


if __name__ == "__main__":
    print(json.dumps(report(load()), indent=2))