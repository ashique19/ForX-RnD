"""4h / daily research round 2026-10-31 (prereg research/new_families/PREREG_20261031.md, commit cdf5b81).

python research/new_families/round_20261031.py --phase insample [--cost-model mt5_measured]
python research/new_families/round_20261031.py --phase holdout     (only for in-sample passes recorded in the decisions file)
python research/new_families/round_20261031.py --sanity            (legacy-setting reproduction of 2026-10-22 numbers; not a trial)
Research only: reads data/history, the measured cost table, BIS rates and the MT5 swap snapshot; writes data/edge_check/round_20261031/.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "research" / "edge_check"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sw = _load("swap_retest_20261022", ROOT / "research" / "lowfreq" / "swap_retest_20261022.py")  # also loads lowfreq_20261022
lf = sw.lf
from forex_lab import cost_model as cm  # noqa: E402

PAIRS = lf.PAIRS
PRIMARY = ["EURUSD", "USDJPY"]
OUT = ROOT / "data" / "edge_check" / "round_20261031"
CUTOFF = lf.CUTOFF
N_TRIALS = 167
ALPHA = 0.05 / N_TRIALS
DD_CAP = 20.0
FOLD_EDGES = pd.date_range("2015-01-01", "2025-10-01", periods=6)
VARIANTS = {
    "F1_4h_q10": dict(kind="F1", tf="4h", q=0.10, extra=30, weekly=False, minkey="F1_4h"),
    "F1_4h_q20": dict(kind="F1", tf="4h", q=0.20, extra=30, weekly=False, minkey="F1_4h"),
    "F1_D_q10": dict(kind="F1", tf="D", q=0.10, extra=30, weekly=False, minkey="F1_D"),
    "F1_D_q20": dict(kind="F1", tf="D", q=0.20, extra=30, weekly=False, minkey="F1_D"),
    "F1_D_q10_W": dict(kind="F1", tf="D", q=0.10, extra=30, weekly=True, minkey="F1_DW"),
    "F1_D_q20_W": dict(kind="F1", tf="D", q=0.20, extra=30, weekly=True, minkey="F1_DW"),
    "F2_top3": dict(kind="F2", k=3, extra=30, minkey="F2"),
    "F3_L120": dict(kind="F3", L=120, extra=30, minkey="F3"),
    "LT1_SMA200": dict(kind="LT1", extra=0, minkey="LT1"),
    "LT2_DON55_20": dict(kind="LT2", extra=0, minkey="LT2"),
    "LT3_MA50_200": dict(kind="LT3", extra=0, minkey="LT3"),
}
MIN_IS = {"F1_4h": 100, "F1_D": 30, "F1_DW": 20, "F2": 30, "F3": 15, "LT1": 15, "LT2": 15, "LT3": 10}
MIN_HO = {"F1_4h": 15, "F1_D": 5, "F1_DW": 3, "F2": 5, "F3": 5, "LT1": 3, "LT2": 3, "LT3": 3}
assert len(VARIANTS) == 11 and N_TRIALS == 156 + len(VARIANTS)


# ------------------------------------------------------------------ signals
def week_key(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Trading-week label (Monday date) of daily bars indexed by close time (Mon..Fri 21:00/22:00 UTC closes)."""
    day = (idx - pd.Timedelta(hours=12)).normalize()
    return pd.DatetimeIndex(day - pd.to_timedelta(day.dayofweek, unit="D"))


def weekly_trend_table(daily: pd.DataFrame, n: int = 10) -> pd.Series:
    """sign(weekly close - SMA_n of weekly closes), indexed by the close time of the week's last daily bar (when it exists)."""
    wk = week_key(daily.index)
    close = daily["Close"].groupby(wk).last()
    done = pd.Series(daily.index, index=daily.index).groupby(wk).max()
    sma = close.rolling(n, min_periods=n).mean()
    tr = np.sign(close - sma).where(sma.notna())
    s = pd.Series(tr.to_numpy(), index=pd.DatetimeIndex(done.to_numpy()))
    return s.sort_index()


