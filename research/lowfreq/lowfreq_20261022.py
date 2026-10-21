"""Low-frequency families F1-F3 (prereg research/lowfreq/PREREG_LOWFREQ_20261022.md, commit 0fd7527).

python research/lowfreq/lowfreq_20261022.py --family F1 --phase insample|holdout
python research/lowfreq/lowfreq_20261022.py --report insample|holdout
Research only. Reads data/history, measured costs, data/research/bis/cbpol_daily_8ccy.csv; writes data/edge_check/lowfreq_20261022/.
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

from forex_lab.backtest import _first_touch_exit, true_range_atr  # noqa: E402
from forex_lab.config_loader import pip_size_for_pair  # noqa: E402

PAIRS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "EURJPY", "EURGBP", "GBPJPY"]
PRIMARY = ["EURUSD", "USDJPY"]
OUT = ROOT / "data" / "edge_check" / "lowfreq_20261022"
RATES = ROOT / "data" / "research" / "bis" / "cbpol_daily_8ccy.csv"
N_TRIALS = 154
ALPHA_IS = 0.05 / N_TRIALS
CUTOFF = pd.Timestamp(json.loads((ROOT / "research" / "holdout.json").read_text())["cutoff_utc"])
MARKUP_PIPS = 1.0
VARIANTS = {
    "F1_4h_q10": dict(fam="F1", tf="4h", q=0.10), "F1_4h_q20": dict(fam="F1", tf="4h", q=0.20),
    "F1_D_q10": dict(fam="F1", tf="D", q=0.10), "F1_D_q20": dict(fam="F1", tf="D", q=0.20),
    "F2_top3": dict(fam="F2", k=3), "F2_top5": dict(fam="F2", k=5),
    "F3_L20": dict(fam="F3", L=20), "F3_L120": dict(fam="F3", L=120),
}
MIN_IS = {"F1_4h": 100, "F1_D": 30, "F2": 30, "F3_L20": 30, "F3_L120": 15}
MIN_HO = {"F1_4h": 15, "F1_D": 5, "F2": 5, "F3_L20": 5, "F3_L120": 5}


def _minkey(name: str) -> str:
    if name.startswith("F1"):
        return "F1_4h" if "_4h_" in name else "F1_D"
    return "F2" if name.startswith("F2") else name


# ------------------------------------------------------------------ bars
def trading_day(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    lab = (idx + pd.Timedelta(hours=3)).normalize()
    dow = lab.dayofweek
    lab = lab.where(dow != 5, lab - pd.Timedelta(days=1))
    lab = lab.where(dow != 6, lab + pd.Timedelta(days=1))
    return pd.DatetimeIndex(lab)


def resample(h: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Closed resampled bars indexed by close time; 'open_time' = first hourly open in the bar."""
    h = h.sort_index()
    idx = h.index
    day = trading_day(idx)
    raw_lab = (idx + pd.Timedelta(hours=3)).normalize()
    dayn = np.asarray((day - pd.Timestamp("2000-01-01")).days, dtype=np.int64)
    if tf == "D":
        key = pd.Series(dayn, index=idx)
        exp_end = day + pd.Timedelta(hours=21)
    else:
        slot = np.where(raw_lab.dayofweek == 5, 6, ((idx.hour - 21) % 24) // 4)
        key = pd.Series(dayn * 10 + slot, index=idx)
        exp_end = day - pd.Timedelta(hours=3) + pd.to_timedelta(np.minimum(slot, 5) * 4 + 4, unit="h") + \
            pd.to_timedelta(np.where(slot == 6, 2, 0), unit="h")
    gid = (key != key.shift()).cumsum().to_numpy()
    if tf == "4h":  # merge bins with < 2 hourly rows into the previous bin
        sizes = pd.Series(gid).map(pd.Series(gid).value_counts()).to_numpy()
        g = gid.copy()
        for i in range(1, len(g)):
            if sizes[i] < 2 and g[i] != g[i - 1] and (i == len(g) - 1 or gid[i + 1] != gid[i]):
                g[i] = g[i - 1]
        gid = g
    df = h[["Open", "High", "Low", "Close"]].copy()
    df["gid"] = gid
    df["t"] = idx
    df["exp_end"] = pd.DatetimeIndex(exp_end)
    agg = df.groupby("gid").agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"),
                                open_time=("t", "first"), last_t=("t", "last"), exp_end=("exp_end", "max"))
    agg["close_time"] = agg["last_t"] + pd.Timedelta(hours=1)
    last_close = idx[-1] + pd.Timedelta(hours=1)
    if len(agg) and agg["exp_end"].iloc[-1] > last_close:
        agg = agg.iloc[:-1]  # incomplete final bin
    return agg.set_index("close_time")[["Open", "High", "Low", "Close", "open_time"]]


