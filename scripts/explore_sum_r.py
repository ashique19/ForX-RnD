"""Print the canonical exploration sum_r (closed, annotation-aware): raw and reconciled.

raw            = close rows exactly as journaled, no annotations
pre_reconcile  = annotations except the 2026-10-19 broker_reconcile / duplicate_entry / demo_fill types
pre_fill_booking = all annotations except demo_fill (paper exits restated at real demo fills)
reconciled     = every annotation (THE canonical number; same as explore_book.sum_r())
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.explore_book import RECONCILE_TYPES, load, open_positions, sum_r  # noqa: E402

if __name__ == "__main__":
    ev = load()
    rec = sum_r(ev)
    print(json.dumps({**rec, "open": len(open_positions(ev)),
                      "raw": sum_r(ev, use_annotations=False),
                      "pre_reconcile": sum_r(ev, ignore_types=RECONCILE_TYPES),
                      "pre_fill_booking": sum_r(ev, ignore_types=("demo_fill",)),
                      "reconciled": rec}, indent=2))
