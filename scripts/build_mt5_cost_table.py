"""Build the versioned research-only MT5 measured cost table (read-only on every input).

python scripts/build_mt5_cost_table.py [--asof 2026-10-31] [--out research/costs/mt5_measured_costs_20261031.json]
Writes only the table JSON and a small markdown summary beside it. Never touches yaml, journals, MT5 or the desk.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forex_lab.cost_model import TABLE_PATH, build_table  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="2026-10-31")
    ap.add_argument("--spreads", default=str(ROOT / "data" / "costs" / "mt5_spreads.csv"))
    ap.add_argument("--journal", default=str(ROOT / "data" / "paper_shadow" / "explore_journal.jsonl"))
    ap.add_argument("--duka", default=str(ROOT / "data" / "paper_shadow" / "measured_costs.csv"))
    ap.add_argument("--out", default=str(TABLE_PATH))
    a = ap.parse_args()
    t = build_table(Path(a.spreads), Path(a.journal), Path(a.duka), asof=a.asof)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(t, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [f"# MT5 measured cost table (research only), asof {a.asof}", "",
             "Round trip, pips. mt5 = spread + 2 x adverse slippage; mt5_measured = max(mt5_p90, duka_p90).", "",
             "| Pair | spread n | spread p50 | spread p90 | slip n | slip mean | slip p90 | mt5 p50 | mt5 p90 | duka p50 | duka p90 | mt5_measured | binding |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for p, r in sorted(t["pairs"].items()):
        lines.append(f"| {p} | {r['spread_n']} | {r['spread_p50']} | {r['spread_p90']} | {r['slip_n']} ({r['slip_source']}) | {r['slip_adverse_mean']} | "
                     f"{r['slip_adverse_p90']} | {r['mt5_p50']} | {r['mt5_p90']} | {r.get('duka_p50')} | {r.get('duka_p90')} | {r.get('mt5_measured')} | {r.get('binding')} |")
    lines += ["", f"Pooled slippage: {json.dumps(t['slippage_pooled'])}", ""]
    out.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(out)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())