# ------------------------------------------------------------------ financing
def load_rates() -> pd.DataFrame:
    r = pd.read_csv(RATES, index_col=0, parse_dates=True).sort_index()
    full = pd.date_range(r.index.min(), r.index.max() + pd.Timedelta(days=60), freq="D")
    return r.reindex(full).ffill()


def rate_diff(rates: pd.DataFrame, pair: str, date) -> float:
    """r_base - r_quote known on `date` (value of date - 1 calendar day)."""
    d = pd.Timestamp(date).normalize() - pd.Timedelta(days=1)
    if d not in rates.index:
        return float("nan")
    row = rates.loc[d]
    return float(row[pair[:3]] - row[pair[3:]])


def rollovers(t0, t1) -> pd.DatetimeIndex:
    """Weekday 21:00 UTC instants strictly between entry time t0 and exit time t1."""
    start = pd.Timestamp(t0).normalize()
    days = pd.date_range(start, pd.Timestamp(t1).normalize(), freq="D") + pd.Timedelta(hours=21)
    days = days[(days > pd.Timestamp(t0)) & (days < pd.Timestamp(t1)) & (days.dayofweek < 5)]
    return days


def financing(pair: str, side: int, entry: float, t0, t1, rates) -> tuple[float, float, int]:
    """(accrual_frac, markup_frac, n_rollovers)."""
    ro = rollovers(t0, t1)
    if not len(ro):
        return 0.0, 0.0, 0
    acc = sum(side * rate_diff(rates, pair, r) / 100.0 / 365.0 for r in ro)
    acc = 0.0 if not np.isfinite(acc) else acc
    mk = len(ro) * MARKUP_PIPS * pip_size_for_pair(pair, {}) / entry
    return float(acc), float(mk), int(len(ro))


def _row(pair, side, entry, x_px, t_entry, t_exit, dec_t, risk, rt_pips, rates, reason):
    raw = side * (x_px / entry - 1.0)
    cost = rt_pips * pip_size_for_pair(pair, {}) / entry
    acc, mk, n_ro = financing(pair, side, entry, t_entry, t_exit, rates)
    net_px = raw - cost
    net = net_px + acc - mk
    return {"pair": pair, "decision_time": dec_t, "entry_time": t_entry, "exit_time": t_exit, "side": side, "entry": entry,
            "exit": x_px, "exit_reason": reason, "risk": risk, "rollovers": n_ro, "accrual": acc, "markup": mk,
            "r": net * entry / risk, "r_price": net_px * entry / risk}


# ------------------------------------------------------------------ F1 squeeze breakout
def sig_f1(bars: pd.DataFrame, atr: pd.Series, q: float, N: int = 20, win: int = 250) -> pd.Series:
    rank_prev = atr.rolling(win, min_periods=win).rank(pct=True).shift(1)
    hi = bars["High"].shift(1).rolling(N, min_periods=N).max()
    lo = bars["Low"].shift(1).rolling(N, min_periods=N).min()
    sq = (rank_prev <= q) & atr.notna() & (atr > 0)
    s = pd.Series(0, index=bars.index)
    s[sq & (bars["Close"] > hi)] = 1
    s[sq & (bars["Close"] < lo)] = -1
    return s[s != 0]


