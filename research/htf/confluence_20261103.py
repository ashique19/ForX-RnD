"""Multi-timeframe confluence filter on the main-book 1h entries (prereg research/new_families/PREREG_20261103.md, commit a2ec0d0).

python research/htf/confluence_20261103.py --sanity            baseline reproduction vs the saved 2026-10-20 trades (no variants read)
python research/htf/confluence_20261103.py --phase run         simulate baseline + 7 variants for 10 pairs -> data/edge_check/confluence_20261103/
python research/htf/confluence_20261103.py --phase insample    tables + decisions (writes insample_report.md)
python research/htf/confluence_20261103.py --phase holdout     scored ONCE, only for in-sample passes
Research only: reads saved predictions, data/history, the cost table and the MT5 swap snapshot. No retrain, no yaml/model/journal writes.
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
for _p in (ROOT, ROOT / "research" / "edge_check"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M = _load("htf_wf_20261020", ROOT / "research" / "htf" / "daily_trend_flip_wf_20261020.py")
TH = _load("trend_hold_20261020", ROOT / "research" / "htf" / "trend_hold_20261020.py")
from forex_lab import cost_model as cm  # noqa: E402
from forex_lab.backtest import _barrier_distances, _first_touch_exit, _spread_cost_frac, true_range_atr  # noqa: E402
from forex_lab.features import INV_LABEL_MAP  # noqa: E402

PAIRS, HOLD, BUY, SELL = M.PAIRS, M.HOLD, M.BUY, M.SELL
PRIMARY = ["EURUSD", "USDJPY"]
OLD = M.OUT  # saved 2026-10-20 predictions and trades
OUT = ROOT / "data" / "edge_check" / "confluence_20261103"
SNAP = ROOT / "data" / "research" / "swaps" / "mt5_swaps_20261021_1813Z.json"
CUTOFF = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
N_TRIALS = 174
ALPHA = 0.05 / N_TRIALS
FOLD_EDGES = pd.date_range("2015-01-01", "2025-10-01", periods=6)
VARIANTS = ["H4_EMA50", "D_EMA50", "H4_AND_D", "H4_D_CONF65", "H4_STRUCT", "H4_D_DISAGREE", "CONF65"]
JUDGED_VARIANTS = ["H4_EMA50", "D_EMA50", "H4_AND_D", "H4_D_CONF65", "H4_STRUCT", "CONF65"]  # H4_D_DISAGREE is a sanity check
assert N_TRIALS == 167 + len(VARIANTS)
H = pd.Timedelta(hours=1)


# ------------------------------------------------------------------ higher-timeframe bars (completed bars only)
def h4_bars(h1: pd.DataFrame) -> pd.DataFrame:
    """4h bins starting 01/05/09/13/17/21 UTC; indexed by bin start; 'end' = start + 4h. Bins with < 2 hourly bars dropped."""
    key = (h1.index - H).floor("4h") + H
    g = h1.groupby(key)
    b = pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last(), "n": g.size()})
    b = b[b["n"] >= 2].copy()
    b["end"] = b.index + 4 * H
    return b


def ema_trend(close: pd.Series, span: int = 50) -> pd.Series:
    ema = close.ewm(span=span, adjust=False, min_periods=span).mean()
    return np.sign(close - ema).where(ema.notna())


def swing_state(b: pd.DataFrame, k: int = 2) -> pd.Series:
    """+1 HH&HL, -1 LH&LL, 0 otherwise, NaN until two confirmed swings of each kind. Pivot i is confirmed when bar i+k has completed."""
    hi, lo, n = b["High"].to_numpy(), b["Low"].to_numpy(), len(b)
    sh, sl = np.zeros(n, bool), np.zeros(n, bool)
    for i in range(k, n - k):
        sh[i] = hi[i] > hi[i - k:i].max() and hi[i] > hi[i + 1:i + k + 1].max()
        sl[i] = lo[i] < lo[i - k:i].min() and lo[i] < lo[i + 1:i + k + 1].min()
    out = np.full(n, np.nan)
    highs, lows = [], []
    for j in range(n):
        i = j - k
        if i >= k:
            if sh[i]:
                highs.append(hi[i])
            if sl[i]:
                lows.append(lo[i])
        if len(highs) >= 2 and len(lows) >= 2:
            up = highs[-1] > highs[-2] and lows[-1] > lows[-2]
            dn = highs[-1] < highs[-2] and lows[-1] < lows[-2]
            out[j] = 1.0 if up else (-1.0 if dn else 0.0)
    return pd.Series(out, index=b.index)


def daily_with_trend(h1: pd.DataFrame) -> pd.DataFrame:
    d = M.daily_bars(h1)[["Close", "hours", "end_utc"]].copy()
    d["td"] = ema_trend(d["Close"])
    return d


def htf_at(bar_end: pd.DatetimeIndex, values: np.ndarray, decide: pd.DatetimeIndex) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Value of the latest higher-timeframe bar whose END is at or before each decision time (completed bars only)."""
    pos = bar_end.searchsorted(np.asarray(decide, dtype="datetime64[ns]"), side="right") - 1
    ok = pos >= 0
    p = np.clip(pos, 0, None)
    val = np.where(ok, np.asarray(values, dtype=float)[p], np.nan)
    used = pd.DatetimeIndex(np.where(ok, bar_end.values[p], np.datetime64("NaT")))
    return val, used


