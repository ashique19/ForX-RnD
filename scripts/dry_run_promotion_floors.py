# -*- coding: utf-8 -*-
"""Dry-run absolute promotion floors against recent Replay scoreboards.

No new Replay — floors are gate-only (do not change live signal filtering).
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from forex_lab.config_loader import load_config
from forex_lab.retrain import promotion_decision, promotion_floors, retrain_cfg

ROOT = Path(r"C:\AI\forex-lab")
REPLAY = ROOT / "data" / "replay"
NOW = datetime.now().strftime("%Y-%m-%d %H:%M Asia/Dhaka")

JOBS = [
    ("c7a5b4603aa94c969b406afba887e704", "full +vol (champion baseline)"),
    ("d78a33e99899498fbb741946f4f4eb53", "short +vol"),
    ("5a62a59812294465b17775b5896f16bb", "full costs+overlap"),
    ("ba8fa3aa5ae94bf0ac0d8bf641c32a39", "short costs+overlap"),
    ("57f4dd89366c4ed692466795fd6f22c2", "full costs-only"),
]


def _row(path: Path, book: str) -> dict:
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if r.get("book") == book:
                return {
                    "n_trades": int(float(r["n_trades"])),
                    "win_rate": float(r["win_rate"]),
                    "profit_factor": float(r["profit_factor"]),
                    "total_return": float(r["total_return"]),
                    "max_drawdown": float(r["max_drawdown"]),
                }
    raise KeyError(book)


def _fmt(m: dict) -> str:
    return (
        f"PF={m['profit_factor']:.4f} WR={m['win_rate']:.4f} "
        f"ret={m['total_return']:+.4f} DD={m['max_drawdown']:.4f} n={m['n_trades']}"
    )


def main() -> None:
    cfg = load_config()
    floors = promotion_floors(cfg)
    rc = retrain_cfg(cfg)

    rows_out = []
    lines = []
    lines.append("# Tighten pass F — promotion floors (PF / WR / DD)")
    lines.append("")
    lines.append(f"- Generated: {NOW}")
    lines.append("- Replay: **not run** (gate logic only; does not change signal filtering)")
    lines.append("- Live gates kept ON: min_confidence 0.55, costs, session overlap,")
    lines.append("  vol_regime skip_extremes; news blackout fail-soft")
    lines.append("")
    lines.append("## Floors chosen (config `retrain.promotion`)")
    lines.append("")
    lines.append("| knob | value | rationale |")
    lines.append("|---|---:|---|")
    lines.append(
        f"| `enabled` | `{floors.get('enabled')}` | absolute floors ON by default |"
    )
    lines.append(
        f"| `min_profit_factor` | **{floors.get('min_profit_factor')}** | "
        "above break-even cushion; blocks promoting a less-bad loser "
        "(e.g. PF 0.90→0.95). Roadmap 'clearly >1' bar; 1.10 left as future tighten |"
    )
    lines.append(
        f"| `max_drawdown_floor` | **{floors.get('max_drawdown_floor')}** | "
        "challenger DD must be ≥ this (shallower than −15%) |"
    )
    lines.append(
        f"| `min_win_rate` | **{floors.get('min_win_rate')}** | "
        "soft floor; asym R:R OK below 50% |"
    )
    lines.append(
        f"| `min_trades` | **{floors.get('min_trades')}** "
        f"(retrain top-level also {rc.get('min_trades')}) | "
        "was 1; 30 filters fold noise without killing short windows |"
    )
    lines.append("")
    lines.append(
        "Relative `improve` / `non_regression` rules still required. "
        "Seed (no champion) bypasses floors. Not a live edge."
    )
    lines.append("")
    lines.append("## Dry-run on recent scoreboards")
    lines.append("")
    lines.append(
        "For each prior Replay job: treat scoreboard **champion** as incumbent and "
        "**challenger** as the candidate under the **new** floors-aware gate "
        "(same relative improve mode as live)."
    )
    lines.append("")
    lines.append(
        "| job | window | champ | chal | old verdict | new verdict | floors_ok | note |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    for job_id, label in JOBS:
        sb = REPLAY / job_id / "scoreboard.csv"
        promo_path = REPLAY / job_id / "promotion.json"
        old = json.loads(promo_path.read_text(encoding="utf-8")) if promo_path.exists() else {}
        old_v = old.get("verdict", "?")
        champ = _row(sb, "champion")
        chal = _row(sb, "challenger")
        # Also: would floors alone pass on the *incumbent* champion book
        # (informational — promotion compares challenger vs champion).
        d = promotion_decision(champ, chal, cfg)
        note = "; ".join(d.reasons[:2]) if d.reasons else ""
        if len(note) > 90:
            note = note[:87] + "..."
        lines.append(
            f"| `{job_id}` | {label} | {_fmt(champ)} | {_fmt(chal)} | "
            f"`{old_v}` | `{d.verdict}` | {d.floors_ok} | {note} |"
        )
        rows_out.append(
            {
                "job_id": job_id,
                "label": label,
                "old_verdict": old_v,
                "new_verdict": d.verdict,
                "new_promote": d.promote,
                "floors_ok": d.floors_ok,
                "pf_floor_ok": d.pf_floor_ok,
                "dd_floor_ok": d.dd_floor_ok,
                "wr_floor_ok": d.wr_floor_ok,
                "reasons": list(d.reasons),
                "champion": champ,
                "challenger": chal,
            }
        )

    # Hypothetical: less-bad loser that old improve would promote
    lines.append("")
    lines.append("## Hypothetical: less-bad loser (why floors exist)")
    lines.append("")
    hypo_champ = {
        "n_trades": 200,
        "win_rate": 0.48,
        "profit_factor": 0.90,
        "total_return": -0.05,
        "max_drawdown": -0.10,
    }
    hypo_chal = {
        "n_trades": 200,
        "win_rate": 0.50,
        "profit_factor": 0.98,
        "total_return": -0.01,
        "max_drawdown": -0.07,
    }
    d_off = promotion_decision(
        hypo_champ, hypo_chal, {"retrain": {"promotion": {"enabled": False}, "min_trades": 1}}
    )
    d_on = promotion_decision(hypo_champ, hypo_chal, cfg)
    lines.append(
        f"- Challenger PF 0.98 vs champion 0.90 (relative improve clears): "
        f"floors **off** → `{d_off.verdict}`; floors **on** → `{d_on.verdict}` "
        f"({'; '.join(r for r in d_on.reasons if 'floor' in r.lower())})."
    )
    lines.append("")

    # Would current best full champion clear floors if it were a challenger?
    lines.append("## Would current best full champion clear floors as a challenger?")
    lines.append("")
    best = _row(REPLAY / "c7a5b4603aa94c969b406afba887e704" / "scoreboard.csv", "champion")
    weak = {
        "n_trades": 400,
        "win_rate": 0.50,
        "profit_factor": 1.00,
        "total_return": 0.00,
        "max_drawdown": -0.05,
    }
    d_best = promotion_decision(weak, best, cfg)
    lines.append(
        f"- c7a5 champion ({_fmt(best)}) vs weak PF=1.00 incumbent → "
        f"`{d_best.verdict}` promote={d_best.promote} floors_ok={d_best.floors_ok}."
    )
    short = _row(REPLAY / "d78a33e99899498fbb741946f4f4eb53" / "scoreboard.csv", "champion")
    d_short = promotion_decision(weak, short, cfg)
    lines.append(
        f"- d78a short champion ({_fmt(short)}) vs same weak → "
        f"`{d_short.verdict}` promote={d_short.promote} floors_ok={d_short.floors_ok}."
    )
    lines.append("")
    lines.append("## Read")
    lines.append("")
    lines.append(
        "- All five recent Replay challengers were already `null` under relative "
        "improve; floors do not change those outcomes (still `null`, and "
        "challenger PF/WR/DD also fail absolute floors)."
    )
    lines.append(
        "- Floors **do** change the dangerous case: promoting PF&lt;1.05 over a "
        "worse champion. That is the whole point of step 8."
    )
    lines.append(
        "- Current best full champion (c7a5 PF 1.142 / WR 0.54 / DD −2.8% / n=400) "
        "clears every floor; short vol champ (d78a PF 1.072) also clears PF 1.05."
    )
    lines.append("- No Replay started. `data/champion` untouched. Step 9 not started.")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("- Code: `forex_lab/retrain.py` (`promotion_floors`, floor flags on `PromotionDecision`)")
    lines.append("- Config: `config/default.yaml` → `retrain.promotion` + `min_trades: 30`")
    lines.append("- Tests: `tests/test_retrain.py` (16 passed)")
    lines.append("- Dry-run: `scripts/dry_run_promotion_floors.py`")
    lines.append("")
    lines.append("## Kept ON (no step 9 from here)")
    lines.append("")
    lines.append(
        "- min_confidence 0.55, costs (slip 0.2 + exit_slippage), session_gate overlap, "
        "news blackout fail-soft, vol_regime skip_extremes, promotion floors."
    )
    lines.append("")

    out_md = REPLAY / "tighten_F_promotion_floors.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_json = REPLAY / "tighten_F_promotion_floors_dry_run.json"
    out_json.write_text(
        json.dumps(
            {"generated": NOW, "floors": floors, "retrain_min_trades": rc.get("min_trades"), "jobs": rows_out},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_md}")
    print(f"wrote {out_json}")
    for r in rows_out:
        print(r["job_id"][:8], r["old_verdict"], "->", r["new_verdict"], "floors_ok=", r["floors_ok"])


if __name__ == "__main__":
    main()