def weekly_trend_at(daily: pd.DataFrame, times: pd.DatetimeIndex, n: int = 10) -> pd.Series:
    """Weekly trend known at each decision time: latest weekly bar completed STRICTLY BEFORE the decision bar's close."""
    tab = weekly_trend_table(daily, n)
    pos = tab.index.searchsorted(np.asarray(times, dtype="datetime64[ns]"), side="left") - 1
    vals = np.where(pos >= 0, tab.to_numpy()[np.clip(pos, 0, None)] if len(tab) else np.nan, np.nan)
    return pd.Series(vals, index=times)


def target_sma_cross(daily: pd.DataFrame, fast: int | None, slow: int) -> pd.Series:
    """LT1 (fast=None): sign(Close - SMA_slow); LT3: sign(SMA_fast - SMA_slow). Unchanged on equality; NaN until defined."""
    c = daily["Close"]
    a = c if fast is None else c.rolling(fast, min_periods=fast).mean()
    b = c.rolling(slow, min_periods=slow).mean()
    s = np.sign(a - b).where(b.notna() & a.notna())
    s = s.replace(0.0, np.nan).ffill()
    return s.dropna().astype(int)


def target_donchian(daily: pd.DataFrame, entry_n: int = 55, exit_n: int = 20) -> pd.Series:
    """LT2 target changes only (+1/-1/0). Exit bar may reverse into the opposite entry; same-direction re-entry only later."""
    h, l, c = daily["High"], daily["Low"], daily["Close"]
    h_in, l_in = h.shift(1).rolling(entry_n, min_periods=entry_n).max(), l.shift(1).rolling(entry_n, min_periods=entry_n).min()
    h_out, l_out = h.shift(1).rolling(exit_n, min_periods=exit_n).max(), l.shift(1).rolling(exit_n, min_periods=exit_n).min()
    cur, out = 0, {}
    for t, cc, hi, lo, ho, lw in zip(daily.index, c, h_in, l_in, h_out, l_out):
        if not (np.isfinite(hi) and np.isfinite(lo) and np.isfinite(ho) and np.isfinite(lw)):
            continue
        new = cur
        if cur == 1 and cc < lw:
            new = 0
        elif cur == -1 and cc > ho:
            new = 0
        if new == 0:
            if cc > hi and cur != 1:
                new = 1
            elif cc < lo and cur != -1:
                new = -1
        if new != cur:
            out[t] = new
            cur = new
    return pd.Series(out, dtype=int)


# ------------------------------------------------------------------ metrics and rules
def fold_net_r(t: pd.DataFrame) -> list[float]:
    if t is None or len(t) == 0:
        return [0.0] * 5
    et = pd.to_datetime(t["entry_time"])
    return [float(t.loc[(et >= FOLD_EDGES[i]) & (et < FOLD_EDGES[i + 1]), "r"].sum()) if ((et >= FOLD_EDGES[i]) & (et < FOLD_EDGES[i + 1])).any() else 0.0
            for i in range(5)]


def folds_positive(t: pd.DataFrame) -> int:
    return int(sum(1 for x in fold_net_r(t) if x > 0))


def metrics(t: pd.DataFrame) -> dict:
    return lf.metrics(t, "r", alpha=ALPHA)


def is_pass(m: dict, minkey: str, folds_pos: int) -> bool:
    return bool(m["n"] >= MIN_IS[minkey] and m["pf"] is not None and m["pf"] >= 1.10 and abs(m["max_dd_r"]) <= m["net_r"]
                and abs(m["max_dd_r"]) <= DD_CAP and m["p_trade"] is not None and m["p_trade"] < ALPHA and folds_pos >= 3)


def ho_pass(m: dict, m_is: dict, minkey: str, n_cases: int) -> bool:
    return bool(m["n"] >= MIN_HO[minkey] and m["pf"] is not None and m["pf"] > 1.10 and m["net_r"] > 0
                and abs(m["max_dd_r"]) <= abs(m_is["max_dd_r"]) and m["p_trade"] is not None and m["p_trade"] < 0.05 / max(n_cases, 1))


# ------------------------------------------------------------------ simulation
def _none_fin(pair, side, entry, t0, t1, rates):
    ro = lf.rollovers(t0, t1)
    return 0.0, 0.0, int(len(ro))


