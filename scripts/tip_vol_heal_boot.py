"""START_DESK tip Volume heal entrypoint (fail-soft, Jetta then Active-capped Duka)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.tip_vol_heal import format_heal_log_line, heal_watchlist_tip_volumes


def main() -> None:
    summary = heal_watchlist_tip_volumes(
        tip_bars=200,
        duka_lookback_hours=16,
        use_dukascopy=True,
        use_jetta=True,
        write=True,
        max_pairs=4,
        duka_max_pairs=1,
        jetta_max_pairs=3,
        budget_sec=40,
    )
    print(format_heal_log_line(summary), flush=True)


if __name__ == "__main__":
    main()
