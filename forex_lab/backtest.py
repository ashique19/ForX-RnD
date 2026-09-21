"""Walk-forward backtest with cost model and baseline comparison."""
from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.config_loader import pip_size_for_pair
from forex_lab.features import (
    INV_LABEL_MAP,
    LABEL_MAP,
    label_distribution,
    make_dataset,
    true_range_atr,
)
from forex_lab.model import apply_signal_filters, build_model, fit_model, predict_proba_aligned
from forex_lab.paths import resolve_under_root


def _spread_cost_frac(pair: str, cfg: dict[str, Any], price: float) -> float:
    pips = float(cfg.get("spread_pips", 1.0)) + float(cfg.get("commission_pips", 0.0))
    pip = pip_size_for_pair(pair, cfg)
    return (pips * pip) / max(price, 1e-12)


def _sma_crossover_signals(close: pd.Series, fast: int = 10, slow: int = 50) -> pd.Series:
    sma_f = close.rolling(fast).mean()
    sma_s = close.rolling(slow).mean()
    sig = pd.Series(LABEL_MAP["HOLD"], index=close.index, dtype=int)
    sig = sig.mask(sma_f > sma_s, LABEL_MAP["BUY"])
    sig = sig.mask(sma_f < sma_s, LABEL_MAP["SELL"])
    return sig


def _barrier_distances(atr_value: float, cfg: dict[str, Any]) -> tuple[float, float]:
    b = cfg.get("barrier") or {}
    tp = float(b.get("tp_atr", 2.0)) * float(atr_value)
    sl = float(b.get("sl_atr", 2.0)) * float(atr_value)
    return tp, sl


def _first_touch_exit(
    ohlcv: pd.DataFrame,
    entry_loc: int,
    horizon: int,
    side: int,
    entry: float,
    tp_dist: float,
    sl_dist: float,
    path: str,
) -> tuple[int, float, str]:
    """Walk horizon bars from entry_loc inclusive. Returns (exit_loc, exit_px, reason)."""
    n = len(ohlcv)
    end = min(n, entry_loc + horizon)
    use_hl = path != "close"
    high = ohlcv["High"].to_numpy()
    low = ohlcv["Low"].to_numpy()
    close = ohlcv["Close"].to_numpy()
    up = entry + tp_dist
    dn = entry - sl_dist
    for i in range(entry_loc, end):
        px_up = high[i] if use_hl else close[i]
        px_dn = low[i] if use_hl else close[i]
        if side == 1:
            hit_tp = px_up >= up
            hit_sl = px_dn <= dn
        else:
            hit_tp = px_dn <= dn
            hit_sl = px_up >= up
        if hit_tp and hit_sl:
            # Ambiguous same-bar path: pessimistic stop
            sl_px = entry - sl_dist if side == 1 else entry + sl_dist
            return i, float(sl_px), "sl_conflict"
        if hit_sl:
            sl_px = entry - sl_dist if side == 1 else entry + sl_dist
            return i, float(sl_px), "sl"
        if hit_tp:
            tp_px = entry + tp_dist if side == 1 else entry - tp_dist
            return i, float(tp_px), "tp"
    exit_loc = end - 1
    return exit_loc, float(close[exit_loc]), "timeout"