class World:
    """History, bars and ATR per pair for a given in-sample extension (days past the cutoff); extra=None -> full data."""

    def __init__(self, extra: int | None, pairs: list[str]):
        import edge_lib as L
        self.pairs, self.bars, self.atr, self.missing = [], {"4h": {}, "D": {}}, {"4h": {}, "D": {}}, []
        for p in pairs:
            try:
                h = L.load_history(p)[["Open", "High", "Low", "Close"]].astype(float)
            except Exception as exc:  # reported, never filled
                self.missing.append((p, str(exc)))
                continue
            if extra is not None:
                h = h[h.index < CUTOFF + pd.Timedelta(days=extra)]
            self.pairs.append(p)
            for tf in ("4h", "D"):
                self.bars[tf][p] = lf.resample(h, tf)
                self.atr[tf][p] = lf.true_range_atr(self.bars[tf][p], 14)


def sim_variant(name: str, world: World, rt: dict[str, float], rates, phase: str) -> pd.DataFrame:
    v = VARIANTS[name]
    out = []
    tg_c = lf.carry_targets({p: world.bars["D"][p] for p in world.pairs}, rates, v["k"]) if v["kind"] == "F2" else None
    for p in world.pairs:
        if p not in rt:
            continue
        D, aD = world.bars["D"][p], world.atr["D"][p]
        if v["kind"] == "F1":
            b, a = world.bars[v["tf"]][p], world.atr[v["tf"]][p]
            side = lf.sig_f1(b, a, v["q"])
            if v["weekly"]:
                wt = weekly_trend_at(D, side.index)
                side = side[(wt.reindex(side.index).to_numpy() == side.to_numpy())]
            t = lf.sim_f1(b, a, side, p, rt[p], rates)
        elif v["kind"] == "F2":
            t = lf.sim_position(D, aD, tg_c[p], p, rt[p], rates)
        elif v["kind"] == "F3":
            t = lf.sim_position(D, aD, lf.sig_f3(D, v["L"]), p, rt[p], rates)
        elif v["kind"] == "LT1":
            t = lf.sim_position(D, aD, target_sma_cross(D, None, 200), p, rt[p], rates)
        elif v["kind"] == "LT3":
            t = lf.sim_position(D, aD, target_sma_cross(D, 50, 200), p, rt[p], rates)
        else:
            t = lf.sim_position(D, aD, target_donchian(D), p, rt[p], rates)
        if len(t):
            t = t[t["entry_time"] < CUTOFF] if phase == "insample" else t[t["entry_time"] >= CUTOFF]
            out.append(t.assign(variant=name))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def run_combos(names: list[str], phase: str, cost_model: str, combos: list[tuple[str, str, str]], pairs: list[str] = PAIRS):
    """combos: (label, cost model, financing model). Returns (trades, missing pairs per cost model, history-missing pairs)."""
    rates = lf.load_rates()
    swaps = sw.load_swaps()
    fins = {"swap": sw.make_swap_fin(swaps), "bis_markup": lf.financing, "none": _none_fin}
    orig = lf.financing
    worlds: dict[int | None, World] = {}
    allt, miss_cost = [], {}
    try:
        for label, cmodel, fin in combos:
            rt, miss = cm.cost_series(pairs, cmodel)
            miss_cost[cmodel] = miss
            lf.financing = fins[fin] if fin != "bis_markup" else orig
            for name in names:
                extra = None if phase == "holdout" else VARIANTS[name]["extra"]
                if extra not in worlds:
                    worlds[extra] = World(extra, pairs)
                t = sim_variant(name, worlds[extra], rt, rates, phase)
                if len(t):
                    allt.append(t.assign(combo=label, cost_model=cmodel, fin_model=fin))
            print("combo", label, "done", flush=True)
    finally:
        lf.financing = orig
    missing_hist = sorted({m for w in worlds.values() for m in w.missing})
    return (pd.concat(allt, ignore_index=True) if allt else pd.DataFrame()), miss_cost, missing_hist


