"""One-off: tag past paper entries with observe-only news fields (append-only annotations).

Explore journal: ``annotation`` rows with annotation_type ``news_tag``.
Main shadow journal: ``annotation`` rows with reason ``news_tag`` and
exclude_from_stage2=False (never removes a fill). Entries that already carry
news fields, or already have a news_tag annotation, are skipped (idempotent).
Back up both journals first. Default is a dry run; --apply appends.
History: FF snapshots only exist from the first fetch; earlier tags rely on the
USD/EUR point-in-time file, so coverage is marked per tag (full/partial/none).
"""
from __future__ import annotations

import argparse
import collections
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


def _when(row: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row.get("ts_utc")).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _todo(ev: list[dict]) -> list[dict]:
    tagged = {str(a.get("ref_position_id")) for a in ev
              if a.get("event") == "annotation" and (a.get("annotation_type") == "news_tag" or a.get("reason") == "news_tag")}
    return [o for o in ev if o.get("event") == "open" and "news_within_min" not in o
            and str(o.get("position_id")) not in tagged and _when(o)]


def explore_rows(ev: list[dict], now=None) -> list[dict]:
    out = []
    for o in _todo(ev):
        out.append({"event": "annotation", **eb._stamps(now), "annotation_type": "news_tag", "reason": "news_tag",
                    "ref_position_id": o["position_id"], "ref_open_ts_utc": o.get("ts_utc"), "pair": o.get("pair"),
                    "backfill": True, "exclude_from_explore": False, "exclude_from_stage2": False,
                    **nt.tag(str(o.get("pair")), _when(o))})
    return out


def main_rows(ev: list[dict]) -> list[dict]:
    out = []
    for o in _todo(ev):
        ts_utc, ts_dhaka = ps._ts_pair(None)
        out.append({"schema": ps.SCHEMA, "event": ps.ANNOTATION_EVENT, "ts_utc": ts_utc, "ts_dhaka": ts_dhaka,
                    "ref_fill_id": o.get("fill_id"), "ref_position_id": o.get("position_id"), "reason": "news_tag",
                    "annotation_type": "news_tag", "exclude_from_stage2": False, "backfill": True,
                    "ref_open_ts_utc": o.get("ts_utc"), "pair": o.get("pair"),
                    "notes": "observe-only news tag (no effect on Stage-2 counts or results)",
                    **nt.tag(str(o.get("pair")), _when(o))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    ex = explore_rows(eb.load())
    mn = main_rows(ps.load_events())
    if a.apply:
        for r in ex:
            eb.append(r)
        for r in mn:
            ps.append_event(r)
    cov = collections.Counter(r["news_coverage"] for r in ex + mn)
    buckets = collections.Counter(nt.bucket(r) for r in ex + mn)
    print(json.dumps({"applied": a.apply, "explore_tagged": len(ex), "main_tagged": len(mn),
                      "coverage": dict(cov), "buckets": dict(buckets),
                      "near": [{k: r.get(k) for k in ("pair", "ref_open_ts_utc", "news_within_min", "news_event")}
                               for r in ex + mn if nt.bucket(r) == "near"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