def trend_states(h1: pd.DataFrame, sig_index: pd.DatetimeIndex) -> pd.DataFrame:
    """T4, TD, TS known at each 1h signal bar t (decision at t + 1h, when bar t has closed)."""
    decide = pd.DatetimeIndex(sig_index) + H
    b4 = h4_bars(h1)
    t4, e4 = htf_at(pd.DatetimeIndex(b4["end"]), ema_trend(b4["Close"]).to_numpy(), decide)
    ts, es = htf_at(pd.DatetimeIndex(b4["end"]), swing_state(b4).to_numpy(), decide)
    d = daily_with_trend(h1)
    td, ed = htf_at(pd.DatetimeIndex(d["end_utc"]), d["td"].to_numpy(), decide)
    return pd.DataFrame({"t4": t4, "td": td, "ts": ts, "end4": e4, "endS": es, "endD": ed, "decide": decide}, index=sig_index)


def agree(pred: pd.Series, trend: pd.Series) -> pd.Series:
    tr = trend.reindex(pred.index)
    return ((pred == BUY) & (tr > 0)) | ((pred == SELL) & (tr < 0))


def oppose(pred: pd.Series, trend: pd.Series) -> pd.Series:
    tr = trend.reindex(pred.index)
    return ((pred == BUY) & (tr < 0)) | ((pred == SELL) & (tr > 0))


def variant_signals(pred: pd.Series, conf: pd.Series, st: pd.DataFrame) -> dict[str, pd.Series]:
    a4, ad, aS = agree(pred, st["t4"]), agree(pred, st["td"]), agree(pred, st["ts"])
    hc = conf.reindex(pred.index) >= 0.65
    keep = {"H4_EMA50": a4, "D_EMA50": ad, "H4_AND_D": a4 & ad, "H4_D_CONF65": a4 & ad & hc, "H4_STRUCT": aS,
            "H4_D_DISAGREE": oppose(pred, st["t4"]) & oppose(pred, st["td"]), "CONF65": hc}
    out = {"baseline": pred}
    for k, m in keep.items():
        out[k] = pred.where(m, HOLD)
    return out


def bucket(pred: pd.Series, st: pd.DataFrame) -> pd.Series:
    a = agree(pred, st["t4"]) & agree(pred, st["td"])
    o = oppose(pred, st["t4"]) & oppose(pred, st["td"])
    return pd.Series(np.where(a, "both_agree", np.where(o, "both_oppose", "mixed")), index=pred.index)


# ------------------------------------------------------------------ simulation (== forex_lab.backtest._simulate_trades; tested)
def load_swaps() -> dict[str, tuple[float, float]]:
    s = json.loads(SNAP.read_text())
    return {r["pair"]: (float(r["swap_long"]), float(r["swap_short"])) for r in s["rows"]}


