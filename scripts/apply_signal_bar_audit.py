"""Append-only ``signal_bar_audit`` annotations from the partial-bar audit (observe-only).

Input: ``scripts/audit_partial_bar_entries.py --json`` output (``--from-json PATH``). One annotation per
audited open (explore + main shadow book), skipping positions that already carry one. Flags are always
``exclude_from_explore: false`` / ``exclude_from_stage2: false``, so no total changes; the tag only
enables extra views (e.g. explore_sum_r.py ``closed_bar_view``). Dry-run unless ``--apply``; back up
the journals first. Never edits or deletes journal rows.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab import explore_book as eb  # noqa: E402
from forex_lab import paper_shadow as ps  # noqa: E402

ANNOTATION_TYPE = "signal_bar_audit"
AUDIT_VERSION = "partial_bar_audit_v1"
_KEEP = ("bar_status", "partial_proven", "verdict", "signal_bar_utc", "bar_source", "bar_age_min",
         "m1_minutes_in_used_bar", "conf_used", "floor", "volume_variant", "replay_faithful", "fires_on_closed_bar")


def _pair(v):
    return (v[0], v[1]) if isinstance(v, (list, tuple)) and len(v) == 2 else (None, None)


def _fields(a: dict) -> dict:
    out = {k: a.get(k) for k in _KEEP}
    out["as_used_raw"], out["as_used_conf"] = _pair(a.get("as_used_replay"))
    out["closed_bar_raw"], out["closed_bar_conf"] = _pair(a.get("closed_bar"))
    return out


def _tagged(ev: list[dict]) -> set[str]:
    return {str(a.get("ref_position_id")) for a in ev
            if a.get("event") == "annotation" and a.get("annotation_type") == ANNOTATION_TYPE}


def explore_rows(audit: dict, ev: list[dict], now=None) -> list[dict]:
    opens = {str(e.get("position_id")): e for e in ev if e.get("event") == "open"}
    done = _tagged(ev)
    out = []
    for a in audit.get("entries") or []:
        pid = str(a.get("position_id"))
        if a.get("book") != "explore" or pid not in opens or pid in done:
            continue
        out.append({"event": "annotation", **eb._stamps(now), "annotation_type": ANNOTATION_TYPE, "reason": ANNOTATION_TYPE,
                    "audit_version": AUDIT_VERSION, "ref_position_id": pid, "ref_open_ts_utc": opens[pid].get("ts_utc"),
                    "pair": opens[pid].get("pair"), "side": opens[pid].get("side"), "backfill": True,
                    "exclude_from_explore": False, "exclude_from_stage2": False, **_fields(a),
                    "notes": "observe-only: entry decided on a forming/stale 1h bar (pre 0641a94); no effect on totals"})
        done.add(pid)
    return out


def main_rows(audit: dict, ev: list[dict]) -> list[dict]:
    opens = {str(e.get("position_id")): e for e in ev if e.get("event") == "open" and e.get("position_id")}
    done = _tagged(ev)
    out = []
    for a in audit.get("entries") or []:
        pid = str(a.get("position_id"))
        if a.get("book") != "main" or pid not in opens or pid in done:
            continue
        ts_utc, ts_dhaka = ps._ts_pair(None)
        o = opens[pid]
        out.append({"schema": ps.SCHEMA, "event": ps.ANNOTATION_EVENT, "ts_utc": ts_utc, "ts_dhaka": ts_dhaka,
                    "ref_fill_id": o.get("fill_id"), "ref_position_id": pid, "reason": ANNOTATION_TYPE,
                    "annotation_type": ANNOTATION_TYPE, "audit_version": AUDIT_VERSION, "exclude_from_stage2": False,
                    "backfill": True, "ref_open_ts_utc": o.get("ts_utc"), "pair": o.get("pair"), "side": o.get("side"),
                    **_fields(a), "notes": "observe-only signal-bar audit tag (no effect on Stage-2 counts or results)"})
        done.add(pid)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", required=True, help="audit_partial_bar_entries.py --json output")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    audit = json.loads(Path(a.from_json).read_text(encoding="utf-8-sig"))
    ex = explore_rows(audit, eb.load())
    mn = main_rows(audit, ps.load_events())
    before = eb.sum_r()
    if a.apply:
        for r in ex:
            eb.append(r)
        for r in mn:
            ps.append_event(r)
    after = eb.sum_r()
    print(json.dumps({"applied": a.apply, "explore_tagged": len(ex), "main_tagged": len(mn),
                      "by_verdict": dict(collections.Counter(r["verdict"] for r in ex + mn)),
                      "explore_sum_r_before": before["sum_r"], "explore_sum_r_after": after["sum_r"],
                      "totals_unchanged": before == after}, indent=1))
    return 0 if before == after else 5


if __name__ == "__main__":
    raise SystemExit(main())