def _simulate_trades(
    ohlcv: pd.DataFrame,
    signals: pd.Series,
    cfg: dict[str, Any],
    pair: str,
    atr: pd.Series | None = None,
) -> pd.DataFrame:
    """Simulate trades from decision-bar signals.

    Default: enter next bar's Open (no same-bar fill), exit on first barrier
    touch or after ``horizon`` bars. Spread+commission deducted on entry.
    When ``one_position`` is true, skip signals until the prior trade has exited.
    """
    horizon = int(cfg.get("horizon", 8))
    entry_timing = str(cfg.get("entry_timing", "next_open")).lower()
    one_pos = bool(cfg.get("one_position", True))
    scheme = str(cfg.get("label_scheme", "triple_barrier")).lower()
    use_barriers = bool(cfg.get("use_tp_sl", scheme == "triple_barrier"))
    path = str((cfg.get("barrier") or {}).get("path", "high_low")).lower()

    if atr is None:
        atr = true_range_atr(ohlcv, int(cfg.get("atr_period", 14)))

    rows: list[dict[str, Any]] = []
    next_free_loc = 0
    index = ohlcv.index

    for ts, sig in signals.items():
        if int(sig) == LABEL_MAP["HOLD"]:
            continue
        loc = ohlcv.index.get_loc(ts)
        if isinstance(loc, slice) or isinstance(loc, np.ndarray):
            continue
        loc = int(loc)
        if one_pos and loc < next_free_loc:
            continue

        if entry_timing == "same_close":
            entry_loc = loc
            if entry_loc >= len(ohlcv):
                continue
            entry = float(ohlcv["Close"].iloc[entry_loc])
            scan_start = entry_loc + 1
        else:
            entry_loc = loc + 1
            if entry_loc >= len(ohlcv):
                continue
            entry = float(ohlcv["Open"].iloc[entry_loc])
            scan_start = entry_loc

        side = 1 if int(sig) == LABEL_MAP["BUY"] else -1
        cost = _spread_cost_frac(pair, cfg, entry)
        reason = "horizon"
        exit_px: float
        exit_loc: int

        if use_barriers:
            try:
                atr_e = float(atr.loc[ts])
            except (KeyError, TypeError, ValueError):
                atr_e = float(atr.iloc[loc]) if loc < len(atr) else float("nan")
            if not np.isfinite(atr_e) or atr_e <= 0:
                continue
            tp_dist, sl_dist = _barrier_distances(atr_e, cfg)
            exit_loc, exit_px, reason = _first_touch_exit(
                ohlcv, scan_start, horizon, side, entry, tp_dist, sl_dist, path
            )
            raw_ret = side * (exit_px / entry - 1.0)
        else:
            exit_loc = scan_start + horizon - 1
            if exit_loc >= len(ohlcv):
                continue
            exit_px = float(ohlcv["Close"].iloc[exit_loc])
            raw_ret = side * (exit_px / entry - 1.0)
            reason = "horizon"

        net = raw_ret - cost
        rows.append(
            {
                "entry_time": index[entry_loc],
                "decision_time": ts,
                "side": INV_LABEL_MAP[int(sig)],
                "entry": entry,
                "exit": exit_px,
                "raw_return": raw_ret,
                "net_return": net,
                "win": int(net > 0),
                "exit_reason": reason,
            }
        )
        if one_pos:
            next_free_loc = exit_loc + 1
    return pd.DataFrame(rows)


def _binomial_ci(p: float | None, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if p is None or n <= 0:
        return None, None
    se = math.sqrt(max(p * (1.0 - p), 0.0) / n)
    return max(0.0, p - z * se), min(1.0, p + z * se)


def _metrics_from_trades(trades: pd.DataFrame) -> dict[str, Any]:
    if trades is None or trades.empty:
        return {
            "n_trades": 0,
            "win_rate": None,
            "win_rate_lo": None,
            "win_rate_hi": None,
            "win_rate_buy": None,
            "win_rate_sell": None,
            "n_buy": 0,
            "n_sell": 0,
            "avg_return_per_trade": None,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": None,
        }
    n = len(trades)
    wr = float(trades["win"].mean())
    lo, hi = _binomial_ci(wr, n)
    buy = trades[trades["side"] == "BUY"]
    sell = trades[trades["side"] == "SELL"]
    avg = float(trades["net_return"].mean())
    equity = (1.0 + trades["net_return"]).cumprod()
    total = float(equity.iloc[-1] - 1.0)
    peak = equity.cummax()
    dd = float((equity / peak - 1.0).min())
    gains = trades.loc[trades["net_return"] > 0, "net_return"].sum()
    losses = -trades.loc[trades["net_return"] < 0, "net_return"].sum()
    pf = float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else None)
    return {
        "n_trades": int(n),
        "win_rate": wr,
        "win_rate_lo": lo,
        "win_rate_hi": hi,
        "win_rate_buy": float(buy["win"].mean()) if len(buy) else None,
        "win_rate_sell": float(sell["win"].mean()) if len(sell) else None,
        "n_buy": int(len(buy)),
        "n_sell": int(len(sell)),
        "avg_return_per_trade": avg,
        "total_return": total,
        "max_drawdown": dd,
        "profit_factor": pf,
    }


