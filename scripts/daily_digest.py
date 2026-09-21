"""Print the daily research digest (yesterday/today, Asia/Dhaka).

    python3 scripts/daily_digest.py
    python3 scripts/daily_digest.py --when today

Same as ``python -m forex_lab digest``. Fail-soft. No live broker. Not a live edge.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.cli import main


if __name__ == "__main__":
    argv = ["digest", *sys.argv[1:]]
    raise SystemExit(main(argv))
