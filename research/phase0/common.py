"""Shared constants and helpers for the Phase 0 diagnostics.

Nothing in this package writes config, models, journals, or price files.
The fresh holdout below is frozen in ``PREREG.md`` and is excluded from every
calculation. A bar count of that window is an exclusion audit, not a result.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.paths import project_root

# Frozen in research/phase0/PREREG.md. Phase 0 must not train, score, or
# summarize bars at or after this timestamp.
FRESH_HOLDOUT_START = pd.Timestamp("2026-11-01 00:00:00")

# research/holdout.json. Already scored by later studies; not the fresh window.
PRIOR_HOLDOUT_START = pd.Timestamp("2025-10-01 00:00:00")

# Last bar the 2026-10-31 prereg allowed history to contain (inclusive).
PUBLISHED_SCORE_LAST_BAR = pd.Timestamp("2026-10-12 18:00:00")

# Round-trip pips. These are mt5_measured for the two focus pairs
# (research/costs/mt5_measured_costs_20261031.json): EURUSD 1.4, USDJPY 2.4.
DECLARED_RT_PIPS = {"EURUSD": 1.4, "USDJPY": 2.4}

DEFAULT_PAIRS = ("EURUSD", "USDJPY")
DEFAULT_SEEDS = 200
DEFAULT_BOOT = 1000
DEFAULT_BLOCK = 20
DEFAULT_SEED = 42

BANNED_OUTPUT_DIRS = ("data", "models", "signals")


def fresh_holdout_start() -> pd.Timestamp:
    return pd.Timestamp(FRESH_HOLDOUT_START)


def naive_index(index: Any) -> pd.DatetimeIndex:
    di = pd.DatetimeIndex(pd.to_datetime(index))
    if di.tz is not None:
        di = di.tz_convert("UTC").tz_localize(None)
    return di


def as_naive_ts(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts


def split_fresh_holdout(df: pd.DataFrame, start: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Drop bars at or after the fresh holdout. Prices in the tail are not returned."""
    cut = fresh_holdout_start() if start is None else as_naive_ts(start)
    if df is None or len(df) == 0:
        return df, {"n_kept": 0, "n_excluded": 0, "excluded_first": None, "excluded_last": None, "cutoff_utc": str(cut)}
    idx = naive_index(df.index)
    work = df.copy()
    work.index = idx
    keep = idx < cut
    excluded = work.loc[~keep]
    kept = work.loc[keep]
    return kept, {
        "cutoff_utc": str(cut),
        "n_kept": int(keep.sum()),
        "n_excluded": int((~keep).sum()),
        "excluded_first": None if excluded.empty else str(as_naive_ts(excluded.index.min())),
        "excluded_last": None if excluded.empty else str(as_naive_ts(excluded.index.max())),
    }


def assert_output_dir(path: Path) -> Path:
    """Refuse to write results under data/, models/, or signals/."""
    root = project_root().resolve()
    resolved = Path(path).resolve()
    for name in BANNED_OUTPUT_DIRS:
        banned = (root / name).resolve()
        if resolved == banned or banned in resolved.parents:
            raise SystemExit(f"refusing to write Phase 0 output under {name}/ ({resolved})")
    return resolved