def _fold_stability(fold_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not fold_rows:
        return {"n_folds": 0}
    df = pd.DataFrame(fold_rows)
    traded = df[df["n_trades"] > 0]
    out: dict[str, Any] = {
        "n_folds": int(len(df)),
        "folds_with_trades": int(len(traded)),
        "n_trades_mean": float(df["n_trades"].mean()),
    }
    for col in ("win_rate", "avg_return_per_trade", "profit_factor", "total_return"):
        s = traded[col].replace([np.inf, -np.inf], np.nan).dropna() if len(traded) else pd.Series(dtype=float)
        out[f"{col}_mean"] = float(s.mean()) if len(s) else None
        out[f"{col}_std"] = float(s.std(ddof=1)) if len(s) > 1 else None
    return out


def walk_forward_backtest(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    model_type: str | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    X, y, _ohlcv = make_dataset(df, cfg)
    wf = cfg.get("walk_forward") or {}
    train_bars = int(wf.get("train_bars", 2000))
    test_bars = int(wf.get("test_bars", 250))
    step_bars = int(wf.get("step_bars", 250))
    min_train = int(wf.get("min_train_bars", 500))
    balanced = bool((cfg.get("model") or {}).get("class_weight_balanced", True))

    n = len(X)
    if n < min_train + 50:
        train_bars = max(min_train, n // 2)
        test_bars = max(50, n // 10)
        step_bars = test_bars

    all_preds = []
    fold_rows: list[dict[str, Any]] = []
    start = train_bars
    if start >= n:
        start = max(min_train, int(n * 0.6))

    atr_full = true_range_atr(df, int(cfg.get("atr_period", 14)))
    fold = 0
    while start < n:
        tr_start = max(0, start - train_bars)
        tr_end = start
        te_end = min(n, start + test_bars)
        if tr_end - tr_start < min_train or te_end - start < 10:
            break
        X_tr, y_tr = X.iloc[tr_start:tr_end], y.iloc[tr_start:tr_end]
        X_te = X.iloc[start:te_end]
        y_te = y.iloc[start:te_end]
        model, mname = build_model(cfg, model_type)
        fit_model(model, X_tr, y_tr, balanced=balanced)
        pred = model.predict(X_te)
        proba = predict_proba_aligned(model, X_te)
        fold_df = pd.DataFrame({"pred_raw": pred}, index=X_te.index)
        if proba is not None:
            fold_df["p_sell"] = proba[:, LABEL_MAP["SELL"]]
            fold_df["p_hold"] = proba[:, LABEL_MAP["HOLD"]]
            fold_df["p_buy"] = proba[:, LABEL_MAP["BUY"]]
            fold_df["confidence"] = proba.max(axis=1)
            fold_df["dir_edge"] = np.abs(
                proba[:, LABEL_MAP["BUY"]] - proba[:, LABEL_MAP["SELL"]]
            )
        fold_df["pred"] = apply_signal_filters(fold_df, cfg)
        all_preds.append(fold_df)

        fold_trades = _simulate_trades(
            df, fold_df["pred"], cfg, pair, atr=atr_full
        )
        fm = _metrics_from_trades(fold_trades)
        fm["fold"] = fold
        fm["n_test_bars"] = int(len(X_te))
        if len(y_te):
            fm["label_accuracy"] = float((fold_df["pred"] == y_te).mean())
        fold_rows.append(fm)
        fold += 1
        start += step_bars

    if not all_preds:
        raise RuntimeError("Walk-forward produced no folds — not enough data")

    preds = pd.concat(all_preds)
    preds = preds[~preds.index.duplicated(keep="last")]

    model_trades = _simulate_trades(df, preds["pred"], cfg, pair, atr=atr_full)
    model_metrics = _metrics_from_trades(model_trades)

    sma_sig = (
        _sma_crossover_signals(df["Close"]).reindex(preds.index).fillna(LABEL_MAP["HOLD"]).astype(int)
    )
    sma_trades = _simulate_trades(df, sma_sig, cfg, pair, atr=atr_full)
    sma_metrics = _metrics_from_trades(sma_trades)

    always_long = pd.Series(LABEL_MAP["BUY"], index=preds.index, dtype=int)
    long_trades = _simulate_trades(df, always_long, cfg, pair, atr=atr_full)
    long_metrics = _metrics_from_trades(long_trades)

    y_te = y.reindex(preds.index).dropna()
    overlap = y_te.index.intersection(preds.index)
    dir_acc = None
    raw_acc = None
    if len(overlap):
        dir_acc = float((preds.loc[overlap, "pred"] == y_te.loc[overlap]).mean())
        if "pred_raw" in preds.columns:
            raw_acc = float((preds.loc[overlap, "pred_raw"] == y_te.loc[overlap]).mean())

    b = cfg.get("barrier") or {}
    sig_cfg = cfg.get("signals") or {}
    result = {
        "pair": pair.upper(),
        "model_type": (model_type or (cfg.get("model") or {}).get("type") or "xgboost"),
        "folds": fold,
        "n_test_bars": int(len(preds)),
        "label_accuracy_on_test": dir_acc,
        "label_accuracy_unfiltered": raw_acc,
        "label_scheme": str(cfg.get("label_scheme", "triple_barrier")),
        "label_distribution": label_distribution(y),
        "model": model_metrics,
        "baseline_sma_crossover": sma_metrics,
        "baseline_always_long": long_metrics,
        "fold_stability": _fold_stability(fold_rows),
        "costs": {
            "spread_pips": cfg.get("spread_pips"),
            "commission_pips": cfg.get("commission_pips", 0.0),
            "horizon": cfg.get("horizon"),
            "label_threshold": cfg.get("label_threshold"),
            "entry_timing": cfg.get("entry_timing", "next_open"),
            "one_position": bool(cfg.get("one_position", True)),
            "use_tp_sl": bool(cfg.get("use_tp_sl", True)),
            "tp_atr": b.get("tp_atr"),
            "sl_atr": b.get("sl_atr"),
            "min_confidence": sig_cfg.get("min_confidence"),
            "min_dir_edge": sig_cfg.get("min_dir_edge"),
        },
    }
    return result, model_trades, preds


def write_report(result: dict[str, Any], cfg: dict[str, Any], trades: pd.DataFrame | None = None) -> str:
    reports_dir = resolve_under_root(cfg.get("paths", {}).get("reports_dir", "reports"))
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / "latest_report.md"
    json_path = reports_dir / "latest_metrics.json"

    def fmt_pct(x):
        if x is None:
            return "n/a"
        return f"{100 * x:.2f}%"

    def fmt_num(x):
        if x is None:
            return "n/a"
        if x == float("inf"):
            return "inf"
        return f"{x:.6f}"

    def fmt_ci(m):
        lo, hi = m.get("win_rate_lo"), m.get("win_rate_hi")
        if lo is None or hi is None:
            return "n/a"
        return f"{100 * lo:.2f}% – {100 * hi:.2f}%"

    m = result["model"]
    sma = result["baseline_sma_crossover"]
    lng = result["baseline_always_long"]
    costs = result.get("costs") or {}
    dist = result.get("label_distribution") or {}
    stab = result.get("fold_stability") or {}
    scheme = result.get("label_scheme", "triple_barrier")

    if scheme == "forward_return":
        scheme_lines = [
            "For each decision bar `t` (legacy scheme):",
            "",
            "```",
            "entry = Open[t+1]  (or Close[t] if entry_timing=same_close)",
            "forward_return = Close[t+horizon] / entry - 1",
            "BUY  if forward_return >  +label_threshold",
            "SELL if forward_return <  -label_threshold",
            "HOLD otherwise",
            "```",
        ]
    else:
        scheme_lines = [
            "For each decision bar `t` (features at/before `t` only):",
            "",
            "```",
            "fill      = Open[t+1]          # next-bar open; not used as a feature",
            "ATR       = Wilder ATR at t    # causal",
            "upper     = fill + tp_atr * ATR",
            "lower     = fill - sl_atr * ATR",
            "scan      = High/Low of bars t+1 .. t+horizon",
            "BUY  if upper is touched first",
            "SELL if lower is touched first",
            "HOLD if timeout, or both barriers in the same bar",
            "```",
            "",
            "Backtest uses the same fill, barriers, and (optional) one-position rule.",
            "Low-confidence BUY/SELL predictions are forced to HOLD before trading.",
        ]

    lines = [
        f"# Forex Lab Report — {result['pair']}",
        "",
        "> Research only. No live orders. yfinance ≠ broker quotes. Past ≠ future.",
        "> This is **not** a profitable trading system claim — compare vs baselines on the same windows.",
        "",
        f"- Model: `{result['model_type']}`",
        f"- Walk-forward folds: **{result['folds']}**",
        f"- Test bars: **{result['n_test_bars']}**",
        f"- Label scheme: `{scheme}`",
        f"- Label accuracy (filtered signals vs labels): **{fmt_pct(result.get('label_accuracy_on_test'))}**",
        f"- Label accuracy (raw argmax, unfiltered): **{fmt_pct(result.get('label_accuracy_unfiltered'))}**",
        (
            f"- Horizon: {costs.get('horizon')} bars | TP/SL ATR: "
            f"{costs.get('tp_atr')}/{costs.get('sl_atr')} | "
            f"entry: {costs.get('entry_timing')} | spread: {costs.get('spread_pips')} pips"
            f" + commission {costs.get('commission_pips')} pips"
        ),
        f"- Filters: min_confidence={costs.get('min_confidence')} min_dir_edge={costs.get('min_dir_edge')} "
        f"one_position={costs.get('one_position')}",
        (
            f"- Label mix (full labeled set): BUY {fmt_pct(dist.get('buy'))} / "
            f"SELL {fmt_pct(dist.get('sell'))} / HOLD {fmt_pct(dist.get('hold'))} (n={dist.get('n', 0)})"
        ),
        "",
        "## Label scheme",
        "",
        *scheme_lines,
        "",
        "## Model success metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| # trades | {m['n_trades']} |",
        f"| Win rate (overall) | {fmt_pct(m['win_rate'])} |",
        f"| Win rate 95% CI | {fmt_ci(m)} |",
        f"| Win rate BUY | {fmt_pct(m.get('win_rate_buy'))} (n={m.get('n_buy', 0)}) |",
        f"| Win rate SELL | {fmt_pct(m.get('win_rate_sell'))} (n={m.get('n_sell', 0)}) |",
        f"| Avg return / trade | {fmt_num(m['avg_return_per_trade'])} |",
        f"| Total return (compound) | {fmt_pct(m['total_return']) if m['total_return'] is not None else 'n/a'} |",
        f"| Max drawdown | {fmt_pct(m['max_drawdown']) if m['max_drawdown'] is not None else 'n/a'} |",
        f"| Profit factor | {fmt_num(m['profit_factor'])} |",
        "",
        "## Baselines",
        "",
        "| Strategy | Trades | Win rate | Total return | Max DD | Profit factor |",
        "|---|---:|---:|---:|---:|---:|",
        f"| Model | {m['n_trades']} | {fmt_pct(m['win_rate'])} | {fmt_pct(m['total_return'])} | {fmt_pct(m['max_drawdown'])} | {fmt_num(m['profit_factor'])} |",
        f"| SMA crossover | {sma['n_trades']} | {fmt_pct(sma['win_rate'])} | {fmt_pct(sma['total_return'])} | {fmt_pct(sma['max_drawdown'])} | {fmt_num(sma['profit_factor'])} |",
        f"| Always long | {lng['n_trades']} | {fmt_pct(lng['win_rate'])} | {fmt_pct(lng['total_return'])} | {fmt_pct(lng['max_drawdown'])} | {fmt_num(lng['profit_factor'])} |",
        "",
        "## Walk-forward fold stability",
        "",
        "Per-fold trade metrics (model, same costs). Std is sample std across folds with ≥1 trade.",
        "",
        "| Stat | Value |",
        "|---|---|",
        f"| Folds | {stab.get('n_folds', 0)} ({stab.get('folds_with_trades', 0)} with trades) |",
        f"| Trades / fold (mean) | {fmt_num(stab.get('n_trades_mean'))} |",
        f"| Win rate mean ± std | {fmt_pct(stab.get('win_rate_mean'))} ± {fmt_pct(stab.get('win_rate_std'))} |",
        f"| Avg return/trade mean ± std | {fmt_num(stab.get('avg_return_per_trade_mean'))} ± {fmt_num(stab.get('avg_return_per_trade_std'))} |",
        f"| Profit factor mean ± std | {fmt_num(stab.get('profit_factor_mean'))} ± {fmt_num(stab.get('profit_factor_std'))} |",
        "",
        "## How to read success rate",
        "",
        "- **Win rate** = fraction of closed trades with net_return > 0 after spread + commission.",
        "- **95% CI** is a normal-approx binomial interval on that win rate; it is not a live-trading guarantee.",
        "- Compare model win rate / total return / profit factor / drawdown against SMA and always-long **on the same walk-forward windows and cost model**.",
        "- A higher win rate alone is not enough if avg return or profit factor is worse than baseline.",
        "- Fold std tells you whether a headline number is stable or driven by a few windows.",
        "",
        "## Disclaimer",
        "",
        "This lab is for research education only. It does **not** place broker orders.",
        "Data from yfinance is not identical to broker executable quotes. Past backtest results do not predict future performance.",
        "Even if the model beats these baselines, that is a research signal — not evidence of a deployable edge after slippage, gaps, and session holes.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if trades is not None and not trades.empty:
        trades.to_csv(reports_dir / "latest_trades.csv", index=False)
    print(f"[backtest] wrote {md_path}")
    return str(md_path)
