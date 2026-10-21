"""Swap re-test of F3_L120 / F2_top3 (prereg research/lowfreq/PREREG_SWAP_RETEST_20261022.md, commit 95c0ab3).

python research/lowfreq/swap_retest_20261022.py --phase insample|holdout
Only the financing function of lowfreq_20261022 is replaced. Research only; writes data/edge_check/lowfreq_20261022/.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "edge_check"))
_spec = importlib.util.spec_from_file_location("lowfreq_20261022", ROOT / "research" / "lowfreq" / "lowfreq_20261022.py")
lf = importlib.util.module_from_spec(_spec); sys.modules["lowfreq_20261022"] = lf; _spec.loader.exec_module(lf)

SNAP = ROOT / "data" / "research" / "swaps" / "mt5_swaps_20261021_1813Z.json"
N_TRIALS = 156
ALPHA_IS = 0.05 / N_TRIALS
NOW_RATE_DATE = "2026-10-14"


def load_swaps(path: Path = SNAP) -> dict[str, tuple[float, float]]:
    s = json.loads(Path(path).read_text())
    return {r["pair"]: (float(r["swap_long"]), float(r["swap_short"])) for r in s["rows"]}


def nights(ro: pd.DatetimeIndex) -> np.ndarray:
    """Charge multiplier per weekday 21:00 UTC rollover: Wednesday x3 (7 nights/week)."""
    return np.where(ro.dayofweek == 2, 3.0, 1.0)


def make_swap_fin(swaps):
    def fin(pair, side, entry, t0, t1, rates):
        ro = lf.rollovers(t0, t1)
        if not len(ro):
            return 0.0, 0.0, 0
        s = swaps[pair][0] if side == 1 else swaps[pair][1]
        return float((nights(ro) * s / 100.0 / 360.0).sum()), 0.0, int(len(ro))
    return fin


def make_hist_plus_markup_fin(swaps, rates):
    def fin(pair, side, entry, t0, t1, _rates):
        ro = lf.rollovers(t0, t1)
        if not len(ro):
            return 0.0, 0.0, 0
        now_diff = lf.rate_diff(rates, pair, NOW_RATE_DATE)
        s = swaps[pair][0] if side == 1 else swaps[pair][1]
        markup = s - side * now_diff
        hist = np.array([side * lf.rate_diff(rates, pair, r) for r in ro])
        hist = np.where(np.isfinite(hist), hist, 0.0)
        return float((nights(ro) * (hist + markup) / 100.0 / 360.0).sum()), 0.0, int(len(ro))
    return fin


def run(phase: str, only: list[str]) -> pd.DataFrame:
    import edge_lib as L
    costs = L.measured_costs(); rates = lf.load_rates(); swaps = load_swaps()
    hist = {}
    for p in lf.PAIRS:
        h = L.load_history(p)[["Open", "High", "Low", "Close"]].astype(float)
        hist[p] = h[h.index < lf.CUTOFF + pd.Timedelta(days=30)] if phase == "insample" else h
    daily = {p: lf.resample(hist[p], "D") for p in lf.PAIRS}
    atr = {p: lf.true_range_atr(daily[p], 14) for p in lf.PAIRS}
    orig = lf.financing
    out = []
    try:
        for model, fin in (("swap_now", make_swap_fin(swaps)), ("hist_plus_markup", make_hist_plus_markup_fin(swaps, rates))):
            lf.financing = fin
            for name in only:
                tg = lf.carry_targets(daily, rates, 3) if name == "F2_top3_swap" else None
                pairs = lf.PAIRS
                for p in pairs:
                    for q in ("p90", "p50"):
                        rt = 2 * float(costs.loc[p, f"one_way_{q}"])
                        target = tg[p] if tg is not None else lf.sig_f3(daily[p], 120)
                        t = lf.sim_position(daily[p], atr[p], target, p, rt, rates)
                        if len(t):
                            t = t[t["entry_time"] < lf.CUTOFF] if phase == "insample" else t[t["entry_time"] >= lf.CUTOFF]
                            out.append(t.assign(variant=name, cost=q, fin_model=model))
    finally:
        lf.financing = orig
    tr = pd.concat(out, ignore_index=True)
    tr.to_csv(lf.OUT / f"swap_retest_{phase}_trades.csv", index=False)
    return tr


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--phase", choices=["insample", "holdout"], required=True)
    a = ap.parse_args()
    only = ["F3_L120_swap", "F2_top3_swap"]
    if a.phase == "holdout":
        dec = json.loads((lf.OUT / "swap_retest_insample_decisions.json").read_text())
        only = sorted({c[0] for c in dec["to_holdout"]})
        if not only:
            print("nothing passed in-sample; holdout not run"); return
    tr = run(a.phase, only)
    print(a.phase, "trades", len(tr))


if __name__ == "__main__":
    main()
