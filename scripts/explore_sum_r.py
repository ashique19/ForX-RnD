"""Print the single canonical exploration sum_r (closed, annotation-aware)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.explore_book import load, open_positions, sum_r  # noqa: E402

if __name__ == "__main__":
    ev = load()
    print(json.dumps({**sum_r(ev), "open": len(open_positions(ev))}, indent=2))
