"""CLI: python -m forex_lab <fetch|train|backtest|signals>."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from forex_lab.config_loader import load_config
from forex_lab.data import fetch_ohlcv, load_ohlcv
from forex_lab.model import train_models
from forex_lab.backtest import walk_forward_backtest, write_report
from forex_lab.signals import generate_signals, write_signals


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pair", required=True, help="e.g. EURUSD")
    p.add_argument("--config", default=None, help="Path to YAML config")


def cmd_fetch(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df, source = fetch_ohlcv(
        args.pair,
        cfg,
        period=args.period,
        interval=args.interval,
        force_synthetic=args.synthetic,
    )
    print(f"[fetch] {args.pair.upper()}: {len(df)} bars from {source}")
    print(f"[fetch] range {df.index.min()} → {df.index.max()}")
    return 0


def cmd_train(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df = load_ohlcv(args.pair, cfg)
    results = train_models(df, cfg, args.pair)
    print(json.dumps({k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != 'report'}) for k, v in results.items()}, indent=2))
    return 0


def cmd_backtest(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df = load_ohlcv(args.pair, cfg)
    result, trades, _preds = walk_forward_backtest(df, cfg, args.pair, model_type=args.model)
    path = write_report(result, cfg, trades)
    m = result["model"]
    print(
        f"[backtest] win_rate={m['win_rate']} n_trades={m['n_trades']} "
        f"avg_ret={m['avg_return_per_trade']} total_ret={m['total_return']} "
        f"max_dd={m['max_drawdown']} pf={m['profit_factor']}"
    )
    print(f"[backtest] report: {path}")
    return 0


def cmd_signals(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df = load_ohlcv(args.pair, cfg)
    # Ensure a model exists; train if missing
    try:
        sigs = generate_signals(df, cfg, args.pair)
    except FileNotFoundError:
        print("[signals] model missing — training first")
        train_models(df, cfg, args.pair)
        sigs = generate_signals(df, cfg, args.pair)
    path = write_signals(sigs, cfg)
    print(f"[signals] csv: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m forex_lab",
        description="Forex Research Lab — research-only BUY/SELL signals (no live trading)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fetch", help="Download OHLCV via yfinance (synthetic fallback)")
    _add_common(f)
    f.add_argument("--period", default=None, help="yfinance period, e.g. 2y")
    f.add_argument("--interval", default=None, help="e.g. 1h")
    f.add_argument("--synthetic", action="store_true", help="Force synthetic OHLCV")

    t = sub.add_parser("train", help="Train XGBoost + logistic models")
    _add_common(t)

    b = sub.add_parser("backtest", help="Walk-forward backtest + report")
    _add_common(b)
    b.add_argument("--model", default=None, help="xgboost|logistic (default from config)")

    s = sub.add_parser("signals", help="Write signals/latest_signals.csv")
    _add_common(s)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.interval if hasattr(args, "interval") else None:
        pass
    # Allow CLI interval/period to override config for fetch
    if getattr(args, "interval", None):
        cfg = {**cfg, "interval": args.interval}
    if getattr(args, "period", None):
        cfg = {**cfg, "period": args.period}

    cmds = {
        "fetch": cmd_fetch,
        "train": cmd_train,
        "backtest": cmd_backtest,
        "signals": cmd_signals,
    }
    return cmds[args.command](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
