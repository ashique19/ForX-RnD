"""Weekly champion/challenger retrain gate.

    python3 scripts/weekly_retrain.py
    python3 scripts/weekly_retrain.py --pair EURUSD
    python3 scripts/weekly_retrain.py --dry-run

Walk-forward compare vs the saved champion. By default this trains/scores a
challenger and writes ONLY the challenger comparison report
(data/champion/<PAIR>_challenger.json: after-cost PF / DD / return vs champion).
It NEVER writes the champion (data/champion/<PAIR>.json, models/<PAIR>_champion.json)
or refreshes production joblib. A gate pass is reported as promote_eligible.

Promotion is a separate, explicit human action:

    python3 scripts/weekly_retrain.py --pair EURUSD --promote

Same as `python -m forex_lab retrain`. Fail-soft. No live broker. Not a live edge.

For AFTER-COST Replay promote-only (rolling weekly pin + scoreboard), use
`scripts/weekly_replay_promote.py` instead — that compares Replay scoreboards
and does not silently overwrite `data/champion`.
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

