"""Weekly champion/challenger retrain gate.

    python3 scripts/weekly_retrain.py
    python3 scripts/weekly_retrain.py --pair EURUSD
    python3 scripts/weekly_retrain.py --dry-run

Walk-forward compare vs the saved champion. Promote only if PF / total return /
max DD improve (or the non-regression bar). Else keep champion and report null.

Same as ``python -m forex_lab retrain``. Fail-soft. No live broker. Not a live edge.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.cli import main


if __name__ == "__main__":
    argv = ["retrain", *sys.argv[1:]]
    raise SystemExit(main(argv))
