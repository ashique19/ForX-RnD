"""Dry-run tip vol heal without writing (or with --write)."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forex_lab.tip_vol_heal import heal_watchlist_tip_volumes, format_heal_log_line

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--duka", action="store_true", default=False)
    ap.add_argument("--max-pairs", type=int, default=2)
    args = ap.parse_args()
    s = heal_watchlist_tip_volumes(
        tip_bars=200,
        duka_lookback_hours=24,
        use_dukascopy=bool(args.duka),
        write=bool(args.write),
        max_pairs=args.max_pairs,
    )
    print(format_heal_log_line(s))
    print(json.dumps(s, indent=2))

if __name__ == "__main__":
    main()
