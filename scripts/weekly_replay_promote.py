"""Rolling weekly promote-only Replay (after-cost scoreboard gate).

    python scripts/weekly_replay_promote.py --dry-run
    python scripts/weekly_replay_promote.py --dry-run --pair EURUSD
    python scripts/weekly_replay_promote.py --seed-only --champion-job 45bbad3a346c45098fbfe9bf358e8c54
    python scripts/weekly_replay_promote.py --challenger-job <new_replay_job_id>

Promote ONLY if challenger Replay book beats the pinned weekly champion on
PF / total return / max DD (+ absolute floors). Else KEEP. Default is dry-run
(no pointer write). Does not start a Replay. Does not overwrite data/champion
unless retrain.weekly_replay.apply_live_on_promote is true.

Same gate as ``python -m forex_lab retrain`` / scripts/weekly_retrain.py, but
metrics come from Replay scoreboards (after costs), not yfinance walk-forward.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.config_loader import load_config
from forex_lab.weekly_replay_promote import (
    format_weekly_promote_text,
    run_weekly_replay_promote,
    seed_weekly_champion,
    weekly_replay_cfg,
)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Rolling weekly promote-only Replay (after-cost; promote or keep)",
    )
    p.add_argument("--pair", default=None, help="e.g. EURUSD")
    p.add_argument("--config", default=None, help="Path to YAML config")
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Compare only; never write weekly_champion pointer / live joblib",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Allow pointer write on promote (still respects apply_live_on_promote for joblib)",
    )
    p.add_argument("--champion-job", default=None, help="Incumbent Replay job id")
    p.add_argument("--challenger-job", default=None, help="Challenger Replay job id")
    p.add_argument(
        "--challenger-book",
        default=None,
        help="Book on challenger job (default: champion, or challenger when job omitted)",
    )
    p.add_argument(
        "--seed-only",
        action="store_true",
        help="Pin champion-job (or baseline) as weekly champion and exit (not a promotion)",
    )
    p.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = p.parse_args(argv)

    cfg = load_config(args.config) if args.config else load_config()
    wr = weekly_replay_cfg(cfg)

    if args.seed_only:
        job = args.champion_job or wr.get("baseline_job")
        if not job:
            print("seed-only requires --champion-job or retrain.weekly_replay.baseline_job", file=sys.stderr)
            return 1
        rec = seed_weekly_champion(str(job), cfg, persist=not args.dry_run)
        if args.json:
            print(json.dumps(rec, indent=2, default=str))
        else:
            print(
                f"weekly champion seeded  job={rec.get('job_id')}  "
                f"verdict={rec.get('verdict')}  dry_run={bool(args.dry_run)}"
            )
            m = rec.get("metrics") or {}
            print(
                f"  PF={m.get('profit_factor')} ret={m.get('total_return')} "
                f"DD={m.get('max_drawdown')} n={m.get('n_trades')}"
            )
        return 0

    # Default habit is dry-run unless --apply is explicit (no silent overwrite).
    dry = True if not args.apply else False
    if args.dry_run:
        dry = True

    result = run_weekly_replay_promote(
        args.pair,
        cfg,
        champion_job=args.champion_job,
        challenger_job=args.challenger_job,
        challenger_book=args.challenger_book,
        dry_run=dry,
    )
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(format_weekly_promote_text(result))
        if result.get("scoreboard_md"):
            print(f"scoreboard: {result.get('scoreboard_md')}")
        if result.get("scoreboard_latest_md"):
            print(f"latest: {result.get('scoreboard_latest_md')}")
    if result.get("ok") is False:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
