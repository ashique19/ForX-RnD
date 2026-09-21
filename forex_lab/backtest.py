"""Walk-forward backtest with cost model and baseline comparison."""
from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.config_loader import pip_size_for_pair
from forex_lab.features import INV_LABEL_MAP, LABEL_MAP, make_dataset
from forex_lab.model import build_model
from forex_lab.paths import resolve_under_root


def _spread_cost_frac(pair: str, cfg: dict[str, Any], price: float) -> float:
    pips = float(cfg.get("spread_pips", 1.0))
    pip = pip_size_for_pair(pair, cfg)
    # Convert pip cost to fractional return at current price
    return (pips * pip) / max(price, 1e-12)


def _sma_crossover_signals(close: pd.Series, fast: int = 10, slow: int = 50) -> pd.Series:
    sma_f = close.rolling(fast).mean()
    sma_s = close.rolling(slow).mean()
    sig = pd.Series(LABEL_MAP["HOLD"], index=close.index, dtype=int)
    sig = sig.mask(sma_f > sma_s, LABEL_MAP["BUY"])
    sig = sig.mask(sma_f < sma_s, LABEL_MAP["SELL"])
    return sig


def _simulate_trades(
    ohlcv: pd.DataFrame,
    signals: pd.Series,
    cfg: dict[str, Any],
    pair: str,
) -> pd.DataFrame:
    """Simulate trades: enter next bar open conceptually via close[t]; exit after horizon.

    Uses close-to-close over horizon for simplicity (research proxy).
    Spread cost deducted on entry for BUY/SELL (not HOLD).
    """
    horizon = int(cfg.get("horizon", 4))
    close = ohlcv["Close"]
    rows = []
    for ts, sig in signals.items():
        if sig == LABEL_MAP["HOLD"]:
            continue
        # Need forward exit
        loc = ohlcv.index.get_loc(ts)
        if isinstance(loc, slice):
            continue
        if isinstance(loc, np.ndarray):
            loc = int(loc[0])
        exit_loc = loc + horizon
        if exit_loc >= len(ohlcv):
            continue
        entry = float(close.iloc[loc])
        exit_px = float(close.iloc[exit_loc])
        side = 1 if sig == LABEL_MAP["BUY"] else -1
        raw_ret = side * (exit_px / entry - 1.0)
        cost = _spread_cost_frac(pair, cfg, entry)
        net = raw_ret - cost

        # Optional TP/SL path mark (informational; primary exit is horizon unless use_tp_sl)
        if cfg.get("use_tp_sl"):
            tp = float(cfg.get("take_profit", 0.0015))
            sl = float(cfg.get("stop_loss", 0.0010))
            window = ohlcv.iloc[loc + 1 : exit_loc + 1]
            if side == 1:
                hit_tp = (window["High"] >= entry * (1 + tp)).any()
                hit_sl = (window["Low"] <= entry * (1 - sl)).any()
            else:
                hit_tp = (window["Low"] <= entry * (1 - tp)).any()
                hit_sl = (window["High"] >= entry * (1 + sl)).any()
            if hit_sl and not hit_tp:
                net = -sl - cost
            elif hit_tp:
                net = tp - cost

        rows.append(
            {
                "entry_time": ts,
                "side": INV_LABEL_MAP[int(sig)],
                "entry": entry,
                "exit": exit_px,
                "raw_return": raw_ret,
                "net_return": net,
                "win": int(net > 0),
            }
        )
    return pd.DataFrame(rows)


def _metrics_from_trades(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty:
        return {
            "n_trades": 0,
            "win_rate": None,
            "win_rate_buy": None,
            "win_rate_sell": None,
            "avg_return_per_trade": None,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": None,
        }
    n = len(trades)
    wr = float(trades["win"].mean())
    buy = trades[trades["side"] == "BUY"]
    sell = trades[trades["side"] == "SELL"]
    avg = float(trades["net_return"].mean())
    # Compound total return of sequential trades
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
        "win_rate_buy": float(buy["win"].mean()) if len(buy) else None,
        "win_rate_sell": float(sell["win"].mean()) if len(sell) else None,
        "n_buy": int(len(buy)),
        "n_sell": int(len(sell)),
        "avg_return_per_trade": avg,
        "total_return": total,
        "max_drawdown": dd,
        "profit_factor": pf,
    }


