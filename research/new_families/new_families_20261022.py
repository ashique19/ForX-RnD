"""New entry families A-D (prereg research/new_families/PREREG_20261022.md, commit 4e8e168).

Usage:  python research/new_families/new_families_20261022.py --family A --phase insample
        python research/new_families/new_families_20261022.py --family A --phase holdout   (only cases that passed in-sample)
        python research/new_families/new_families_20261022.py --report insample|holdout
Research only: reads data/history, data/calendars, measured costs, saved HTF preds; writes data/edge_check/new_families_20261022/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "research" / "edge_check"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from forex_lab.backtest import _first_touch_exit, _spread_cost_frac, true_range_atr  # noqa: E402

PAIRS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY", "EURGBP", "GBPJPY"]
PRIMARY = ["EURUSD", "USDJPY"]
OUT = ROOT / "data" / "edge_check" / "new_families_20261022"
HTF = ROOT / "data" / "edge_check" / "htf_20261020"
CAL = ROOT / "data" / "calendars" / "pit_high_impact_usd_eur_rich_cpi_pce_gdp.json"
N_TRIALS = 146
P_IS = 0.05 / N_TRIALS
CUTOFF = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])

# exits: (sl_mult, tp_mult, horizon)
VARIANTS = {
    "A_LDN_k2": dict(fam="A", sess="LDN", k=2.0, exit=(1.5, 1.5, 6)),
    "A_LDN_k3": dict(fam="A", sess="LDN", k=3.0, exit=(1.5, 1.5, 6)),
    "A_NY_k2": dict(fam="A", sess="NY", k=2.0, exit=(1.5, 1.5, 6)),
    "A_NY_k3": dict(fam="A", sess="NY", k=3.0, exit=(1.5, 1.5, 6)),
    "B_N12_q10": dict(fam="B", N=12, q=0.10, exit=(1.0, 2.0, 12)),
    "B_N12_q20": dict(fam="B", N=12, q=0.20, exit=(1.0, 2.0, 12)),
    "B_N24_q10": dict(fam="B", N=24, q=0.10, exit=(1.0, 2.0, 12)),
    "B_N24_q20": dict(fam="B", N=24, q=0.20, exit=(1.0, 2.0, 12)),
    "C_cont_k05": dict(fam="C", mode="cont", k=0.5, exit=(1.0, 1.0, 6)),
    "C_cont_k10": dict(fam="C", mode="cont", k=1.0, exit=(1.0, 1.0, 6)),
    "C_fade_k05": dict(fam="C", mode="fade", k=0.5, exit=(1.0, 1.0, 6)),
    "C_fade_k10": dict(fam="C", mode="fade", k=1.0, exit=(1.0, 1.0, 6)),
    "D_K8": dict(fam="D", K=8, exit=(2.0, 2.0, 8)),
    "D_K24": dict(fam="D", K=24, exit=(2.0, 2.0, 8)),
    "D_K48": dict(fam="D", K=48, exit=(2.0, 2.0, 8)),
}
SESS_HOUR = {"LDN": 6, "NY": 12}
SQUEEZE_WIN = 500


# ------------------------------------------------------------------ signals (closed bar t, causal)
def sig_a(df: pd.DataFrame, atr: pd.Series, sess: str, k: float) -> tuple[pd.Series, pd.Series]:
    h = SESS_HOUR[sess]
    idx = df.index
    day = idx.normalize()
    anchor = df["Open"].where(idx.hour == 0)
    anchor_by_day = pd.Series(anchor.to_numpy(), index=day).dropna()
    anchor_by_day = anchor_by_day[~anchor_by_day.index.duplicated()]
    dec = (idx.hour == h) & (idx.dayofweek < 5)
    a0 = pd.Series(day[dec], index=idx[dec]).map(anchor_by_day)
    m = df["Close"][dec] - a0
    a = atr[dec]
    ok = a0.notna() & a.notna() & (a > 0) & (m.abs() >= k * a)
    side = -np.sign(m[ok]).astype(int)
    return side[side != 0], a[ok]


def sig_b(df: pd.DataFrame, atr: pd.Series, N: int, q: float) -> tuple[pd.Series, pd.Series]:
    rank_prev = atr.rolling(SQUEEZE_WIN, min_periods=SQUEEZE_WIN).rank(pct=True).shift(1)
    hi = df["High"].shift(1).rolling(N, min_periods=N).max()
    lo = df["Low"].shift(1).rolling(N, min_periods=N).min()
    idx = df.index
    sess = (idx.hour >= 7) & (idx.hour <= 15) & (idx.dayofweek < 5)
    sq = (rank_prev <= q) & sess & atr.notna() & (atr > 0)
    c = df["Close"]
    side = pd.Series(0, index=idx)
    side[sq & (c > hi)] = 1
    side[sq & (c < lo)] = -1
    side = side[side != 0]
    return side, atr.reindex(side.index)


def load_events() -> pd.DataFrame:
    d = json.loads(CAL.read_text(encoding="utf-8"))
    ev = pd.DataFrame(d["events"])
    ev["when"] = pd.to_datetime(ev["when"], utc=True).dt.tz_localize(None)
    return ev[["title", "currency", "when"]]


def sig_c(df: pd.DataFrame, atr: pd.Series, pair: str, events: pd.DataFrame, mode: str, k: float) -> tuple[pd.Series, pd.Series]:
    ccys = {pair[:3], pair[3:]}
    ev = events[events["currency"].isin(ccys)]
    bars = pd.DatetimeIndex(sorted(set(ev["when"].dt.floor("h"))))
    pos = pd.Series(np.arange(len(df)), index=df.index)
    bars = bars[bars.isin(df.index)]
    sides, atrs = {}, {}
    for ts in bars:
        loc = int(pos[ts])
        if loc < 1:
            continue
        a = float(atr.iloc[loc - 1])
        if not np.isfinite(a) or a <= 0:
            continue
        rx = float(df["Close"].iloc[loc] - df["Open"].iloc[loc])
        if abs(rx) < k * a or rx == 0:
            continue
        s = int(np.sign(rx)) * (1 if mode == "cont" else -1)
        sides[ts], atrs[ts] = s, a
    return pd.Series(sides, dtype=int), pd.Series(atrs, dtype=float)


# ------------------------------------------------------------------ simulator
def simulate(df, side: pd.Series, atr_used: pd.Series, pair: str, rt_pips: float, exit_cfg, cooldown: int | None = None,
             max_entry: pd.Timestamp | None = None) -> pd.DataFrame:
    sl_m, tp_m, horizon = exit_cfg
    idx = df.index
    n = len(idx)
    opn = df["Open"].to_numpy()
    pos = pd.Series(np.arange(n), index=idx)
    locs = sorted((int(pos[t]), int(s), float(atr_used[t])) for t, s in side.items() if t in pos.index)
    cfg = {"spread_pips": float(rt_pips), "commission_pips": 0.0}
    rows, next_free = [], 0
    for loc, s, a in locs:
        if s == 0 or loc < next_free:
            continue
        e = loc + 1
        if e >= n or not np.isfinite(a) or a <= 0:
            continue
        if max_entry is not None and idx[e] >= max_entry:
            continue
        entry = float(opn[e])
        x_loc, x_px, reason = _first_touch_exit(df, e, horizon, s, entry, tp_m * a, sl_m * a, "high_low")
        raw = s * (x_px / entry - 1.0)
        net = raw - _spread_cost_frac(pair, cfg, entry)
        rows.append({"pair": pair, "decision_time": idx[loc], "entry_time": idx[e], "exit_bar_time": idx[x_loc],
                     "side": "BUY" if s == 1 else "SELL", "entry": entry, "exit": x_px, "exit_reason": reason, "atr": a,
                     "raw_return": raw, "net_return": net, "r": net * entry / (sl_m * a)})
        next_free = x_loc + 1
        if cooldown is not None and reason in ("sl", "sl_conflict"):
            next_free = x_loc + int(cooldown)  # first allowed ENTRY bar = x + 1 + K (prereg)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ metrics
def metrics(t: pd.DataFrame) -> dict:
    if t is None or len(t) == 0:
        return {"n": 0, "pf": None, "net_r": 0.0, "max_dd_r": 0.0, "win_rate": None, "p_trade": None, "p_daily": None}
    t = t.sort_values("entry_time")
    r = t["r"].astype(float)
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    eq = r.cumsum()
    dd = min(float((eq - eq.cummax().clip(lower=0)).min()), 0.0)
    pt = float(stats.ttest_1samp(r, 0.0, alternative="greater").pvalue) if len(r) > 1 else None
    d = t.groupby(pd.to_datetime(t["entry_time"]).dt.floor("D"))["r"].sum()
    pdly = float(stats.ttest_1samp(d, 0.0, alternative="greater").pvalue) if len(d) > 1 else None
    return {"n": int(len(t)), "pf": (gp / gl) if gl > 0 else None, "net_r": float(r.sum()), "max_dd_r": dd,
            "win_rate": float((r > 0).mean()), "p_trade": pt, "p_daily": pdly}


def is_pass(m: dict, fam: str) -> bool:
    return bool(m["n"] >= (100 if fam == "C" else 150) and m["pf"] is not None and m["pf"] >= 1.10
                and abs(m["max_dd_r"]) <= m["net_r"] and m["p_trade"] is not None and m["p_trade"] < P_IS)


def ho_pass(m: dict, m_is: dict, fam: str, n_cases: int) -> bool:
    return bool(m["n"] >= (20 if fam == "C" else 30) and m["pf"] is not None and m["pf"] > 1.10 and m["net_r"] > 0
                and abs(m["max_dd_r"]) <= abs(m_is["max_dd_r"]) and m["p_daily"] is not None
                and m["p_daily"] < 0.05 / max(n_cases, 1))


# ------------------------------------------------------------------ runner
def _costs():
    import edge_lib as L
    return L.measured_costs(), L


def signals_for(name, df, atr, pair, events, preds=None):
    v = VARIANTS[name]
    if v["fam"] == "A":
        return sig_a(df, atr, v["sess"], v["k"])
    if v["fam"] == "B":
        return sig_b(df, atr, v["N"], v["q"])
    if v["fam"] == "C":
        return sig_c(df, atr, pair, events, v["mode"], v["k"])
    raise ValueError(name)


def d_signals(pair, df, atr):
    preds = pd.read_csv(HTF / f"{pair}_preds.csv", index_col=0, parse_dates=True)
    from forex_lab.features import LABEL_MAP
    p = preds["pred"].astype(int)
    side = p.map({LABEL_MAP["BUY"]: 1, LABEL_MAP["SELL"]: -1}).fillna(0).astype(int)
    side = side[(side != 0) & side.index.isin(df.index)]
    return side, atr.reindex(side.index)


def run_family(fam: str, phase: str, cases: list[str] | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    costs, L = _costs()
    events = load_events() if fam == "C" else None
    names = [k for k, v in VARIANTS.items() if v["fam"] == fam]
    allt = []
    for pair in PAIRS:
        full = L.load_history(pair)[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        atr_full = true_range_atr(full, 14)
        if phase == "insample":
            df = full[full.index < CUTOFF + pd.Timedelta(days=3)]
        else:
            df = full
        atr = atr_full.reindex(df.index)
        if fam == "D" and phase == "insample":
            # baseline must reproduce the HTF baseline (full data, p90) before anything else
            sb, ab = d_signals(pair, full, atr_full)
            rt90 = 2 * float(costs.loc[pair, "one_way_p90"])
            b = simulate(full, sb, ab, pair, rt90, VARIANTS["D_K8"]["exit"])
            ref = pd.read_csv(HTF / f"{pair}_trades.csv")
            ref = ref[(ref.variant == "baseline") & (ref.cost == "p90")]
            if len(b) != len(ref) or abs(b["r"].sum() - ref["r"].sum()) > 1e-6:
                sys.exit(f"{pair}: D baseline does not reproduce HTF ({len(b)} vs {len(ref)})")
            print(pair, "D baseline reproduced", len(b), flush=True)
        for name in names + (["D_base"] if fam == "D" else []):
            v = VARIANTS.get(name, VARIANTS.get("D_K8"))
            if cases is not None and name not in cases and name != "D_base":
                continue
            if fam == "D":
                side, au = d_signals(pair, df, atr)
            else:
                side, au = signals_for(name, df, atr, pair, events)
            for q in ("p90", "p50"):
                rt = 2 * float(costs.loc[pair, f"one_way_{q}"])
                t = simulate(df, side, au, pair, rt, v["exit"], cooldown=None if name == "D_base" else v.get("K"),
                             max_entry=CUTOFF if phase == "insample" else None)
                if phase == "holdout" and len(t):
                    t = t[t["entry_time"] >= CUTOFF]
                allt.append(t.assign(variant=name, cost=q))
        print(fam, phase, pair, "done", flush=True)
    tr = pd.concat([t for t in allt if len(t)], ignore_index=True) if any(len(t) for t in allt) else pd.DataFrame()
    tr.to_csv(OUT / f"{fam}_{phase}_trades.csv", index=False)
    print(fam, phase, "trades", len(tr), flush=True)


def summarize(phase: str) -> dict:
    out = {}
    for fam in "ABCD":
        f = OUT / f"{fam}_{phase}_trades.csv"
        if not f.exists():
            continue
        tr = pd.read_csv(f, parse_dates=["entry_time", "decision_time", "exit_bar_time"])
        for name in sorted(tr["variant"].unique()):
            for q in ("p90", "p50"):
                t = tr[(tr.variant == name) & (tr.cost == q)]
                per = {p: metrics(t[t.pair == p]) for p in PAIRS}
                pooled = metrics(t)
                out.setdefault(name, {})[q] = {"pairs": per, "pooled": pooled}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family")
    ap.add_argument("--phase", choices=["insample", "holdout"])
    ap.add_argument("--report", choices=["insample", "holdout"])
    a = ap.parse_args()
    if a.report:
        s = summarize(a.report)
        (OUT / f"summary_{a.report}.json").write_text(json.dumps(s, indent=1, default=str))
        print(json.dumps({k: {q: {"EURUSD": v[q]["pairs"]["EURUSD"], "USDJPY": v[q]["pairs"]["USDJPY"], "pooled": v[q]["pooled"]}
                              for q in ("p90",)} for k, v in s.items()}, default=str))
        return
    cases = None
    if a.phase == "holdout":
        dec = json.loads((OUT / "insample_decisions.json").read_text())
        # judged on the primary pair(s) that passed; run on all 10 pairs for the robustness report
        cases = sorted({c[0] for c in dec["to_holdout"] if VARIANTS[c[0]]["fam"] == a.family})
        if not cases:
            print(a.family, "nothing passed in-sample; holdout not run"); return
    run_family(a.family, a.phase, cases)


if __name__ == "__main__":
    main()