def sim_f1(bars, atr, side, pair, rt_pips, rates, horizon=10, sl_m=1.0, tp_m=2.0):
    n = len(bars)
    pos = pd.Series(np.arange(n), index=bars.index)
    opn = bars["Open"].to_numpy()
    rows, next_free = [], 0
    for t, s in side.items():
        loc = int(pos[t])
        if loc < next_free or loc + 1 >= n:
            continue
        a = float(atr.iloc[loc]); e = loc + 1; entry = float(opn[e])
        x_loc, x_px, reason = _first_touch_exit(bars, e, horizon, int(s), entry, tp_m * a, sl_m * a, "high_low")
        rows.append(_row(pair, int(s), entry, x_px, bars["open_time"].iloc[e], bars.index[x_loc], t, sl_m * a, rt_pips, rates, reason))
        next_free = x_loc + 1
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ F3 TSMOM (stop-and-reverse)
def sig_f3(daily: pd.DataFrame, L: int) -> pd.Series:
    s = np.sign(daily["Close"] / daily["Close"].shift(L) - 1.0)
    return s.dropna().astype(int)


def sim_position(daily, atr, target: pd.Series, pair, rt_pips, rates, force_close_end=True):
    """target: desired side at each decision bar close (index subset of daily); act at next bar Open."""
    n = len(daily)
    pos = pd.Series(np.arange(n), index=daily.index)
    opn = daily["Open"].to_numpy()
    rows, cur = [], None  # cur = dict(side, entry, t_entry, dec, risk)
    for t, s in target.items():
        loc = int(pos[t])
        if loc + 1 >= n:
            break
        s = int(s)
        if cur is not None and cur["side"] == s:
            continue
        nxt = loc + 1
        if cur is not None:
            rows.append(_row(pair, cur["side"], cur["entry"], float(opn[nxt]), cur["t_entry"], daily["open_time"].iloc[nxt],
                             cur["dec"], cur["risk"], rt_pips, rates, "signal"))
            cur = None
        a = float(atr.iloc[loc])
        if s != 0 and np.isfinite(a) and a > 0:
            cur = {"side": s, "entry": float(opn[nxt]), "t_entry": daily["open_time"].iloc[nxt], "dec": t, "risk": 2.0 * a}
    if cur is not None and force_close_end:
        rows.append(_row(pair, cur["side"], cur["entry"], float(daily["Close"].iloc[-1]), cur["t_entry"], daily.index[-1],
                         cur["dec"], cur["risk"], rt_pips, rates, "data_end"))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ F2 carry top-k
def carry_targets(daily_by_pair: dict, rates, k: int, min_abs: float = 0.25) -> dict[str, pd.Series]:
    """Weekly decision at the Friday daily bar close (the week's last trading-day bar; known causally only on Friday).
    Weeks without a Friday bar skip the rebalance. Returns per-pair target side series."""
    days = sorted(set().union(*[set(d.index) for d in daily_by_pair.values()]))
    fridays = [t for t in days if t.dayofweek == 4]
    out = {p: {} for p in daily_by_pair}
    for t in fridays:
        t = pd.Timestamp(t)
        diffs = {p: rate_diff(rates, p, t) for p in daily_by_pair}
        elig = {p: d for p, d in diffs.items() if np.isfinite(d) and abs(d) >= min_abs}
        top = sorted(elig, key=lambda p: (-abs(elig[p]), PAIRS.index(p)))[:k]
        for p, d in daily_by_pair.items():
            if t in d.index:
                out[p][t] = int(np.sign(elig[p])) if p in top else 0
    return {p: pd.Series(v, dtype=int).sort_index() for p, v in out.items()}