def simulate(ohlcv: pd.DataFrame, signals: pd.Series, cfg: dict, pair: str, atr: pd.Series, swap: tuple[float, float] | None = None) -> pd.DataFrame:
    horizon = int(cfg.get("horizon", 8))
    path = str((cfg.get("barrier") or {}).get("path", "high_low")).lower()
    sl_atr = float((cfg.get("barrier") or {}).get("sl_atr", 2.0))
    index, n = ohlcv.index, len(ohlcv)
    opn = ohlcv["Open"].to_numpy()
    pos_of = pd.Series(np.arange(n), index=index)
    rows, next_free = [], 0
    for ts, s in signals.items():
        sig = int(s)
        if sig == HOLD or ts not in pos_of.index:
            continue
        loc = int(pos_of[ts])
        if loc < next_free:
            continue
        e = loc + 1
        if e >= n:
            continue
        atr_e = float(atr.iloc[loc])
        if not np.isfinite(atr_e) or atr_e <= 0:
            continue
        entry = float(opn[e])
        side = 1 if sig == BUY else -1
        cost = _spread_cost_frac(pair, cfg, entry)
        tp_d, sl_d = _barrier_distances(atr_e, cfg)
        x_loc, x_px, reason = _first_touch_exit(ohlcv, e, horizon, side, entry, tp_d, sl_d, path)
        net = side * (x_px / entry - 1.0) - cost
        rows.append({"entry_time": index[e], "decision_time": index[loc], "exit_time": index[x_loc] + H, "side": side, "entry": entry,
                     "net_return": net, "exit_reason": reason, "atr": atr_e, "r": net * entry / (sl_atr * atr_e)})
        next_free = x_loc + 1
    t = pd.DataFrame(rows)
    if len(t):
        inst, cumw = TH.rollover_weights(index[0], index[-1])
        nights = TH.nights(t["entry_time"], t["exit_time"], inst, cumw)
        t["nights"] = nights
        if swap is not None:
            rate = np.where(t["side"] == 1, swap[0], swap[1]) / 100.0 / 360.0
            t["swap_ret"] = nights * rate
        else:
            t["swap_ret"] = 0.0
        t["r_swap"] = (t["net_return"] + t["swap_ret"]) * t["entry"] / (sl_atr * t["atr"])
    return t


# ------------------------------------------------------------------ metrics and rules
def metrics(t: pd.DataFrame, col: str = "r") -> dict:
    if t is None or len(t) == 0:
        return {"n": 0, "pf": float("nan"), "net_r": 0.0, "max_dd_r": 0.0, "win": float("nan"), "p": float("nan"), "mean_r": float("nan")}
    t = t.sort_values("entry_time")
    r = t[col].astype(float)
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    eq = r.cumsum()
    dd = min(float((eq - eq.cummax().clip(lower=0)).min()), 0.0)
    p = float(stats.ttest_1samp(r, 0.0, alternative="greater").pvalue) if len(r) > 2 and r.std() > 0 else float("nan")
    return {"n": int(len(r)), "pf": float(gp / gl) if gl > 0 else float("inf"), "net_r": float(r.sum()), "max_dd_r": dd,
            "win": float((r > 0).mean()), "p": p, "mean_r": float(r.mean())}


def folds_positive(t: pd.DataFrame, col: str = "r_swap") -> int:
    if t is None or len(t) == 0:
        return 0
    et = pd.to_datetime(t["entry_time"])
    return int(sum(1 for i in range(5) if t.loc[(et >= FOLD_EDGES[i]) & (et < FOLD_EDGES[i + 1]), col].sum() > 0))


def is_pass(m: dict, base: dict, folds_pos: int, ctrl: dict | None = None) -> dict:
    c = {"n>=100": m["n"] >= 100, "pf>=1.05": bool(m["pf"] >= 1.05), "pf>=base+0.05": bool(m["pf"] >= base["pf"] + 0.05),
         "net_r>0": m["net_r"] > 0, "dd<=net_r": abs(m["max_dd_r"]) <= m["net_r"], "p<alpha": bool(m["p"] < ALPHA), "folds>=3": folds_pos >= 3}
    if ctrl is not None:
        c["pf>=ctrl+0.03"] = bool(m["pf"] >= ctrl["pf"] + 0.03)
    c["PASS"] = all(c.values())
    return c


def ho_pass(m: dict, base: dict, n_cases: int) -> dict:
    c = {"n>=30": m["n"] >= 30, "pf>1": bool(m["pf"] > 1.0), "pf>=base+0.05": bool(m["pf"] >= base["pf"] + 0.05), "net_r>0": m["net_r"] > 0,
         "p<0.05/cases": bool(m["p"] < 0.05 / max(n_cases, 1))}
    c["PASS"] = all(c.values())
    return c


# ------------------------------------------------------------------ phases
def research_cfg() -> dict:
    return M.research_cfg()


