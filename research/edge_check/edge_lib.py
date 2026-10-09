"""Shared, read-only helpers for research/edge_check (no training, no writes outside out_dir)."""
from __future__ import annotations

import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT / "data" / "replay"
HISTORY = ROOT / "data" / "history"
COSTS = ROOT / "data" / "paper_shadow" / "measured_costs.csv"


def pip_size(pair: str) -> float:
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def norm_ppf(p: float) -> float:
    from statistics import NormalDist
    return NormalDist().inv_cdf(p)


def norm_cdf(x: float) -> float:
    from statistics import NormalDist
    return NormalDist().cdf(x)


def profit_factor(r: pd.Series) -> float:
    r = pd.Series(r).dropna()
    g, l = r[r > 0].sum(), -r[r < 0].sum()
    return float(g / l) if l > 0 else (float("inf") if g > 0 else float("nan"))


def max_drawdown_additive(r: pd.Series) -> float:
    """Max DD of the cumulative-sum equity (unit size per trade, return units)."""
    eq = pd.Series(r).fillna(0).cumsum()
    peak = np.maximum.accumulate(np.r_[0.0, eq.values])[1:]
    return float((eq.values - peak).min()) if len(eq) else 0.0


def load_status(job_dir: Path) -> dict[str, Any]:
    return json.loads((job_dir / "status.json").read_text(encoding="utf-8-sig"))


def find_job(prefix: str) -> Path:
    hits = [p for p in REPLAY.iterdir() if p.is_dir() and p.name.startswith(prefix)]
    if len(hits) != 1:
        raise FileNotFoundError(f"job {prefix}: {len(hits)} matches")
    return hits[0]


def load_trades(job_dir: Path, book: str = "champion") -> pd.DataFrame:
    t = pd.read_csv(job_dir / "trades.csv")
    t = t[t["book"] == book].copy()
    t["entry_ts"] = pd.to_datetime(t["entry_time_utc"].str.replace(" UTC", "", regex=False))
    t["exit_ts"] = pd.to_datetime(t["exit_time_utc"].str.replace(" UTC", "", regex=False))
    return t.sort_values("entry_ts").reset_index(drop=True)


def index_runs() -> pd.DataFrame:
    rows = []
    for d in sorted(REPLAY.iterdir()):
        if not d.is_dir() or not (d / "status.json").exists():
            continue
        st = load_status(d)
        rows.append(
            {
                "job": d.name,
                "pair": st.get("pair"),
                "status": st.get("status"),
                "start": st.get("start"),
                "end": st.get("end"),
                "finished": st.get("finished_at_dhaka"),
                "hypothesis": st.get("hypothesis"),
                "source": st.get("source"),
                "has_trades": (d / "trades.csv").exists(),
            }
        )
    return pd.DataFrame(rows)


def load_history(pair: str, path: Path | None = None) -> pd.DataFrame:
    p = path or (HISTORY / f"{pair.upper()}_1h.csv")
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    return df[~df.index.duplicated(keep="last")].sort_index()


def corruption_profile(df: pd.DataFrame, pair: str) -> pd.DataFrame:
    """Per-year share of wickless bars and median |Open - prev Close| in pips."""
    pip = pip_size(pair)
    wick = ((df.High - np.maximum(df.Open, df.Close)).abs() < 1e-12) & (
        (df.Low - np.minimum(df.Open, df.Close)).abs() < 1e-12
    )
    gap = (df.Open - df.Close.shift(1)).abs() / pip
    out = pd.DataFrame({"wickless": wick, "gap": gap})
    return out.groupby(out.index.year).agg(wickless_share=("wickless", "mean"), median_open_gap_pips=("gap", "median"))


def measured_costs() -> pd.DataFrame:
    c = pd.read_csv(COSTS)
    return c.set_index("pair")


def close_atr(close: pd.Series, period: int = 14, factor: float = 1.0) -> pd.Series:
    return close.diff().abs().rolling(period, min_periods=period).mean() * factor


