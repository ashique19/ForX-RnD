"""Stage-3 cost honesty probe (assumed vs tip/history BA).

Samples Active + watchlist tip OHLC and data/history Dukascopy Bid/Ask.
Writes:
  data/paper_shadow/cost_probe_YYYYMMDD.json
  _COST_HONESTY_RESULT_YYYYMMDD.txt

No YAML cost flip. No Replay. No Jetta remesh.

Run:
  .venv\\Scripts\\python.exe scripts\\cost_honesty_probe.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.cost_honesty import run_cost_probe, write_probe_artifacts


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage-3 cost honesty probe")
    ap.add_argument("--sample-n", type=int, default=200, help="recent bars per pair")
    args = ap.parse_args()
    report = run_cost_probe(sample_n=max(20, int(args.sample_n)))
    json_path, txt_path = write_probe_artifacts(report)
    print(txt_path.read_text(encoding="utf-8"))
    print(f"wrote {json_path}")
    print(f"wrote {txt_path}")


if __name__ == "__main__":
    main()