def pair_inputs(pair: str):
    import edge_lib as L
    df = L.load_history(pair)[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    preds = pd.read_csv(OLD / f"{pair}_preds.csv", index_col=0, parse_dates=True)
    return df, preds


def cfg_with_cost(base: dict, rt_pips: float) -> dict:
    c = copy.deepcopy(base)
    c["spread_pips"] = float(rt_pips)
    c["commission_pips"] = 0.0
    return c


def run_pair(pair: str, base: dict, rt: dict[str, float], swaps) -> pd.DataFrame:
    df, preds = pair_inputs(pair)
    atr = true_range_atr(df, int(base.get("atr_period", 14)))
    pred = preds["pred"].astype(int)
    st = trend_states(df, pd.DatetimeIndex(preds.index))
    sigs = variant_signals(pred, preds["confidence"], st)
    bk = bucket(pred, st)
    out = []
    for cost_name, rtp in (("measured", rt[pair]), ("ref1", 1.0)):
        cfg = cfg_with_cost(base, rtp)
        for v, s in sigs.items():
            t = simulate(df, s, cfg, pair, atr, swap=swaps[pair] if cost_name == "measured" else None)
            if len(t):
                t["bucket"] = bk.reindex(pd.DatetimeIndex(t["decision_time"])).to_numpy()
                out.append(t.assign(variant=v, cost=cost_name, pair=pair))
    return pd.concat(out, ignore_index=True)


def sanity() -> int:
    base = research_cfg()
    rt, miss = cm.cost_series(PAIRS, "mt5_measured")
    bad = 0
    for pair in PAIRS:
        df, preds = pair_inputs(pair)
        atr = true_range_atr(df, int(base.get("atr_period", 14)))
        t = simulate(df, preds["pred"].astype(int), cfg_with_cost(base, rt[pair]), pair, atr)
        old = pd.read_csv(OLD / f"{pair}_trades.csv", parse_dates=["entry_time"])
        old = old[(old["variant"] == "baseline") & (old["cost"] == "p90")]
        ok = len(t) == len(old) and abs(t["r"].sum() - old["r"].sum()) < 1e-6 * max(1.0, abs(old["r"].sum()))
        bad += 0 if ok else 1
        print(f"sanity {pair}: n={len(t)} (saved {len(old)}) total R={t['r'].sum():.3f} (saved {old['r'].sum():.3f}) -> {'OK' if ok else 'MISMATCH'}", flush=True)
    return bad


def run_all(pairs: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = research_cfg()
    rt, miss = cm.cost_series(PAIRS, "mt5_measured")
    assert not miss, miss
    swaps = load_swaps()
    for pair in pairs:
        f = OUT / f"trades_{pair}.csv"
        if f.exists():
            print(pair, "done already", flush=True)
            continue
        run_pair(pair, base, rt, swaps).to_csv(f, index=False)
        print(pair, "written", flush=True)


def _load_trades(window: str) -> pd.DataFrame:
    tr = pd.concat([pd.read_csv(OUT / f"trades_{p}.csv", parse_dates=["entry_time", "decision_time", "exit_time"]) for p in PAIRS], ignore_index=True)
    return tr[tr["entry_time"] < CUTOFF] if window == "insample" else tr[tr["entry_time"] >= CUTOFF]


def _f(x, nd=3):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else (f"{x:.2e}" if isinstance(x, float) and 0 < abs(x) < 1e-3 else f"{x:.{nd}f}")


def _sel(tr, variant, cost, pairs):
    return tr[(tr["variant"] == variant) & (tr["cost"] == cost) & (tr["pair"].isin(pairs))]


def tables(window: str, only: list[tuple[str, str]] | None = None) -> tuple[str, dict]:
    tr = _load_trades(window)
    lines = ["| Pair | Variant | Trades | vs base | PF judged | PF no swap | PF 1.0 pip | Net R | Max DD R | Win | p | Folds+ | Base PF | Result |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    dec: dict = {}
    n_cases = len(only) if only else 0
    for pair in PRIMARY:
        bt = _sel(tr, "baseline", "measured", [pair])
        bm = metrics(bt, "r_swap")
        ctrl = metrics(_sel(tr, "CONF65", "measured", [pair]), "r_swap")
        for v in ["baseline"] + VARIANTS:
            if only is not None and (v, pair) not in only and v != "baseline":
                continue
            t = _sel(tr, v, "measured", [pair])
            m = metrics(t, "r_swap")
            fp = folds_positive(t)
            pf_ns = metrics(t, "r")["pf"]
            pf_1 = metrics(_sel(tr, v, "ref1", [pair]), "r")["pf"]
            delta = f"{(m['n'] / bm['n'] - 1) * 100:+.0f}%" if bm["n"] else "n/a"
            res = ""
            if v not in ("baseline", "H4_D_DISAGREE"):
                if window == "insample":
                    c = is_pass(m, bm, fp, ctrl if v == "H4_D_CONF65" else None)
                else:
                    c = ho_pass(m, bm, n_cases)
                dec[f"{v}|{pair}"] = {"checks": c, "n": m["n"], "pf": m["pf"], "net_r": m["net_r"], "max_dd_r": m["max_dd_r"], "p": m["p"], "folds_pos": fp, "base_pf": bm["pf"]}
                res = ("PASS" if c["PASS"] else "FAIL") + ("; UNDERPOWERED" if m["n"] < 100 else ("; thin" if m["n"] < 300 else ""))
            elif v == "H4_D_DISAGREE":
                res = "sanity: " + ("worse than base (as expected)" if m["pf"] < bm["pf"] else "NOT worse than base")
            lines.append(f"| {pair} | {v} | {m['n']} | {delta} | {_f(m['pf'])} | {_f(pf_ns)} | {_f(pf_1)} | {_f(m['net_r'],1)} | {_f(m['max_dd_r'],1)} | {_f(m['win'],2)} | {_f(m['p'])} | {fp}/5 | {_f(bm['pf'])} | {res} |")
    # 10-pair backup label
    lines += ["", "### 10-pair backup (pooled, judged cost incl. swaps)", "", "| Variant | Trades | vs base | PF | Net R | Max DD R | Pairs >= base+0.02 (n>=30) | Label |", "|---|---:|---:|---:|---:|---:|---:|---|"]
    bp = metrics(_sel(tr, "baseline", "measured", PAIRS), "r_swap")
    backup = {}
    for v in ["baseline"] + VARIANTS:
        t = _sel(tr, v, "measured", PAIRS)
        m = metrics(t, "r_swap")
        imp = 0
        for p in PAIRS:
            mp, bpm = metrics(t[t["pair"] == p], "r_swap"), metrics(_sel(tr, "baseline", "measured", [p]), "r_swap")
            imp += int(mp["n"] >= 30 and mp["pf"] >= bpm["pf"] + 0.02)
        lab = "" if v == "baseline" else ("supports" if (m["pf"] >= bp["pf"] + 0.05 and imp >= 6) else "does not support")
        backup[v] = {"n": m["n"], "pf": m["pf"], "pairs_improved": imp, "label": lab}
        lines.append(f"| {v} | {m['n']} | {(m['n'] / bp['n'] - 1) * 100:+.0f}% | {_f(m['pf'])} | {_f(m['net_r'],1)} | {_f(m['max_dd_r'],1)} | {imp} | {lab} |")
    # bucket diagnostic (baseline trades split by 4h+daily agreement)
    lines += ["", "### Diagnostic: baseline trades by 4h + daily agreement with the 1h signal (judged cost incl. swaps)", "", "| Scope | Bucket | Trades | PF | Net R | R per trade |", "|---|---|---:|---:|---:|---:|"]
    for label, plist in (("EURUSD", ["EURUSD"]), ("USDJPY", ["USDJPY"]), ("10 pairs", PAIRS)):
        bt = _sel(tr, "baseline", "measured", plist)
        for b in ("both_agree", "mixed", "both_oppose"):
            m = metrics(bt[bt["bucket"] == b], "r_swap")
            lines.append(f"| {label} | {b} | {m['n']} | {_f(m['pf'])} | {_f(m['net_r'],1)} | {_f(m['mean_r'],4)} |")
    return "\n".join(lines), {"decisions": dec, "backup": backup}


def phase_insample() -> None:
    txt, res = tables("insample")
    to_ho = [k.split("|") for k, v in res["decisions"].items() if v["checks"]["PASS"]]
    res["to_holdout"] = to_ho
    (OUT / "insample_decisions.json").write_text(json.dumps(res, indent=1, default=float))
    (OUT / "insample_report.md").write_text(f"## In-sample (entries before {CUTOFF.date()}), judged = mt5_measured + measured MT5 swaps\n\n{txt}\n")
    print(txt)
    print("to_holdout:", to_ho)


def phase_holdout() -> None:
    if (OUT / "holdout_report.md").exists():
        sys.exit("holdout already scored once - refusing to re-score")
    dec = json.loads((OUT / "insample_decisions.json").read_text())
    only = [(a, b) for a, b in dec["to_holdout"]]
    if not only:
        print("nothing passed in-sample; holdout not run")
        return
    txt, res = tables("holdout", only=only)
    (OUT / "holdout_decisions.json").write_text(json.dumps(res, indent=1, default=float))
    (OUT / "holdout_report.md").write_text(f"## Holdout (entries from {CUTOFF.date()}), scored once\n\n{txt}\n")
    print(txt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["run", "insample", "holdout"])
    ap.add_argument("--sanity", action="store_true")
    ap.add_argument("--pairs", nargs="*", default=PAIRS)
    a = ap.parse_args()
    if a.sanity:
        return 1 if sanity() else 0
    if a.phase == "run":
        run_all(a.pairs)
    elif a.phase == "insample":
        phase_insample()
    elif a.phase == "holdout":
        phase_holdout()
    else:
        ap.error("--phase or --sanity required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())