# ------------------------------------------------------------------ reporting
JUDGED = "judged"
COMBOS = [(JUDGED, "mt5_measured", "swap"), ("bis_markup", "mt5_measured", "bis_markup"), ("no_fin", "mt5_measured", "none"),
          ("mt5_p90_swap", "mt5_p90", "swap"), ("mt5_p50_swap", "mt5_p50", "swap")]


def _fmt(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:.{nd}g}" if isinstance(x, float) and abs(x) < 1e-3 else f"{x:.{nd}f}")


def build_tables(tr: pd.DataFrame, names: list[str]) -> tuple[str, list[list[str]], dict]:
    rows, decisions = [], {}
    hdr = ("| Variant | Scope | Trades | PF judged | Net R | Max DD R | Win | p (t) | MDE R | Folds+ | PF BIS+markup | PF no fin | PF mt5_p90+swap | PF mt5_p50+swap | Power | In-sample |\n"
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    to_holdout = []
    for name in names:
        v = VARIANTS[name]
        scopes = [("pooled book (primary)", PAIRS)] if v["kind"] == "F2" else [(p, [p]) for p in PRIMARY]
        scopes.append(("10-pair pooled (label)", PAIRS))
        for label, plist in scopes:
            def sel(c):
                return tr[(tr["variant"] == name) & (tr["combo"] == c) & (tr["pair"].isin(plist))]
            tj = sel(JUDGED)
            m = metrics(tj)
            fp = folds_positive(tj)
            sec = {c: metrics(sel(c))["pf"] for c in ("bis_markup", "no_fin", "mt5_p90_swap", "mt5_p50_swap")}
            primary = not label.startswith("10-pair")
            passed = is_pass(m, v["minkey"], fp) if primary else None
            under = (m["mde_r"] is None) or (m["mde_r"] > 0.5)
            extra = ""
            if not primary:
                ppf = [metrics(tj[tj["pair"] == p])["pf"] for p in PAIRS if p in set(tj["pair"])]
                npos = sum(1 for x in ppf if x is not None and x > 1.0)
                extra = f"pairs PF>1 {npos}/{len(ppf)}; label " + ("yes" if (m["pf"] or 0) >= 1.0 and npos >= 6 else "no")
            rows.append(f"| {name} | {label} | {m['n']} | {_fmt(m['pf'])} | {_fmt(m['net_r'],1)} | {_fmt(m['max_dd_r'],1)} | {_fmt(m['win_rate'],2)} | {_fmt(m['p_trade'])} | "
                        f"{_fmt(m['mde_r'],2)} | {fp}/5 | {_fmt(sec['bis_markup'])} | {_fmt(sec['no_fin'])} | {_fmt(sec['mt5_p90_swap'])} | {_fmt(sec['mt5_p50_swap'])} | "
                        f"{'UNDERPOWERED' if under else 'ok'} | {('PASS' if passed else 'FAIL') if primary else extra} |")
            if primary:
                decisions[f"{name}|{label}"] = {"pass": bool(passed), "n": m["n"], "pf": m["pf"], "net_r": m["net_r"], "max_dd_r": m["max_dd_r"], "folds_pos": fp,
                                                "p": m["p_trade"], "mde": m["mde_r"], "folds_net_r": fold_net_r(tj)}
                if passed:
                    to_holdout.append([name, label, m["max_dd_r"]])
    return hdr + "\n" + "\n".join(rows), to_holdout, decisions


def per_pair_table(tr: pd.DataFrame, names: list[str]) -> str:
    lines = ["| Variant | " + " | ".join(PAIRS) + " |", "|---|" + "---:|" * len(PAIRS)]
    for name in names:
        cells = []
        for p in PAIRS:
            t = tr[(tr["variant"] == name) & (tr["combo"] == JUDGED) & (tr["pair"] == p)]
            m = metrics(t)
            cells.append(f"{m['n']} / {_fmt(m['pf'],2)}")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run_insample(cost_model: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    names = list(VARIANTS)
    combos = [(a, cost_model if a == JUDGED else b, c) for a, b, c in COMBOS]
    tr, miss_cost, miss_hist = run_combos(names, "insample", cost_model, combos)
    tr.to_csv(OUT / "insample_trades.csv", index=False)
    table, to_holdout, decisions = build_tables(tr, names)
    pp = per_pair_table(tr, names)
    (OUT / "insample_decisions.json").write_text(json.dumps({"to_holdout": to_holdout, "decisions": decisions, "missing_cost": miss_cost,
                                                              "missing_history": miss_hist, "cost_model": cost_model, "trials": N_TRIALS}, indent=2, default=str))
    (OUT / "insample_report.md").write_text(f"## In-sample (judged: {cost_model} + measured swaps)\n\n{table}\n\n### Per pair: trades / PF (judged)\n\n{pp}\n")
    print(table); print(); print(pp)
    print("to_holdout:", to_holdout, "| missing cost:", miss_cost, "| missing history:", miss_hist)


def run_holdout() -> None:
    dec = json.loads((OUT / "insample_decisions.json").read_text())
    cases = dec["to_holdout"]
    if not cases:
        print("nothing passed in-sample; holdout not run")
        return
    names = sorted({c[0] for c in cases})
    combos = [(a, dec["cost_model"] if a == JUDGED else b, c) for a, b, c in COMBOS]
    tr, _, _ = run_combos(names, "holdout", dec["cost_model"], combos)
    tr.to_csv(OUT / "holdout_trades.csv", index=False)
    lines = []
    for name, label, dd_is in cases:
        v = VARIANTS[name]
        plist = PAIRS if v["kind"] == "F2" else [label]
        t = tr[(tr["variant"] == name) & (tr["combo"] == JUDGED) & (tr["pair"].isin(plist))]
        m = metrics(t)
        ok = ho_pass(m, {"max_dd_r": dd_is}, v["minkey"], len(cases))
        lines.append(f"| {name} | {label} | {m['n']} | {_fmt(m['pf'])} | {_fmt(m['net_r'],1)} | {_fmt(m['max_dd_r'],1)} | {_fmt(m['p_trade'])} | {'PASS' if ok else 'FAIL'} |")
    (OUT / "holdout_report.md").write_text("| Variant | Case | Trades | PF | Net R | Max DD R | p | Holdout |\n|---|---|---:|---:|---:|---:|---:|---|\n" + "\n".join(lines) + "\n")
    print("\n".join(lines))


EXPECT = {("F1_4h_q20", "USDJPY"): (361, 0.966), ("F3_L120", "EURUSD"): (72, 1.032), ("F2_top3", "POOL"): (20, 1.520)}


def run_sanity() -> int:
    """Legacy settings: Duka p90 cost, BIS + 1 pip markup financing, +30 days. Must reproduce the 2026-10-22 table."""
    names = ["F1_4h_q20", "F3_L120", "F2_top3"]
    fixed = {n: dict(VARIANTS[n], extra=30) for n in names}
    saved = {n: VARIANTS[n] for n in names}
    VARIANTS.update(fixed)
    tr, _, _ = run_combos(names, "insample", "duka_p90", [("legacy", "duka_p90", "bis_markup")])
    VARIANTS.update(saved)
    bad = 0
    for (name, scope), (n_exp, pf_exp) in EXPECT.items():
        t = tr[(tr["variant"] == name) & ((tr["pair"] == scope) if scope != "POOL" else tr["pair"].notna())]
        m = lf.metrics(t, "r", alpha=0.05 / 154)
        ok = m["n"] == n_exp and m["pf"] is not None and abs(m["pf"] - pf_exp) < 0.0015
        bad += 0 if ok else 1
        print(f"sanity {name} {scope}: n={m['n']} (expect {n_exp}) PF={m['pf']:.4f} (expect {pf_exp}) -> {'OK' if ok else 'MISMATCH'}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["insample", "holdout"])
    ap.add_argument("--sanity", action="store_true")
    ap.add_argument("--cost-model", default="mt5_measured", choices=list(cm.COST_MODELS))
    a = ap.parse_args()
    if a.sanity:
        return 1 if run_sanity() else 0
    if a.phase == "insample":
        run_insample(a.cost_model)
    elif a.phase == "holdout":
        run_holdout()
    else:
        ap.error("--phase or --sanity required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())