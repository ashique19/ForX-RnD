"""Model-versus-random, calibration, same-bar barrier conflicts, and cost versus move.

Research only. Costs and the confidence floor come from the in-memory cfg the
caller already built. This module does not change them.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.backtest import _simulate_trades
from forex_lab.config_loader import pip_size_for_pair
from forex_lab.features import LABEL_MAP

from research.phase0.common import (
    PRIOR_HOLDOUT_START,
    PUBLISHED_SCORE_LAST_BAR,
    FRESH_HOLDOUT_START,
    attach_r,
    metrics_on,
    session_at,
    trade_window_mask,
)


def block_bootstrap_ci(
    values: np.ndarray | pd.Series,
    *,
    block: int,
    n_boot: int,
    rng: np.random.Generator,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Circular block bootstrap of the mean. ``values`` must already be in time order."""
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    n = int(len(x))
    if n == 0 or n_boot <= 0:
        return {"n": n, "mean": None, "ci_lo": None, "ci_hi": None, "block": int(block), "n_boot": int(n_boot)}
    b = max(1, min(int(block), n))
    n_blocks = int(math.ceil(n / b))
    means = np.empty(int(n_boot), dtype=float)
    offsets = np.arange(b)
    for i in range(int(n_boot)):
        starts = rng.integers(0, n, size=n_blocks)
        take = ((starts[:, None] + offsets[None, :]) % n).ravel()[:n]
        means[i] = float(x[take].mean())
    lo, hi = np.quantile(means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {
        "n": n,
        "mean": float(x.mean()),
        "ci_lo": float(lo),
        "ci_hi": float(hi),
        "block": b,
        "n_boot": int(n_boot),
    }


def _ci_where(ci: dict[str, Any]) -> str:
    lo, hi = ci.get("ci_lo"), ci.get("ci_hi")
    if lo is None or hi is None:
        return "no trades"
    if hi < 0:
        return "entirely below zero"
    if lo > 0:
        return "entirely above zero"
    return "covers zero"


def _summarize_seeds(samples: list[float], model_mean: float | None) -> dict[str, Any]:
    arr = np.asarray(samples, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return {"n_seeds": 0, "mean": None, "p05": None, "p50": None, "p95": None, "frac_ge_model": None}
    frac = None
    if model_mean is not None and math.isfinite(model_mean):
        frac = float(np.mean(arr >= model_mean))
    return {
        "n_seeds": int(len(arr)),
        "mean": float(arr.mean()),
        "p05": float(np.quantile(arr, 0.05)),
        "p50": float(np.quantile(arr, 0.50)),
        "p95": float(np.quantile(arr, 0.95)),
        "frac_ge_model": frac,
    }


def resimulate_sides(
    df: pd.DataFrame,
    decisions: pd.Series,
    sides: np.ndarray,
    cfg: dict[str, Any],
    pair: str,
) -> pd.DataFrame:
    """Barrier simulation at the given decision times. One position is off.

    The model's realized entries are already non-overlapping. Turning the
    one-position rule off keeps those entry times when the new side exits on
    a different bar. Unit size is the simulator's unit. The signal series
    contains only those decisions, so the simulator does not walk every bar.
    """
    cfg2 = dict(cfg)
    cfg2["one_position"] = False
    stamps: list[pd.Timestamp] = []
    codes: list[int] = []
    index = pd.DatetimeIndex(df.index)
    for ts, side in zip(decisions, sides):
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert("UTC").tz_localize(None)
        if stamp not in index:
            continue
        name = str(side).upper()
        if name not in LABEL_MAP or name == "HOLD":
            continue
        stamps.append(stamp)
        codes.append(LABEL_MAP[name])
    signals = pd.Series(codes, index=pd.DatetimeIndex(stamps), dtype=int)
    if signals.index.has_duplicates:
        signals = signals[~signals.index.duplicated(keep="last")]
    trades = _simulate_trades(df, signals, cfg2, pair)
    return attach_r(trades, df, cfg2)


def _ordered_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or len(trades) == 0:
        return trades
    out = trades.copy()
    out["_t"] = pd.to_datetime(out["entry_time"])
    return out.sort_values("_t").drop(columns="_t").reset_index(drop=True)


def _window_block(trades: pd.DataFrame, start, end) -> dict[str, Any]:
    mask = trade_window_mask(trades, start, end)
    part = trades.loc[mask] if len(trades) else trades
    if len(part) == 0:
        return {"n_trades": 0, "net_return": metrics_on(part, []), "r_net": metrics_on(part, [])}
    return {
        "n_trades": int(len(part)),
        "net_return": metrics_on(part, part["net_return"]),
        "raw_return": metrics_on(part, part["raw_return"]),
        "r_net": metrics_on(part, part["r_net"]),
        "r_raw": metrics_on(part, part["r_raw"]),
    }


def model_vs_random(
    df: pd.DataFrame,
    trades: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    *,
    n_seeds: int,
    n_boot: int,
    block: int,
    rng: np.random.Generator,
) -> dict[str, Any]:
    """Zero-cost and after-cost expectancy versus random sides and flipped sides.

    Two nulls:

    * Resimulated random side. Same decision timestamps and unit size as the
      model's realized trades. The barrier path is recomputed, so holding
      time is the random side's own exit.
    * Sign permutation. The model's raw R path is kept (timing and exposure
      match) and only the sign is randomized. The same per-trade cost drag
      is subtracted, so after-cost R is ``sign * r_raw - (r_raw - r_net)``.

    Flipped sides are reported both ways. Uncertainty on the model and on the
    flipped series is a time-block bootstrap of trade-level R, contiguous in
    entry time. The random null's spread is the cross-seed distribution.
    """
    ordered = _ordered_trades(trades)
    windows = {
        "before_prior_holdout": _window_block(ordered, None, PRIOR_HOLDOUT_START),
        "published_holdout": _window_block(ordered, PRIOR_HOLDOUT_START, PUBLISHED_SCORE_LAST_BAR + pd.Timedelta(hours=1)),
        "unscored_tail_before_fresh_holdout": _window_block(
            ordered, PUBLISHED_SCORE_LAST_BAR + pd.Timedelta(hours=1), FRESH_HOLDOUT_START
        ),
    }
    base = {
        "n_trades": int(len(ordered)),
        "definition": {
            "resimulated": "same decision timestamps, unit size, new barrier path, one_position off",
            "sign_matched": "same raw R path and cost drag, random or flipped sign",
            "bootstrap": "circular block bootstrap of the mean, trades ordered by entry time",
            "r": "return * entry / (sl_atr * ATR14 at the decision bar)",
        },
        "windows_by_entry": windows,
    }
    if ordered is None or len(ordered) == 0:
        base["note"] = "no trades"
        return base

    r_net = ordered["r_net"].to_numpy(dtype=float)
    r_raw = ordered["r_raw"].to_numpy(dtype=float)
    cost_r = r_raw - r_net
    decisions = ordered["decision_time"]
    model_sides = ordered["side"].astype(str).str.upper().to_numpy()
    flipped_sides = np.where(model_sides == "BUY", "SELL", np.where(model_sides == "SELL", "BUY", "HOLD"))

    model_ci_net = block_bootstrap_ci(r_net, block=block, n_boot=n_boot, rng=rng)
    model_ci_raw = block_bootstrap_ci(r_raw, block=block, n_boot=n_boot, rng=rng)

    flip_trades = resimulate_sides(df, decisions, flipped_sides, cfg, pair)
    flip_trades = _ordered_trades(flip_trades)
    flip_ci_net = block_bootstrap_ci(flip_trades["r_net"], block=block, n_boot=n_boot, rng=rng) if len(flip_trades) else block_bootstrap_ci([], block=block, n_boot=n_boot, rng=rng)
    flip_ci_raw = block_bootstrap_ci(flip_trades["r_raw"], block=block, n_boot=n_boot, rng=rng) if len(flip_trades) else block_bootstrap_ci([], block=block, n_boot=n_boot, rng=rng)

    sign_flip_raw = -r_raw
    sign_flip_net = -r_raw - cost_r
    sign_flip_ci_net = block_bootstrap_ci(sign_flip_net, block=block, n_boot=n_boot, rng=rng)
    sign_flip_ci_raw = block_bootstrap_ci(sign_flip_raw, block=block, n_boot=n_boot, rng=rng)

    seed_raw: list[float] = []
    seed_net: list[float] = []
    sign_raw: list[float] = []
    sign_net: list[float] = []
    n_resim = 0
    for _ in range(int(n_seeds)):
        draw = rng.choice(np.array(["BUY", "SELL"]), size=len(ordered))
        sim = resimulate_sides(df, decisions, draw, cfg, pair)
        if len(sim):
            seed_raw.append(float(np.nanmean(sim["r_raw"].to_numpy(dtype=float))))
            seed_net.append(float(np.nanmean(sim["r_net"].to_numpy(dtype=float))))
            n_resim += 1
        signs = rng.choice(np.array([-1.0, 1.0]), size=len(ordered))
        finite = np.isfinite(r_raw) & np.isfinite(cost_r)
        if finite.any():
            sign_raw.append(float(np.mean(signs[finite] * r_raw[finite])))
            sign_net.append(float(np.mean(signs[finite] * r_raw[finite] - cost_r[finite])))

    model_net_mean = model_ci_net["mean"]
    model_raw_mean = model_ci_raw["mean"]
    out = dict(base)
    out.update(
        {
            "model_r_net": {**model_ci_net, "interval": _ci_where(model_ci_net)},
            "model_r_raw": {**model_ci_raw, "interval": _ci_where(model_ci_raw)},
            "model_net_return": metrics_on(ordered, ordered["net_return"]),
            "model_raw_return": metrics_on(ordered, ordered["raw_return"]),
            "flipped_resimulated_r_net": {**flip_ci_net, "interval": _ci_where(flip_ci_net), "n_trades": int(len(flip_trades))},
            "flipped_resimulated_r_raw": {**flip_ci_raw, "interval": _ci_where(flip_ci_raw)},
            "flipped_sign_r_net": {**sign_flip_ci_net, "interval": _ci_where(sign_flip_ci_net)},
            "flipped_sign_r_raw": {**sign_flip_ci_raw, "interval": _ci_where(sign_flip_ci_raw)},
            "random_resimulated_r_raw": _summarize_seeds(seed_raw, model_raw_mean),
            "random_resimulated_r_net": _summarize_seeds(seed_net, model_net_mean),
            "random_sign_r_raw": _summarize_seeds(sign_raw, model_raw_mean),
            "random_sign_r_net": _summarize_seeds(sign_net, model_net_mean),
            "n_seeds": int(n_seeds),
            "n_resimulated_seeds_with_trades": n_resim,
            "block": int(block),
            "n_boot": int(n_boot),
        }
    )
    return out


def _reason_bucket(reason: Any) -> str:
    r = str(reason or "").strip().lower()
    if r in {"sl", "sl_conflict"}:
        return "SL"
    if r == "tp":
        return "TP"
    if r in {"timeout", "horizon", "time"}:
        return "time"
    return "other"


def calibration(trades: pd.DataFrame, preds: pd.DataFrame) -> dict[str, Any]:
    """Confidence deciles of taken trades versus realized net R, and P&L by exit reason."""
    if trades is None or len(trades) == 0:
        return {"n_trades": 0, "deciles": [], "by_exit": [], "note": "no trades"}
    work = trades.copy()
    if preds is None or "confidence" not in getattr(preds, "columns", []):
        work["confidence"] = np.nan
        conf_note = "prediction frame has no confidence column"
    else:
        conf = preds["confidence"].copy()
        conf.index = naive_or_same(conf.index)
        decisions = pd.to_datetime(work["decision_time"])
        if getattr(decisions.dt, "tz", None) is not None:
            decisions = decisions.dt.tz_convert("UTC").dt.tz_localize(None)
        work["confidence"] = conf.reindex(pd.DatetimeIndex(decisions)).to_numpy()
        conf_note = None
    by_exit = []
    work["exit_bucket"] = work["exit_reason"].map(_reason_bucket) if "exit_reason" in work.columns else "other"
    for name, sub in work.groupby("exit_bucket", sort=False):
        r = pd.to_numeric(sub["r_net"], errors="coerce")
        by_exit.append(
            {
                "exit": str(name),
                "n": int(len(sub)),
                "sum_r_net": float(r.sum(skipna=True)),
                "mean_r_net": float(r.mean()) if r.notna().any() else None,
                "sum_net_return": float(pd.to_numeric(sub["net_return"], errors="coerce").sum(skipna=True)),
            }
        )
    conf_s = pd.to_numeric(work["confidence"], errors="coerce")
    usable = work.loc[conf_s.notna()].copy()
    deciles: list[dict[str, Any]] = []
    note = conf_note
    if len(usable) < 2:
        note = note or "fewer than 2 trades with confidence"
    else:
        n_bins = int(min(10, usable["confidence"].nunique()))
        try:
            usable["decile"] = pd.qcut(usable["confidence"], q=n_bins, duplicates="drop", labels=False)
        except ValueError:
            usable["decile"] = 0
        for d, sub in usable.groupby("decile"):
            r = pd.to_numeric(sub["r_net"], errors="coerce")
            c = pd.to_numeric(sub["confidence"], errors="coerce")
            deciles.append(
                {
                    "decile": int(d) + 1,
                    "n": int(len(sub)),
                    "conf_min": float(c.min()),
                    "conf_max": float(c.max()),
                    "mean_confidence": float(c.mean()),
                    "mean_r_net": float(r.mean()) if r.notna().any() else None,
                    "mean_r_raw": float(pd.to_numeric(sub["r_raw"], errors="coerce").mean()),
                    "win_rate": float((pd.to_numeric(sub["net_return"], errors="coerce") > 0).mean()),
                }
            )
        if n_bins < 10:
            note = (note + "; " if note else "") + f"{n_bins} bins (duplicate confidence collapsed the deciles)"
    return {"n_trades": int(len(work)), "n_with_confidence": int(conf_s.notna().sum()), "deciles": deciles, "by_exit": by_exit, "note": note}


def naive_or_same(index: pd.Index) -> pd.DatetimeIndex:
    di = pd.DatetimeIndex(pd.to_datetime(index))
    if di.tz is not None:
        di = di.tz_convert("UTC").tz_localize(None)
    return di


def same_bar_sl_tp(df: pd.DataFrame, trades: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    """Bars in a trade's life where both barriers are touched, and the share of trades.

    The scan matches ``_first_touch_exit``: from the entry bar, up to ``horizon``
    bars, stopping at the first barrier event. A same-bar conflict is the exit,
    and production books it as ``sl_conflict`` (the stop fills).
    """
    from forex_lab.features import true_range_atr

    empty = {
        "n_trades": 0,
        "trades_affected": 0,
        "share_of_trades": None,
        "ambiguous_bars": 0,
        "simulator_sl_conflict": 0,
        "sl_conflict_matches_scan": True,
    }
    if trades is None or len(trades) == 0:
        return empty
    horizon = int(cfg.get("horizon", 8))
    path = str((cfg.get("barrier") or {}).get("path", "high_low")).lower()
    sl_mult = float((cfg.get("barrier") or {}).get("sl_atr", 2.0))
    tp_mult = float((cfg.get("barrier") or {}).get("tp_atr", 2.0))
    use_hl = path != "close"
    index = naive_or_same(df.index)
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    close = df["Close"].to_numpy(dtype=float)
    atr = true_range_atr(df, int(cfg.get("atr_period", 14)))
    pos = pd.Series(np.arange(len(index)), index=index)
    affected = 0
    bars = 0
    unmatched = 0
    for _, tr in trades.iterrows():
        entry_ts = pd.Timestamp(tr["entry_time"])
        if entry_ts.tzinfo is not None:
            entry_ts = entry_ts.tz_convert("UTC").tz_localize(None)
        dec_ts = pd.Timestamp(tr["decision_time"])
        if dec_ts.tzinfo is not None:
            dec_ts = dec_ts.tz_convert("UTC").tz_localize(None)
        if entry_ts not in pos.index:
            unmatched += 1
            continue
        entry_loc = int(pos.loc[entry_ts])
        side_name = str(tr["side"]).upper()
        side = 1 if side_name == "BUY" else -1
        try:
            atr_e = float(atr.loc[dec_ts])
        except (KeyError, TypeError, ValueError):
            unmatched += 1
            continue
        if not np.isfinite(atr_e) or atr_e <= 0:
            unmatched += 1
            continue
        entry = float(tr["entry"])
        tp_dist = tp_mult * atr_e
        sl_dist = sl_mult * atr_e
        end = min(len(index), entry_loc + horizon)
        hit = False
        for i in range(entry_loc, end):
            px_up = high[i] if use_hl else close[i]
            px_dn = low[i] if use_hl else close[i]
            if side == 1:
                hit_tp = px_up >= entry + tp_dist
                hit_sl = px_dn <= entry - sl_dist
            else:
                hit_tp = px_dn <= entry - tp_dist
                hit_sl = px_up >= entry + sl_dist
            if hit_tp and hit_sl:
                bars += 1
                hit = True
                break
            if hit_tp or hit_sl:
                break
        if hit:
            affected += 1
    n = int(len(trades))
    sl_conflict = 0
    if "exit_reason" in trades.columns:
        sl_conflict = int(trades["exit_reason"].astype(str).str.lower().isin(["sl_conflict"]).sum())
    return {
        "n_trades": n,
        "trades_affected": int(affected),
        "share_of_trades": float(affected / n) if n else None,
        "ambiguous_bars": int(bars),
        "unmatched_trades": int(unmatched),
        "simulator_sl_conflict": sl_conflict,
        "sl_conflict_matches_scan": sl_conflict == affected,
        "path": path,
        "note": "Same-bar high and low both through the barriers. Production fills the stop (sl_conflict).",
    }


def cost_vs_move(df: pd.DataFrame, cfg: dict[str, Any], pair: str, cost_pips: float) -> dict[str, Any]:
    """Typical absolute move from the entry to the close ``horizon`` bars later, by session.

    Diagnostic only. The move is ``|Close[t+horizon] - entry|`` in pips, with
    entry = Open[t+1] (or Close[t] when entry_timing is same_close). Session is
    the desk clock at the decision bar, so London and New York overlap is one
    bucket and a bar is not counted twice.
    """
    horizon = int(cfg.get("horizon", 8))
    timing = str(cfg.get("entry_timing", "next_open")).lower()
    pip = float(pip_size_for_pair(pair, cfg))
    close = pd.to_numeric(df["Close"], errors="coerce")
    open_ = pd.to_numeric(df["Open"], errors="coerce")
    if timing == "same_close":
        entry = close
    else:
        entry = open_.shift(-1)
    exit_px = close.shift(-horizon)
    pips = (exit_px - entry).abs() / pip
    idx = naive_or_same(df.index)
    sessions = [session_at(ts, cfg) for ts in idx]
    frame = pd.DataFrame({"session": sessions, "abs_pips": pips.to_numpy(dtype=float)}, index=idx)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    rows = [_move_row("ALL", frame["abs_pips"], cost_pips)]
    for name, sub in frame.groupby("session", sort=True):
        rows.append(_move_row(str(name), sub["abs_pips"], cost_pips))
    return {
        "pair": pair.upper(),
        "horizon": horizon,
        "entry_timing": timing,
        "cost_pips": float(cost_pips),
        "pip_size": pip,
        "n_bars": int(len(frame)),
        "by_session": rows,
        "note": "Diagnostic only. No threshold, session filter, or cost setting is changed.",
    }


def _move_row(session: str, pips: pd.Series, cost_pips: float) -> dict[str, Any]:
    s = pd.to_numeric(pips, errors="coerce").dropna()
    if len(s) == 0:
        return {"session": session, "n": 0, "median_abs_pips": None, "mean_abs_pips": None, "cost_pips": cost_pips, "cost_over_median": None, "frac_move_gt_cost": None}
    med = float(s.median())
    return {
        "session": session,
        "n": int(len(s)),
        "median_abs_pips": med,
        "mean_abs_pips": float(s.mean()),
        "cost_pips": float(cost_pips),
        "cost_over_median": None if med <= 0 else float(cost_pips / med),
        "frac_move_gt_cost": float((s > cost_pips).mean()),
    }