def true_range_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    pc = df.Close.shift(1)
    tr = pd.concat([(df.High - df.Low), (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def resim_close_path(
    trades: pd.DataFrame,
    close: pd.Series,
    atr: pd.Series,
    *,
    cost_rt_px: float,
    tp_atr: float = 2.0,
    sl_atr: float = 2.0,
    horizon: int = 8,
    min_bars_before_sl: int = 2,
) -> pd.DataFrame:
    """Re-label each logged call on the CLOSE path only (no Open/High/Low used).

    Entry = close of the decision bar (bar before the logged fill bar; on clean
    data that equals next open within ~0.1 pip). Barrier = the board's ATR TP/SL
    distance. Exit at the first close beyond TP or SL (SL not checked in the first
    ``min_bars_before_sl`` bars, as replay.exit_hold), else the close of bar
    ``horizon``. Return is fractional, after the given round-trip cost.
    """
    idx = close.index
    out = []
    cv = close.values
    av = atr.reindex(idx).values
    for _, tr in trades.iterrows():
        loc = idx.searchsorted(tr["entry_ts"])
        if loc <= 0 or loc >= len(idx) or idx[loc] != tr["entry_ts"]:
            out.append((np.nan, "no_bar"))
            continue
        dec = loc - 1
        entry = cv[dec]
        a = av[dec]
        if not np.isfinite(a) or a <= 0 or not np.isfinite(entry):
            out.append((np.nan, "no_atr"))
            continue
        sgn = 1.0 if tr["side"] == "BUY" else -1.0
        tp_d, sl_d = tp_atr * a, sl_atr * a
        last = min(len(cv) - 1, loc + horizon - 1)
        if last < loc + horizon - 1:
            out.append((np.nan, "open"))
            continue
        exit_px, why = cv[last], "timeout"
        for k, j in enumerate(range(loc, last + 1), start=1):
            move = sgn * (cv[j] - entry)
            if move >= tp_d:
                exit_px, why = cv[j], "tp"
                break
            if k > min_bars_before_sl and move <= -sl_d:
                exit_px, why = cv[j], "sl"
                break
        ret = (sgn * (exit_px - entry) - cost_rt_px) / entry
        out.append((ret, why))
    res = trades[["entry_ts", "side"]].copy()
    res["ret"] = [o[0] for o in out]
    res["exit_reason"] = [o[1] for o in out]
    return res


# ---------- Bailey & Lopez de Prado statistics ----------

def sharpe_stats(r: pd.Series) -> dict[str, float]:
    r = pd.Series(r).dropna()
    n = len(r)
    sd = r.std(ddof=1)
    sr = float(r.mean() / sd) if n > 1 and sd > 0 else float("nan")
    skew = float(r.skew()) if n > 2 else float("nan")
    kurt = float(r.kurt() + 3.0) if n > 3 else float("nan")  # raw (non-excess)
    return {"n": n, "sr": sr, "skew": skew, "kurt": kurt}


def psr(sr: float, sr0: float, n: int, skew: float, kurt: float) -> float:
    den = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if n < 2 or not np.isfinite(sr) or den <= 0:
        return float("nan")
    return norm_cdf((sr - sr0) * math.sqrt(n - 1) / math.sqrt(den))


def expected_max_sr(n_trials: int, var_sr: float) -> float:
    """SR0 = sqrt(V[SR]) * ((1-g) Z(1-1/N) + g Z(1-1/(N e)))  (per-trade SR units)."""
    g = 0.5772156649015329
    if n_trials < 2 or not np.isfinite(var_sr) or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - g) * norm_ppf(1 - 1.0 / n_trials) + g * norm_ppf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(r: pd.Series, n_trials: int, var_sr: float) -> dict[str, float]:
    s = sharpe_stats(r)
    sr0 = expected_max_sr(n_trials, var_sr)
    return {**s, "sr0": sr0, "dsr": psr(s["sr"], sr0, s["n"], s["skew"], s["kurt"]), "psr0": psr(s["sr"], 0.0, s["n"], s["skew"], s["kurt"])}


def min_track_record(sr: float, skew: float, kurt: float, sr_star: float = 0.0, alpha: float = 0.05) -> float:
    """Minimum number of trades for PSR(sr_star) >= 1-alpha."""
    if not np.isfinite(sr) or sr <= sr_star:
        return float("inf")
    den = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    return 1.0 + den * (norm_ppf(1 - alpha) / (sr - sr_star)) ** 2


def pbo_cscv(m: pd.DataFrame, n_splits: int = 16) -> dict[str, float]:
    """Probability of backtest overfitting via CSCV (Bailey et al. 2015).

    ``m``: rows = time periods (equal-length blocks are formed from rows),
    columns = variants, values = period returns. Metric = mean/std (Sharpe).
    """
    m = m.dropna(how="any")
    t, n = m.shape
    if n < 2 or t < n_splits:
        return {"pbo": float("nan"), "n_combos": 0, "T": t, "N": n}
    blocks = np.array_split(np.arange(t), n_splits)
    logits = []
    for comb in itertools.combinations(range(n_splits), n_splits // 2):
        is_rows = np.concatenate([blocks[i] for i in comb])
        oos_rows = np.concatenate([blocks[i] for i in range(n_splits) if i not in comb])
        is_m, oos_m = m.values[is_rows], m.values[oos_rows]

        def sr(x):
            sd = x.std(axis=0, ddof=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(sd > 0, x.mean(axis=0) / sd, 0.0)

        best = int(np.argmax(sr(is_m)))
        oos_sr = sr(oos_m)
        rank = (oos_sr < oos_sr[best]).sum() + 0.5 * ((oos_sr == oos_sr[best]).sum() - 1) + 1
        w = rank / (n + 1)
        logits.append(math.log(w / (1 - w)))
    logits = np.array(logits)
    return {"pbo": float((logits <= 0).mean()), "n_combos": len(logits), "T": t, "N": n, "median_logit": float(np.median(logits))}