# ------------------------------------------------------------------ metrics
def metrics(t: pd.DataFrame, col: str = "r", alpha: float = ALPHA_IS) -> dict:
    if t is None or len(t) == 0:
        return {"n": 0, "pf": None, "net_r": 0.0, "max_dd_r": 0.0, "win_rate": None, "p_trade": None, "mde_r": None, "mean_r": None}
    t = t.sort_values("entry_time")
    r = t[col].astype(float)
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    eq = r.cumsum()
    dd = min(float((eq - eq.cummax().clip(lower=0)).min()), 0.0)
    pt = float(stats.ttest_1samp(r, 0.0, alternative="greater").pvalue) if len(r) > 1 else None
    mde = float((stats.norm.ppf(1 - alpha) + 0.84) * r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else None
    return {"n": int(len(t)), "pf": (gp / gl) if gl > 0 else None, "net_r": float(r.sum()), "max_dd_r": dd,
            "win_rate": float((r > 0).mean()), "p_trade": pt, "mde_r": mde, "mean_r": float(r.mean())}


def is_pass(m: dict, name: str) -> bool:
    return bool(m["n"] >= MIN_IS[_minkey(name)] and m["pf"] is not None and m["pf"] >= 1.10 and abs(m["max_dd_r"]) <= m["net_r"]
                and m["p_trade"] is not None and m["p_trade"] < ALPHA_IS)


def ho_pass(m: dict, m_is: dict, name: str, n_cases: int) -> bool:
    return bool(m["n"] >= MIN_HO[_minkey(name)] and m["pf"] is not None and m["pf"] > 1.10 and m["net_r"] > 0
                and abs(m["max_dd_r"]) <= abs(m_is["max_dd_r"]) and m["p_trade"] is not None and m["p_trade"] < 0.05 / max(n_cases, 1))


# ------------------------------------------------------------------ runner
def run_family(fam: str, phase: str, only: list[str] | None = None) -> None:
    import edge_lib as L
    OUT.mkdir(parents=True, exist_ok=True)
    costs = L.measured_costs()
    rates = load_rates()
    names = [k for k, v in VARIANTS.items() if v["fam"] == fam and (only is None or k in only)]
    hist = {}
    for p in PAIRS:
        h = L.load_history(p)[["Open", "High", "Low", "Close"]].astype(float)
        if phase == "insample":
            h = h[h.index < CUTOFF + pd.Timedelta(days=30)]
        hist[p] = h
    bars = {tf: {p: resample(hist[p], tf) for p in PAIRS} for tf in ("4h", "D")}
    atrs = {tf: {p: true_range_atr(bars[tf][p], 14) for p in PAIRS} for tf in ("4h", "D")}
    allt = []
    for name in names:
        v = VARIANTS[name]
        tgt_c = carry_targets(bars["D"], rates, v["k"]) if fam == "F2" else None
        for p in PAIRS:
            for q in ("p90", "p50"):
                rt = 2 * float(costs.loc[p, f"one_way_{q}"])
                if fam == "F1":
                    b, a = bars[v["tf"]][p], atrs[v["tf"]][p]
                    t = sim_f1(b, a, sig_f1(b, a, v["q"]), p, rt, rates)
                elif fam == "F3":
                    t = sim_position(bars["D"][p], atrs["D"][p], sig_f3(bars["D"][p], v["L"]), p, rt, rates)
                else:
                    t = sim_position(bars["D"][p], atrs["D"][p], tgt_c[p], p, rt, rates)
                if len(t):
                    t = t[t["entry_time"] < CUTOFF] if phase == "insample" else t[t["entry_time"] >= CUTOFF]
                    allt.append(t.assign(variant=name, cost=q))
        print(fam, phase, name, "done", flush=True)
    tr = pd.concat(allt, ignore_index=True) if allt else pd.DataFrame()
    tr.to_csv(OUT / f"{fam}_{phase}_trades.csv", index=False)
    print(fam, phase, "trades", len(tr), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family"); ap.add_argument("--phase", choices=["insample", "holdout"])
    a = ap.parse_args()
    only = None
    if a.phase == "holdout":
        dec = json.loads((OUT / "insample_decisions.json").read_text())
        only = sorted({c[0] for c in dec["to_holdout"] if VARIANTS[c[0]]["fam"] == a.family})
        if not only:
            print(a.family, "nothing passed in-sample; holdout not run"); return
    run_family(a.family, a.phase, only)


if __name__ == "__main__":
    main()