def load_history_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    if df.empty:
        raise FileNotFoundError(f"history file is empty: {path}")
    df.index = naive_index(df.index)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    needed = ["Open", "High", "Low", "Close"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise FileNotFoundError(f"{path} is missing {', '.join(missing)}")
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    return df


def history_path(pair: str, history_dir: Path) -> Path:
    return Path(history_dir) / f"{pair.upper()}_1h.csv"


def judged_rt_pips(pair: str) -> dict[str, Any]:
    """Research round-trip cost in pips. Declared values win for the focus pairs.

    The committed mt5_measured table is checked when it imports. A mismatch is
    reported; the declared 1.4 / 2.4 figures are what this diagnostic uses.
    """
    key = pair.upper()
    table_value = None
    table_error = None
    try:
        from forex_lab.cost_model import rt_cost_pips

        table_value = float(rt_cost_pips(key, "mt5_measured"))
    except Exception as exc:  # missing pair or unreadable table
        table_error = str(exc)
    if key in DECLARED_RT_PIPS:
        declared = float(DECLARED_RT_PIPS[key])
        return {
            "pair": key,
            "rt_pips": declared,
            "source": "phase0_declared",
            "mt5_measured_table": table_value,
            "table_error": table_error,
            "table_matches_declared": table_value is not None and abs(table_value - declared) < 1e-9,
        }
    if table_value is None:
        raise KeyError(f"no Phase 0 round-trip cost for {key}: {table_error}")
    return {
        "pair": key,
        "rt_pips": table_value,
        "source": "mt5_measured",
        "mt5_measured_table": table_value,
        "table_error": None,
        "table_matches_declared": True,
    }


def research_cfg(cfg: dict[str, Any], pair: str, model_type: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """In-memory config for one pair. Does not write yaml.

    ``spread_pips`` becomes the research round trip (commission stays in the
    yaml copy and is subtracted so it is not charged twice). Logistic comparison
    is turned off so the diagnostic fits the primary model only.
    """
    import copy

    from forex_lab.cost_model import override_cfg_costs

    cost = judged_rt_pips(pair)
    new, info = override_cfg_costs(cfg, pair, cost_pips=cost["rt_pips"])
    new = copy.deepcopy(new)
    new.setdefault("model", {})["compare_logistic"] = False
    if model_type:
        new["model"]["type"] = str(model_type).lower()
    info = dict(info)
    info.update(cost)
    info["yaml_written"] = False
    info["compare_logistic"] = False
    return new, info


def attach_r(trades: pd.DataFrame, df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    """R = return * entry / (sl_atr * ATR at the decision bar). 1R is the stop."""
    from forex_lab.features import true_range_atr

    out = trades.copy()
    if out.empty:
        out["r_net"] = pd.Series(dtype=float)
        out["r_raw"] = pd.Series(dtype=float)
        return out
    sl = float((cfg.get("barrier") or {}).get("sl_atr", 2.0))
    atr = true_range_atr(df, int(cfg.get("atr_period", 14)))
    decisions = pd.DatetimeIndex(pd.to_datetime(out["decision_time"]))
    if decisions.tz is not None:
        decisions = decisions.tz_convert("UTC").tz_localize(None)
    scale = sl * atr.reindex(decisions).to_numpy(dtype=float)
    entry = pd.to_numeric(out["entry"], errors="coerce").to_numpy(dtype=float)
    net = pd.to_numeric(out["net_return"], errors="coerce").to_numpy(dtype=float)
    raw = pd.to_numeric(out["raw_return"], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(scale) & (scale > 0) & np.isfinite(entry)
    r_net = np.full(len(out), np.nan)
    r_raw = np.full(len(out), np.nan)
    r_net[ok] = net[ok] * entry[ok] / scale[ok]
    r_raw[ok] = raw[ok] * entry[ok] / scale[ok]
    out["r_net"] = r_net
    out["r_raw"] = r_raw
    return out


def metrics_on(trades: pd.DataFrame, values: np.ndarray | pd.Series) -> dict[str, Any]:
    """Profit factor and expectancy via the production trade-metric function.

    Expectancy is ``avg_return_per_trade`` on the supplied column. Compounded
    total return is not used: it is not meaningful for an R series.
    """
    from forex_lab.backtest import _metrics_from_trades

    if trades is None or len(trades) == 0:
        empty = _metrics_from_trades(pd.DataFrame())
        return _slim_metrics(empty)
    frame = trades.copy()
    arr = np.asarray(values, dtype=float)
    frame["net_return"] = arr
    frame["win"] = (arr > 0).astype(int)
    return _slim_metrics(_metrics_from_trades(frame))


def _slim_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    pf = raw.get("profit_factor")
    avg = raw.get("avg_return_per_trade")
    pf_out: float | None
    if pf is None:
        pf_out = None
    else:
        pf_out = float(pf)
    avg_out = None if avg is None else float(avg)
    return {
        "n_trades": int(raw.get("n_trades") or 0),
        "profit_factor": pf_out,
        "profit_factor_infinite": pf_out == float("inf"),
        "expectancy": avg_out,
        "win_rate": raw.get("win_rate"),
    }


def trade_window_mask(trades: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> np.ndarray:
    """True for trades whose entry is in ``[start, end)``. None bounds are open."""
    if trades is None or len(trades) == 0 or "entry_time" not in trades.columns:
        return np.zeros(0, dtype=bool)
    ts = pd.to_datetime(trades["entry_time"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
    ok = np.ones(len(trades), dtype=bool)
    if start is not None:
        ok &= ts.to_numpy() >= np.datetime64(as_naive_ts(start).to_datetime64())
    if end is not None:
        ok &= ts.to_numpy() < np.datetime64(as_naive_ts(end).to_datetime64())
    return ok


def session_at(ts: Any, cfg: dict[str, Any] | None = None) -> str:
    from forex_lab.session import classify_session

    t = as_naive_ts(ts).to_pydatetime()
    return classify_session(t, cfg).name


def jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (np.floating,)):
        x = float(obj)
        return x if math.isfinite(x) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return as_naive_ts(obj).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return jsonable(obj.tolist())
    return str(obj)


def fmt_num(x: Any, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, (float, np.floating)):
        v = float(x)
        if math.isnan(v):
            return "n/a"
        if v == float("inf"):
            return "inf"
        if v == float("-inf"):
            return "-inf"
        return f"{v:.{nd}f}"
    if isinstance(x, (int, np.integer)) and not isinstance(x, bool):
        return str(int(x))
    return str(x)


def results_stamp(when: datetime | None = None) -> str:
    t = when or datetime.now(timezone.utc)
    return t.strftime("%Y%m%d")