def walk_forward_backtest(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    model_type: str | None = None,
) -> dict[str, Any]:
    X, y, ohlcv = make_dataset(df, cfg)
    wf = cfg.get("walk_forward") or {}
    train_bars = int(wf.get("train_bars", 2000))
    test_bars = int(wf.get("test_bars", 250))
    step_bars = int(wf.get("step_bars", 250))
    min_train = int(wf.get("min_train_bars", 500))

    n = len(X)
    if n < min_train + 50:
        # Shrink windows for short series (e.g. synthetic demo)
        train_bars = max(min_train, n // 2)
        test_bars = max(50, n // 10)
        step_bars = test_bars

    all_preds = []
    start = train_bars
    if start >= n:
        start = max(min_train, int(n * 0.6))

    fold = 0
    while start < n:
        tr_start = max(0, start - train_bars)
        tr_end = start
        te_end = min(n, start + test_bars)
        if tr_end - tr_start < min_train or te_end - start < 10:
            break
        X_tr, y_tr = X.iloc[tr_start:tr_end], y.iloc[tr_start:tr_end]
        X_te = X.iloc[start:te_end]
        model, mname = build_model(cfg, model_type)
        model.fit(X_tr, y_tr)
        pred = model.predict(X_te)
        proba = None
        if hasattr(model, "predict_proba"):
            proba = model.predict_proba(X_te)
        fold_df = pd.DataFrame({"pred": pred}, index=X_te.index)
        if proba is not None:
            fold_df["confidence"] = proba.max(axis=1)
        all_preds.append(fold_df)
        fold += 1
        start += step_bars

    if not all_preds:
        raise RuntimeError("Walk-forward produced no folds — not enough data")

    preds = pd.concat(all_preds)
    preds = preds[~preds.index.duplicated(keep="last")]
    ohlcv_te = ohlcv.loc[preds.index]
    close_te = ohlcv_te["Close"]

    model_trades = _simulate_trades(ohlcv_te, preds["pred"], cfg, pair)
    model_metrics = _metrics_from_trades(model_trades)

    # Baselines on same test index
    sma_sig = _sma_crossover_signals(df["Close"]).reindex(preds.index).fillna(LABEL_MAP["HOLD"]).astype(int)
    sma_trades = _simulate_trades(ohlcv_te, sma_sig, cfg, pair)
    sma_metrics = _metrics_from_trades(sma_trades)

    always_long = pd.Series(LABEL_MAP["BUY"], index=preds.index, dtype=int)
    long_trades = _simulate_trades(ohlcv_te, always_long, cfg, pair)
    long_metrics = _metrics_from_trades(long_trades)

    # Directional accuracy vs true labels on overlap
    y_te = y.reindex(preds.index).dropna()
    overlap = y_te.index.intersection(preds.index)
    dir_acc = None
    if len(overlap):
        dir_acc = float((preds.loc[overlap, "pred"] == y_te.loc[overlap]).mean())

    result = {
        "pair": pair.upper(),
        "model_type": (model_type or (cfg.get("model") or {}).get("type") or "xgboost"),
        "folds": fold,
        "n_test_bars": int(len(preds)),
        "label_accuracy_on_test": dir_acc,
        "model": model_metrics,
        "baseline_sma_crossover": sma_metrics,
        "baseline_always_long": long_metrics,
        "costs": {
            "spread_pips": cfg.get("spread_pips"),
            "horizon": cfg.get("horizon"),
            "label_threshold": cfg.get("label_threshold"),
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

    m = result["model"]
    sma = result["baseline_sma_crossover"]
    lng = result["baseline_always_long"]

    lines = [
        f"# Forex Lab Report — {result['pair']}",
        "",
        "> Research only. No live orders. yfinance ≠ broker quotes. Past ≠ future.",
        "",
        f"- Model: `{result['model_type']}`",
        f"- Walk-forward folds: **{result['folds']}**",
        f"- Test bars: **{result['n_test_bars']}**",
        f"- Label accuracy (test): **{fmt_pct(result.get('label_accuracy_on_test'))}**",
        f"- Horizon: {result['costs']['horizon']} bars | threshold: {result['costs']['label_threshold']} | spread: {result['costs']['spread_pips']} pips",
        "",
        "## Model success metrics",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| # trades | {m['n_trades']} |",
        f"| Win rate (overall) | {fmt_pct(m['win_rate'])} |",
        f"| Win rate BUY | {fmt_pct(m.get('win_rate_buy'))} (n={m.get('n_buy', 0)}) |",
        f"| Win rate SELL | {fmt_pct(m.get('win_rate_sell'))} (n={m.get('n_sell', 0)}) |",
        f"| Avg return / trade | {fmt_num(m['avg_return_per_trade'])} |",
        f"| Total return (compound) | {fmt_pct(m['total_return']) if m['total_return'] is not None else 'n/a'} |",
        f"| Max drawdown | {fmt_pct(m['max_drawdown']) if m['max_drawdown'] is not None else 'n/a'} |",
        f"| Profit factor | {fmt_num(m['profit_factor'])} |",
        "",
        "## Baselines",
        "",
        f"| Strategy | Trades | Win rate | Total return | Max DD | Profit factor |",
        f"|---|---:|---:|---:|---:|---:|",
        f"| Model | {m['n_trades']} | {fmt_pct(m['win_rate'])} | {fmt_pct(m['total_return'])} | {fmt_pct(m['max_drawdown'])} | {fmt_num(m['profit_factor'])} |",
        f"| SMA crossover | {sma['n_trades']} | {fmt_pct(sma['win_rate'])} | {fmt_pct(sma['total_return'])} | {fmt_pct(sma['max_drawdown'])} | {fmt_num(sma['profit_factor'])} |",
        f"| Always long | {lng['n_trades']} | {fmt_pct(lng['win_rate'])} | {fmt_pct(lng['total_return'])} | {fmt_pct(lng['max_drawdown'])} | {fmt_num(lng['profit_factor'])} |",
        "",
        "## How to read success rate",
        "",
        "- **Win rate** = fraction of closed trades with net_return > 0 after spread cost.",
        "- Compare model win rate / total return / profit factor against SMA and always-long baselines.",
        "- A higher win rate alone is not enough if avg return or profit factor is worse than baseline.",
        "",
        "## Disclaimer",
        "",
        "This lab is for research education only. It does **not** place broker orders.",
        "Data from yfinance is not identical to broker executable quotes. Past backtest results do not predict future performance.",
        "",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if trades is not None and not trades.empty:
        trades.to_csv(reports_dir / "latest_trades.csv", index=False)
    print(f"[backtest] wrote {md_path}")
    return str(md_path)
