"""Phase 1 daily lens library harness.

Prereg: research/phase1/PREREG_20261112.md (commit 218e3e5). Research only.

python research/phase1/run_phase1.py --sanity
python research/phase1/run_phase1.py --phase run
python research/phase1/run_phase1.py --phase insample
python research/phase1/run_phase1.py --phase holdout
python research/phase1/run_phase1.py --phase report
python research/phase1/run_phase1.py --phase appendix

Intermediate outputs (gitignored): data/edge_check/phase1_20261112/
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "research" / "edge_check", ROOT / "research" / "phase1"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("confluence_20261103", ROOT / "research" / "htf" / "confluence_20261103.py")
import lenses as L  # noqa: E402
from forex_lab import cost_model as cm  # noqa: E402
from forex_lab.backtest import true_range_atr  # noqa: E402

HOLD, BUY, SELL = C.HOLD, C.BUY, C.SELL
PRIMARY = ["EURUSD", "USDJPY"]
OLD = C.OLD
OUT = ROOT / "data" / "edge_check" / "phase1_20261112"
CUTOFF = C.CUTOFF
N_PRIOR = 174
DIRECTIONAL = list(L.DIRECTIONAL)
VARIANTS = DIRECTIONAL + ["BEST_PER_REGIME"]
N_TRIALS = N_PRIOR + len(VARIANTS)
ALPHA = 0.05 / N_TRIALS
assert N_TRIALS == 182
FOLD_EDGES = pd.date_range("2015-01-01", "2025-10-01", periods=6)
BEST_START = pd.Timestamp("2016-01-01")
JUDGED_COSTS = {"EURUSD": 1.4, "USDJPY": 2.4}
SANITY = {"EURUSD": (3153, -80.729), "USDJPY": (2005, -154.674)}
WF_YEARS = list(range(2016, 2026))
H = pd.Timedelta(hours=1)
BOOT_BLOCK, BOOT_DRAWS, BOOT_SEED = 20, 1000, 7


def research_cfg() -> dict:
    return C.research_cfg()


def pair_inputs(pair: str):
    return C.pair_inputs(pair)


def cfg_with_cost(base: dict, rt_pips: float) -> dict:
    return C.cfg_with_cost(base, rt_pips)


def simulate(ohlcv, signals, cfg, pair, atr, swap=None):
    return C.simulate(ohlcv, signals, cfg, pair, atr, swap=swap)


def metrics(t: pd.DataFrame, col: str = "r") -> dict:
    m = C.metrics(t, col=col)
    lo, hi = block_ci(t[col] if t is not None and len(t) else pd.Series(dtype=float))
    m["mean_r_ci_lo"] = lo
    m["mean_r_ci_hi"] = hi
    return m


def block_ci(r: pd.Series, block: int = BOOT_BLOCK, draws: int = BOOT_DRAWS, seed: int = BOOT_SEED):
    r = np.asarray(pd.to_numeric(r, errors="coerce").dropna(), dtype=float)
    n = len(r)
    if n < block or n < 3:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    starts = np.arange(0, n - block + 1)
    nblocks = int(np.ceil(n / block))
    means = np.empty(draws)
    for d in range(draws):
        idx = []
        for _ in range(nblocks):
            s = int(rng.choice(starts))
            idx.extend(range(s, s + block))
        means[d] = r[np.asarray(idx[:n])].mean()
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def folds_positive(t: pd.DataFrame, col: str = "r") -> int:
    if t is None or len(t) == 0:
        return 0
    et = pd.to_datetime(t["entry_time"])
    return int(sum(1 for i in range(5) if t.loc[(et >= FOLD_EDGES[i]) & (et < FOLD_EDGES[i + 1]), col].sum() > 0))


def is_pass(m: dict, base: dict, folds_pos: int) -> dict:
    c = {
        "n>=100": m["n"] >= 100,
        "pf>=1.05": bool(m["pf"] >= 1.05),
        "pf>=base+0.05": bool(m["pf"] >= base["pf"] + 0.05),
        "net_r>0": m["net_r"] > 0,
        "dd<=net_r": abs(m["max_dd_r"]) <= m["net_r"],
        "p<alpha": bool(m["p"] < ALPHA),
        "folds>=3": folds_pos >= 3,
    }
    c["PASS"] = all(c.values())
    c["thin"] = 100 <= m["n"] < 300
    c["underpowered"] = m["n"] < 100
    return c


def ho_pass(m: dict, base: dict, n_cases: int) -> dict:
    c = {
        "n>=30": m["n"] >= 30,
        "pf>1": bool(m["pf"] > 1.0),
        "pf>=base+0.05": bool(m["pf"] >= base["pf"] + 0.05),
        "net_r>0": m["net_r"] > 0,
        "p<0.05/cases": bool(m["p"] < 0.05 / max(n_cases, 1)),
    }
    c["PASS"] = all(c.values())
    return c


def variant_signals(pred: pd.Series, st: pd.DataFrame) -> dict[str, pd.Series]:
    out = {"baseline": pred}
    for name in DIRECTIONAL:
        keep = L.agrees(pred, st[name], BUY, SELL)
        out[name] = pred.where(keep, HOLD)
    return out


def select_for_year(Y: int, baseline: pd.DataFrame, by_lens: dict[str, pd.DataFrame]) -> dict[str, str]:
    train_end = pd.Timestamp(f"{Y}-01-01")
    picks = {"unknown": "no-filter"}
    base = baseline[pd.to_datetime(baseline["entry_time"]) < train_end]
    for regime in ("low", "high"):
        breg = base[base["regime"] == regime]
        base_mean = float(breg["r"].mean()) if len(breg) else float("nan")
        best_name, best_mean = None, -np.inf
        for name in DIRECTIONAL:
            t = by_lens[name]
            t = t[(pd.to_datetime(t["entry_time"]) < train_end) & (t["regime"] == regime)]
            if len(t) < 30:
                continue
            m = float(t["r"].mean())
            if m > best_mean:
                best_mean = m
                best_name = name
        if best_name is None or not (best_mean > base_mean):
            picks[regime] = "no-filter"
        else:
            picks[regime] = best_name
    return picks


def best_signals(pred: pd.Series, st: pd.DataFrame, year_picks: dict[int, dict[str, str]]) -> pd.Series:
    """Apply year-Y selections to would-be entries at t+1h. Years before 2016 are unfiltered. 2026+ use frozen 2025."""
    out = pred.copy()
    entry = pd.DatetimeIndex(pred.index) + H
    for i, ts in enumerate(pred.index):
        e = entry[i]
        y = int(e.year)
        if y < 2016:
            continue
        if y > 2025:
            y = 2025
        picks = year_picks[y]
        reg = st["ATR_VOL"].iloc[i]
        if not isinstance(reg, str) or reg not in picks:
            choice = "no-filter"
        else:
            choice = picks[reg]
        if choice == "no-filter":
            continue
        tr = pd.to_numeric(pd.Series([st[choice].iloc[i]]), errors="coerce").iloc[0]
        sig = int(pred.iloc[i])
        ok = ((sig == BUY) and tr > 0) or ((sig == SELL) and tr < 0)
        if not ok:
            out.iloc[i] = HOLD
    return out


def attach_regime_and_lenses(trades: pd.DataFrame, st: pd.DataFrame) -> pd.DataFrame:
    if trades is None or len(trades) == 0:
        return trades
    t = trades.copy()
    dec = pd.DatetimeIndex(t["decision_time"])
    aligned = st.reindex(dec)
    t["regime"] = aligned["ATR_VOL"].to_numpy()
    for name in DIRECTIONAL + ["RSI", "SMA200_DIST_ATR"]:
        t[name] = aligned[name].to_numpy()
    return t


def sanity() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    base = research_cfg()
    bad = 0
    report = {}
    for pair in PRIMARY:
        df, preds = pair_inputs(pair)
        atr = true_range_atr(df, int(base.get("atr_period", 14)))
        rt = JUDGED_COSTS[pair]
        t = simulate(df, preds["pred"].astype(int), cfg_with_cost(base, rt), pair, atr)
        exp_n, exp_r = SANITY[pair]
        ok = len(t) == exp_n and abs(t["r"].sum() - exp_r) < 0.0015
        bad += 0 if ok else 1
        line = f"sanity {pair}: n={len(t)} (saved {exp_n}) total R={t['r'].sum():.3f} (saved {exp_r:.3f}) -> {'OK' if ok else 'MISMATCH'}"
        print(line, flush=True)
        report[pair] = {"n": int(len(t)), "total_r": float(t["r"].sum()), "expected_n": exp_n, "expected_r": exp_r, "ok": bool(ok)}
        # also vs saved trades file
        old = pd.read_csv(OLD / f"{pair}_trades.csv", parse_dates=["entry_time"])
        old = old[(old["variant"] == "baseline") & (old["cost"] == "p90")]
        ok2 = len(t) == len(old) and abs(t["r"].sum() - old["r"].sum()) < 1e-6 * max(1.0, abs(old["r"].sum()))
        print(f"  vs saved trades csv: n={len(old)} R={old['r'].sum():.3f} -> {'OK' if ok2 else 'MISMATCH'}", flush=True)
        report[pair]["saved_csv_ok"] = bool(ok2)
        bad += 0 if ok2 else 1
    (OUT / "sanity.json").write_text(json.dumps({"ok": bad == 0, "pairs": report}, indent=2), encoding="utf-8")
    return bad


def run_pair(pair: str, base: dict, swaps) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    done = OUT / f"trades_{pair}.csv"
    if done.exists():
        print(pair, "done already", flush=True)
        return
    df, preds = pair_inputs(pair)
    atr = true_range_atr(df, int(base.get("atr_period", 14)))
    pred = preds["pred"].astype(int)
    st = L.lenses_at_decisions(df, pd.DatetimeIndex(preds.index))
    st.to_csv(OUT / f"lenses_{pair}.csv")
    print(pair, "lenses written", flush=True)
    sigs = variant_signals(pred, st)
    cfg = cfg_with_cost(base, JUDGED_COSTS[pair])
    swap = swaps.get(pair) if swaps else None
    by_lens: dict[str, pd.DataFrame] = {}
    rows = []
    for v, s in sigs.items():
        t = simulate(df, s, cfg, pair, atr, swap=swap)
        t = attach_regime_and_lenses(t, st)
        if v != "baseline":
            by_lens[v] = t
        else:
            baseline = t
        if len(t):
            rows.append(t.assign(variant=v, cost="judged", pair=pair))
        print(pair, v, "n=", len(t), flush=True)
    year_picks = {str(Y): select_for_year(Y, baseline, by_lens) for Y in WF_YEARS}
    (OUT / f"selections_{pair}.json").write_text(json.dumps(year_picks, indent=2), encoding="utf-8")
    picks_int = {int(k): v for k, v in year_picks.items()}
    best = best_signals(pred, st, picks_int)
    tb = simulate(df, best, cfg, pair, atr, swap=swap)
    tb = attach_regime_and_lenses(tb, st)
    if len(tb):
        rows.append(tb.assign(variant="BEST_PER_REGIME", cost="judged", pair=pair))
    print(pair, "BEST_PER_REGIME n=", len(tb), flush=True)
    pd.concat(rows, ignore_index=True).to_csv(done, index=False)
    print(pair, "written", flush=True)


def load_swaps():
    try:
        return C.load_swaps()
    except Exception as e:
        print("swap snapshot unavailable:", e, flush=True)
        return {}


def run_all() -> None:
    base = research_cfg()
    swaps = load_swaps()
    for pair in PRIMARY:
        run_pair(pair, base, swaps)


def _load_trades() -> pd.DataFrame:
    parts = [pd.read_csv(OUT / f"trades_{p}.csv", parse_dates=["entry_time", "decision_time", "exit_time"]) for p in PRIMARY]
    return pd.concat(parts, ignore_index=True)


def _window(tr: pd.DataFrame, window: str, variant: str | None = None) -> pd.DataFrame:
    t = tr
    if variant is not None:
        t = t[t["variant"] == variant]
    et = pd.to_datetime(t["entry_time"])
    if window == "insample":
        if variant == "BEST_PER_REGIME":
            return t[(et >= BEST_START) & (et < CUTOFF)]
        return t[et < CUTOFF]
    if window == "holdout":
        return t[et >= CUTOFF]
    return t


def _base_window(tr: pd.DataFrame, window: str, variant: str) -> pd.DataFrame:
    b = _window(tr, window, "baseline")
    if window == "insample" and variant == "BEST_PER_REGIME":
        et = pd.to_datetime(b["entry_time"])
        return b[(et >= BEST_START) & (et < CUTOFF)]
    return b


def evaluate(window: str) -> tuple[str, dict]:
    tr = _load_trades()
    lines = [f"# Phase 1 {window} tables", ""]
    decisions = {}
    for pair in PRIMARY:
        lines.append(f"## {pair}")
        lines.append("")
        ptr = tr[tr["pair"] == pair]
        lines.append(
            "| Variant | Trades | PF | Mean R [95% CI] | Net R | Max DD | Win | p | Folds+ | vs base n | Flag | PASS |"
        )
        lines.append("|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|")
        for v in ["baseline"] + VARIANTS:
            t = _window(ptr, window, v)
            m = metrics(t)
            if v == "baseline":
                base_m = m
                folds = folds_positive(t)
                flag = ""
                decision = {"metrics": m, "folds": folds, "pass": {"PASS": False, "n/a": "baseline"}}
                vs = "—"
                pas = "—"
            else:
                b = _base_window(ptr, window, v)
                base_m = metrics(b)
                folds = folds_positive(t)
                if window == "insample":
                    decision = {"metrics": m, "folds": folds, "base": base_m, "pass": is_pass(m, base_m, folds)}
                    pas = "YES" if decision["pass"]["PASS"] else "no"
                else:
                    decision = {"metrics": m, "folds": folds, "base": base_m}
                    pas = "—"
                vs = str(m["n"] - base_m["n"])
                flag = "UNDERPOWERED" if m["n"] < 100 else ("thin" if m["n"] < 300 else "")
            ci = f"{m['mean_r']:.4f} [{m['mean_r_ci_lo']:.4f}, {m['mean_r_ci_hi']:.4f}]" if np.isfinite(m.get("mean_r", np.nan)) else "n/a"
            pf = f"{m['pf']:.3f}" if np.isfinite(m.get("pf", np.nan)) else "n/a"
            p = f"{m['p']:.4g}" if np.isfinite(m.get("p", np.nan)) else "n/a"
            lines.append(
                f"| {v} | {m['n']} | {pf} | {ci} | {m['net_r']:.2f} | {m['max_dd_r']:.2f} | "
                f"{m['win']:.3f} | {p} | {folds} | {vs} | {flag} | {pas} |"
            )
            decisions[(pair, v)] = decision
        lines.append("")
    return "\n".join(lines) + "\n", decisions


def diagnostic_tables(window: str = "insample") -> str:
    tr = _load_trades()
    lines = ["# Diagnostic (not a trial, cannot pass)", ""]
    for pair in PRIMARY:
        ptr = tr[(tr["pair"] == pair) & (tr["variant"] == "baseline")]
        t = _window(ptr, window, "baseline") if "variant" in ptr.columns else ptr
        # _window already filtered variant if we pass whole tr
        t = _window(tr[tr["pair"] == pair], window, "baseline")
        lines.append(f"## {pair} baseline splits")
        lines.append("")
        # agree / disagree / flat per lens
        lines.append("### Agreement buckets (baseline trades, lens at decision)")
        lines.append("| Lens | Bucket | n | PF | Mean R | Net R |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for name in DIRECTIONAL:
            side = t["side"] if "side" in t.columns else pd.Series(dtype=float)
            lens = pd.to_numeric(t[name], errors="coerce") if name in t.columns else pd.Series(np.nan, index=t.index)
            agree = ((side == 1) & (lens > 0)) | ((side == -1) & (lens < 0))
            oppose = ((side == 1) & (lens < 0)) | ((side == -1) & (lens > 0))
            buckets = np.where(agree, "agree", np.where(oppose, "oppose", "flat_or_na"))
            for bname in ("agree", "oppose", "flat_or_na"):
                sub = t[buckets == bname]
                m = metrics(sub)
                pf = f"{m['pf']:.3f}" if np.isfinite(m.get("pf", np.nan)) else "n/a"
                mr = f"{m['mean_r']:.4f}" if np.isfinite(m.get("mean_r", np.nan)) else "n/a"
                lines.append(f"| {name} | {bname} | {m['n']} | {pf} | {mr} | {m['net_r']:.2f} |")
        lines.append("")
        # ATR regime
        lines.append("### ATR_VOL regime (baseline)")
        lines.append("| Regime | n | PF | Mean R | Net R |")
        lines.append("|---|---:|---:|---:|---:|")
        for reg in ("low", "high", "unknown"):
            sub = t[t["regime"] == reg] if "regime" in t.columns else t.iloc[0:0]
            m = metrics(sub)
            pf = f"{m['pf']:.3f}" if np.isfinite(m.get("pf", np.nan)) else "n/a"
            mr = f"{m['mean_r']:.4f}" if np.isfinite(m.get("mean_r", np.nan)) else "n/a"
            lines.append(f"| {reg} | {m['n']} | {pf} | {mr} | {m['net_r']:.2f} |")
        lines.append("")
        # In-sample best lens per regime (diagnostic only)
        lines.append("### In-sample best lens per ATR regime (diagnostic only, not a pass)")
        lines.append("| Regime | Lens | n | Mean R | PF |")
        lines.append("|---|---|---:|---:|---:|")
        for reg in ("low", "high"):
            best = None
            for name in DIRECTIONAL:
                vt = _window(tr[(tr["pair"] == pair) & (tr["variant"] == name)], window)
                vt = vt[vt["regime"] == reg] if "regime" in vt.columns else vt.iloc[0:0]
                m = metrics(vt)
                row = (name, m)
                if best is None or (np.isfinite(m["mean_r"]) and m["mean_r"] > best[1]["mean_r"]):
                    best = row
                pf = f"{m['pf']:.3f}" if np.isfinite(m.get("pf", np.nan)) else "n/a"
                mr = f"{m['mean_r']:.4f}" if np.isfinite(m.get("mean_r", np.nan)) else "n/a"
                lines.append(f"| {reg} | {name} | {m['n']} | {mr} | {pf} |")
        lines.append("")
        # RSI extremes
        if "RSI" in t.columns:
            rsi = pd.to_numeric(t["RSI"], errors="coerce")
            buckets = np.where(rsi >= 70, "rsi>=70", np.where(rsi <= 30, "rsi<=30", np.where(rsi.notna(), "30<rsi<70", "na")))
            lines.append("### RSI extremes (baseline, diagnostic)")
            lines.append("| Bucket | n | PF | Mean R | Net R |")
            lines.append("|---|---:|---:|---:|---:|")
            for bname in ("rsi>=70", "30<rsi<70", "rsi<=30", "na"):
                sub = t[buckets == bname]
                m = metrics(sub)
                pf = f"{m['pf']:.3f}" if np.isfinite(m.get("pf", np.nan)) else "n/a"
                mr = f"{m['mean_r']:.4f}" if np.isfinite(m.get("mean_r", np.nan)) else "n/a"
                lines.append(f"| {bname} | {m['n']} | {pf} | {mr} | {m['net_r']:.2f} |")
            lines.append("")
        # SMA200 distance
        if "SMA200_DIST_ATR" in t.columns:
            d = pd.to_numeric(t["SMA200_DIST_ATR"], errors="coerce")
            buckets = np.where(d.isna(), "na", np.where(d.abs() > 2.0, "|dist|>2ATR", "|dist|<=2ATR"))
            lines.append("### Distance from SMA200 in ATR (baseline, diagnostic)")
            lines.append("| Bucket | n | PF | Mean R | Net R |")
            lines.append("|---|---:|---:|---:|---:|")
            for bname in ("|dist|<=2ATR", "|dist|>2ATR", "na"):
                sub = t[buckets == bname]
                m = metrics(sub)
                pf = f"{m['pf']:.3f}" if np.isfinite(m.get("pf", np.nan)) else "n/a"
                mr = f"{m['mean_r']:.4f}" if np.isfinite(m.get("mean_r", np.nan)) else "n/a"
                lines.append(f"| {bname} | {m['n']} | {pf} | {mr} | {m['net_r']:.2f} |")
            lines.append("")
        # year selections
        sel_path = OUT / f"selections_{pair}.json"
        if sel_path.exists():
            sel = json.loads(sel_path.read_text(encoding="utf-8"))
            lines.append("### BEST_PER_REGIME walk-forward selections")
            lines.append("| Year | Low-vol lens | High-vol lens |")
            lines.append("|---|---|---|")
            for Y in WF_YEARS:
                p = sel[str(Y)]
                lines.append(f"| {Y} | {p.get('low')} | {p.get('high')} |")
            lines.append("")
    return "\n".join(lines) + "\n"


def phase_insample() -> dict:
    text, decisions = evaluate("insample")
    (OUT / "insample_tables.md").write_text(text, encoding="utf-8")
    diag = diagnostic_tables("insample")
    (OUT / "insample_diagnostics.md").write_text(diag, encoding="utf-8")
    # serialize decisions (tuple keys)
    ser = {}
    passes = []
    for (pair, v), d in decisions.items():
        ser[f"{pair}:{v}"] = _jsonable(d)
        if v != "baseline" and d.get("pass", {}).get("PASS"):
            passes.append((pair, v))
    (OUT / "insample_decisions.json").write_text(json.dumps({"passes": [list(p) for p in passes], "details": ser}, indent=2), encoding="utf-8")
    print(text)
    print(f"In-sample passes: {passes if passes else 'none'}", flush=True)
    return {"passes": passes, "decisions": decisions}


def phase_holdout() -> None:
    dec_path = OUT / "insample_decisions.json"
    if not dec_path.exists():
        print("No in-sample decisions; run --phase insample first.", flush=True)
        return
    ins = json.loads(dec_path.read_text(encoding="utf-8"))
    passes = [tuple(p) for p in ins.get("passes", [])]
    if not passes:
        print("No in-sample pass. Holdout is not scored, per the prereg.", flush=True)
        (OUT / "holdout_tables.md").write_text(
            "# Phase 1 holdout\n\nNot scored: no case passed in-sample.\n", encoding="utf-8"
        )
        return
    text, decisions = evaluate("holdout")
    n_cases = len(passes)
    lines = [text, "", f"Holdout pass checks ({n_cases} in-sample pass cases):", ""]
    overall = []
    for pair, v in passes:
        d = decisions[(pair, v)]
        hp = ho_pass(d["metrics"], d["base"], n_cases)
        lines.append(f"- {pair} {v}: {hp}")
        overall.append({"pair": pair, "variant": v, "holdout_pass": hp, "overall_pass": bool(hp["PASS"])})
    (OUT / "holdout_tables.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "holdout_decisions.json").write_text(json.dumps(overall, indent=2, default=str), encoding="utf-8")
    print("\n".join(lines), flush=True)


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return v if np.isfinite(v) else None
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    return x


def paper_shadow_appendix() -> str:
    path = ROOT / "data" / "paper_shadow" / "journal.jsonl"
    if not path.exists():
        return "Paper-shadow journal not found. Appendix omitted.\n"
    opens, closes = {}, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        ev = r.get("event") or r.get("type")
        pair = r.get("pair") or r.get("symbol")
        if ev == "open":
            key = r.get("id") or r.get("trade_id") or r.get("position_id") or (pair, r.get("opened_at") or r.get("time"))
            opens[str(key)] = r
        elif ev == "close":
            closes.append(r)
    lines = [
        "# Paper-shadow journal (descriptive only, not a trial, cannot pass)",
        "",
        "Existing journal rows only. No number is invented. After-cost R is used only when the journal already stores it.",
        "",
    ]
    # count per pair
    from collections import Counter

    oc = Counter()
    cc = Counter()
    for r in opens.values():
        oc[r.get("pair") or r.get("symbol") or "?"] += 1
    for r in closes:
        cc[r.get("pair") or r.get("symbol") or "?"] += 1
    lines.append("| Pair | Opens | Closes |")
    lines.append("|---|---:|---:|")
    for p in PRIMARY:
        lines.append(f"| {p} | {oc.get(p, 0)} | {cc.get(p, 0)} |")
    lines.append("")
    lines.append(
        f"These counts are underpowered (prereg expected single-digit to low-teens closed trades per pair). "
        f"Total journal opens={sum(oc.values())}, closes={sum(cc.values())}."
    )
    lines.append("")
    # if a stored r exists, list it
    sample_keys = sorted({k for r in closes for k in r.keys()})
    lines.append(f"Close-row keys present: {', '.join(sample_keys)}.")
    lines.append("")
    r_fields = [k for k in ("r", "r_net", "sum_r", "pnl_r", "net_r", "R") if any(k in r for r in closes)]
    for p in PRIMARY:
        rows = [r for r in closes if (r.get("pair") or r.get("symbol")) == p]
        lines.append(f"## {p} closed rows ({len(rows)})")
        if r_fields:
            for k in r_fields:
                vals = [r[k] for r in rows if k in r and isinstance(r[k], (int, float))]
                lines.append(f"- Stored `{k}` on {len(vals)} of {len(rows)} closes; sum={sum(vals) if vals else 'n/a'}.")
        else:
            lines.append("- No stored R field on close rows; no R is computed here.")
        lines.append("")
    return "\n".join(lines) + "\n"


def phase_report() -> Path:
    """Assemble RESULTS_20261112.md from already-written intermediate files."""
    parts = []
    parts.append("# Phase 1 results: daily lens library — 2026-11-12")
    parts.append("")
    parts.append("Paper research only. Prereg: `research/phase1/PREREG_20261112.md` (commit 218e3e5), committed and pushed before any result was computed.")
    parts.append(f"Trial count N = {N_PRIOR} + {len(VARIANTS)} = **{N_TRIALS}**. Alpha = 0.05/{N_TRIALS} = {ALPHA:.6g}.")
    parts.append("Judged costs: EURUSD 1.4 pips, USDJPY 2.4 pips round trip. Pairs judged individually. Nothing live changed.")
    parts.append("")
    san = OUT / "sanity.json"
    if san.exists():
        s = json.loads(san.read_text(encoding="utf-8"))
        parts.append("## Sanity")
        parts.append("")
        parts.append("Baseline re-simulation at the judged costs versus the saved 2026-10-20 / 2026-11-03 numbers (EURUSD 3153 trades, -80.729 R; USDJPY 2005 trades, -154.674 R).")
        parts.append("")
        for pair, r in s.get("pairs", {}).items():
            parts.append(
                f"- {pair}: n={r['n']} (expected {r['expected_n']}), total R={r['total_r']:.3f} (expected {r['expected_r']:.3f}), "
                f"harness {'OK' if r['ok'] else 'MISMATCH'}, saved-csv {'OK' if r.get('saved_csv_ok') else 'MISMATCH'}."
            )
        parts.append("")
        if not s.get("ok"):
            parts.append("Sanity failed. Variant results below must not be used for a pass.")
            parts.append("")
    ins = OUT / "insample_tables.md"
    if ins.exists():
        parts.append("## In-sample (entries before 2025-10-01; BEST_PER_REGIME from 2016-01-01)")
        parts.append("")
        body = ins.read_text(encoding="utf-8")
        # drop the first heading, already have one
        if body.startswith("# "):
            body = "\n".join(body.splitlines()[1:]).lstrip("\n")
        parts.append(body)
    dec = OUT / "insample_decisions.json"
    passes = []
    if dec.exists():
        insd = json.loads(dec.read_text(encoding="utf-8"))
        passes = insd.get("passes", [])
        parts.append("## In-sample pass / fail")
        parts.append("")
        if passes:
            parts.append("Cases that cleared the in-sample bar: " + ", ".join(f"{p[0]} {p[1]}" for p in passes) + ".")
        else:
            parts.append("**No case passed in-sample.** Holdout was not scored. Nothing is proposed for a forward paper shadow.")
        parts.append("")
        # ban list
        failed = [v for v in VARIANTS if not any(p[1] == v and p[0] in PRIMARY for p in passes)]
        # more precise: ban a variant if neither pair passed? Prereg says failed variants are banned.
        # A variant that failed on both pairs, or that was judged and didn't overall pass.
        # I'll ban each (pair, variant) that didn't pass, and ban the variant family if it has no overall pass.
        parts.append("Failed judged variants (banned; no overall PASS): " + ", ".join(VARIANTS) + "." if not passes else "")
        if not passes:
            parts.append("")
    ho = OUT / "holdout_tables.md"
    if ho.exists():
        parts.append("## Holdout")
        parts.append("")
        parts.append(ho.read_text(encoding="utf-8"))
    diag = OUT / "insample_diagnostics.md"
    if diag.exists():
        parts.append(diag.read_text(encoding="utf-8"))
    app = OUT / "paper_shadow_appendix.md"
    if app.exists():
        parts.append(app.read_text(encoding="utf-8"))
    parts.append("## Guard rails")
    parts.append("")
    parts.append("No yaml, gate, model, journal, or desk change. No `--promote`, no `/pipeline/*` or `/model/train/*`. Failed variants are banned. No live enablement.")
    parts.append("")
    dest = ROOT / "research" / "phase1" / "RESULTS_20261112.md"
    dest.write_text("\n".join(parts).replace("\n\n\n", "\n\n"), encoding="utf-8")
    print("wrote", dest, flush=True)
    return dest


def phase_appendix() -> None:
    text = paper_shadow_appendix()
    (OUT / "paper_shadow_appendix.md").write_text(text, encoding="utf-8")
    print(text, flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sanity", action="store_true")
    p.add_argument("--phase", choices=["run", "insample", "holdout", "report", "appendix"])
    args = p.parse_args()
    if args.sanity:
        bad = sanity()
        return 1 if bad else 0
    if args.phase == "run":
        san = OUT / "sanity.json"
        if not san.exists() or not json.loads(san.read_text(encoding="utf-8")).get("ok"):
            print("Sanity is missing or failed. Run --sanity first and do not read variants on a fail.", flush=True)
            return 1
        run_all()
        return 0
    if args.phase == "insample":
        phase_insample()
        return 0
    if args.phase == "holdout":
        phase_holdout()
        return 0
    if args.phase == "appendix":
        phase_appendix()
        return 0
    if args.phase == "report":
        phase_report()
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
