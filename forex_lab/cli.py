"""CLI: python -m forex_lab <fetch|train|backtest|signals|digest|retrain|history|replay>."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from forex_lab.config_loader import load_config
from forex_lab.console import configure_stdio, safe_print
from forex_lab.data import data_path, fetch_ohlcv, load_ohlcv
from forex_lab.model import train_models
from forex_lab.backtest import walk_forward_backtest, write_report
from forex_lab.signals import generate_signals, write_signals


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pair", required=True, help="e.g. EURUSD")
    p.add_argument("--config", default=None, help="Path to YAML config")


def cmd_fetch(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Download OHLCV. Exit 0 if a CSV was saved, even if a later print fails."""
    pair = str(args.pair).upper()
    interval = getattr(args, "interval", None) or cfg.get("interval", "1h")
    out_path = data_path(pair, cfg, interval)
    df = None
    source = None
    try:
        df, source = fetch_ohlcv(
            pair,
            cfg,
            period=args.period,
            interval=args.interval,
            force_synthetic=args.synthetic,
        )
    except Exception as exc:  # noqa: BLE001 — keep CSV if the write already happened
        if out_path.exists() and out_path.stat().st_size > 0:
            safe_print(f"[fetch] {pair}: error after save ({exc}); keeping {out_path}")
            return 0
        safe_print(f"[fetch] {pair}: failed ({exc})")
        return 1

    saved = out_path.exists() and out_path.stat().st_size > 0
    try:
        n = 0 if df is None else len(df)
        safe_print(f"[fetch] {pair}: {n} bars from {source}")
        if df is not None and len(df):
            safe_print(f"[fetch] range {df.index.min()} -> {df.index.max()}")
        safe_print(f"[fetch] saved {out_path}")
    except Exception:
        pass
    return 0 if saved else 1


def cmd_train(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df = load_ohlcv(args.pair, cfg)
    results = train_models(df, cfg, args.pair)
    payload = {
        k: (v if not isinstance(v, dict) else {kk: vv for kk, vv in v.items() if kk != "report"})
        for k, v in results.items()
    }
    safe_print(json.dumps(payload, indent=2, default=str))
    return 0


def cmd_backtest(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df = load_ohlcv(args.pair, cfg)
    result, trades, _preds = walk_forward_backtest(df, cfg, args.pair, model_type=args.model)
    path = write_report(result, cfg, trades)
    m = result["model"]
    safe_print(
        f"[backtest] win_rate={m['win_rate']} n_trades={m['n_trades']} "
        f"avg_ret={m['avg_return_per_trade']} total_ret={m['total_return']} "
        f"max_dd={m['max_drawdown']} pf={m['profit_factor']}"
    )
    safe_print(f"[backtest] report: {path}")
    return 0


def cmd_signals(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    df = load_ohlcv(args.pair, cfg)
    try:
        sigs = generate_signals(df, cfg, args.pair)
    except FileNotFoundError:
        safe_print("[signals] model missing - training first")
        train_models(df, cfg, args.pair)
        sigs = generate_signals(df, cfg, args.pair)
    path = write_signals(sigs, cfg)
    safe_print(f"[signals] csv: {path}")
    return 0


def cmd_digest(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Yesterday/today snapshot. Fail-soft. No live broker. Asia/Dhaka clocks."""
    from forex_lab.digest import collect_digest, format_digest_text

    which = getattr(args, "when", None) or "both"
    persist = not bool(getattr(args, "no_save", False))
    payload = collect_digest(cfg, which=which, persist=persist)
    if bool(getattr(args, "json", False)):
        safe_print(json.dumps(payload, indent=2, default=str))
    else:
        safe_print(format_digest_text(payload))
    errs = list(payload.get("errors") or [])
    # Fail-soft: missing caches still exit 0. A hard assembler crash would raise.
    if errs and not payload.get("freshness") and not payload.get("paper"):
        return 0
    return 0


def cmd_retrain(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Champion/challenger walk-forward gate. Promote or report null. Not a live edge."""
    from forex_lab.retrain import format_retrain_text, run_retrain_gate

    pair = str(getattr(args, "pair", None) or (cfg.get("retrain") or {}).get("pair") or "EURUSD")
    dry = bool(getattr(args, "dry_run", False))
    result = run_retrain_gate(pair, cfg, dry_run=dry)
    if bool(getattr(args, "json", False)):
        safe_print(json.dumps(result, indent=2, default=str))
    else:
        safe_print(format_retrain_text(result))
    if result.get("ok") is False and not bool((cfg.get("retrain") or {}).get("fail_soft", True)):
        return 1
    return 0


def cmd_history(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Pull Dukascopy (or HistData) OHLC into data/history. Does not write synthetic prices."""
    from forex_lab.history import HistoryError, pull_history

    def _progress(payload: dict[str, Any]) -> None:
        msg = payload.get("message")
        if msg:
            safe_print(f"[history] {msg}")

    try:
        result = pull_history(
            str(args.pair).upper(),
            cfg,
            interval=getattr(args, "interval", None) or "1h",
            start=getattr(args, "start", None),
            end=getattr(args, "end", None),
            source=getattr(args, "source", None),
            progress=_progress,
        )
    except HistoryError as exc:
        safe_print(f"[history] failed: {exc}")
        return 1
    safe_print(
        f"[history] {result['pair']} {result['interval']}: {result['rows']} bars "
        f"from {result['source']} -> {result['path']}"
    )
    return 0


def cmd_replay(args: argparse.Namespace, cfg: dict[str, Any]) -> int:
    """Walk-forward replay train + scoreboard. Paper books only."""
    from forex_lab.history import HistoryError, history_status, load_history, load_meta, pull_history
    from forex_lab.replay import ReplayError, run_replay

    pair = str(args.pair).upper()
    interval = getattr(args, "interval", None) or "1h"
    if getattr(args, "model", None):
        model = dict(cfg.get("model") or {})
        model["type"] = str(args.model)
        cfg = {**cfg, "model": model}
    if getattr(args, "slippage_pips", None) is not None:
        replay = dict(cfg.get("replay") or {})
        replay["slippage_pips"] = float(args.slippage_pips)
        cfg = {**cfg, "replay": replay}

    def _progress(payload: dict[str, Any]) -> None:
        msg = payload.get("message")
        if msg:
            safe_print(f"[replay] {msg}")

    try:
        if not bool(getattr(args, "no_pull", False)):
            info = history_status(pair, interval, cfg, getattr(args, "start", None), getattr(args, "end", None))
            if info.get("stale"):
                pull_history(
                    pair,
                    cfg,
                    interval=interval,
                    start=getattr(args, "start", None),
                    end=getattr(args, "end", None),
                    source=getattr(args, "source", None),
                    progress=_progress,
                )
        frame = load_history(pair, cfg, interval, start=getattr(args, "start", None), end=getattr(args, "end", None))
        meta = load_meta(pair, interval, cfg)
        result = run_replay(
            frame,
            cfg,
            pair,
            interval=interval,
            source=str(meta.get("source") or "cache"),
            progress=_progress,
        )
    except (HistoryError, ReplayError) as exc:
        safe_print(f"[replay] failed: {exc}")
        return 1
    safe_print(f"[replay] {result.get('promotion_line')}")
    files = result.get("files") or {}
    safe_print(f"[replay] scoreboard {files.get('csv')}")
    safe_print(f"[replay] equity {files.get('equity_png')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m forex_lab",
        description="Forex Research Lab - research-only BUY/SELL signals (no live trading)",
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

    d = sub.add_parser(
        "digest",
        help="Daily digest (yesterday/today, Asia/Dhaka) - freshness, flips, paper, calendar, awareness",
    )
    d.add_argument("--config", default=None, help="Path to YAML config")
    d.add_argument(
        "--when",
        default="both",
        choices=["today", "yesterday", "both"],
        help="Calendar day in ui.timezone (default both = yesterday + today)",
    )
    d.add_argument("--json", action="store_true", help="Print JSON instead of text")
    d.add_argument("--no-save", action="store_true", help="Do not write data/digest_latest.json")

    r = sub.add_parser(
        "retrain",
        help="Weekly champion/challenger walk-forward gate (promote or null; not a live edge)",
    )
    r.add_argument("--pair", default=None, help="e.g. EURUSD (default retrain.pair or EURUSD)")
    r.add_argument("--config", default=None, help="Path to YAML config")
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="Compare latest_metrics.json vs champion without walk-forward or train",
    )
    r.add_argument("--json", action="store_true", help="Print JSON instead of text")

    h = sub.add_parser(
        "history",
        help="Pull Dukascopy tick->OHLC (HistData fallback) into data/history (gitignored)",
    )
    _add_common(h)
    h.add_argument("--interval", default="1h", help="15m | 1h | 4h | 1d")
    h.add_argument("--start", default="2015-01-01", help="UTC start (default 2015-01-01)")
    h.add_argument("--end", default=None, help="UTC end (default last closed hour)")
    h.add_argument("--source", default="auto", choices=["auto", "dukascopy", "histdata"])

    rp = sub.add_parser(
        "replay",
        help="Walk-forward replay train + scoreboard (paper only, does not touch the live journal)",
    )
    _add_common(rp)
    rp.add_argument("--interval", default="1h", help="15m | 1h | 4h | 1d")
    rp.add_argument("--start", default="2015-01-01", help="UTC start (default 2015-01-01)")
    rp.add_argument("--end", default=None, help="UTC end (default last closed hour)")
    rp.add_argument("--source", default="auto", choices=["auto", "dukascopy", "histdata"])
    rp.add_argument("--no-pull", action="store_true", help="Use the cache only; do not download")
    rp.add_argument("--model", default=None, help="Champion model: xgboost | logistic")
    rp.add_argument("--slippage-pips", dest="slippage_pips", type=float, default=None)

    return p


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if getattr(args, "interval", None):
        cfg = {**cfg, "interval": args.interval}
    if getattr(args, "period", None):
        cfg = {**cfg, "period": args.period}

    cmds = {
        "fetch": cmd_fetch,
        "train": cmd_train,
        "backtest": cmd_backtest,
        "signals": cmd_signals,
        "digest": cmd_digest,
        "retrain": cmd_retrain,
        "history": cmd_history,
        "replay": cmd_replay,
    }
    try:
        return int(cmds[args.command](args, cfg))
    except BrokenPipeError:
        return 0
    except UnicodeEncodeError:
        # Last-resort: data commands already saved; do not look like a hard failure.
        if getattr(args, "command", None) == "fetch":
            path = data_path(str(args.pair).upper(), cfg, getattr(args, "interval", None))
            if path.exists() and path.stat().st_size > 0:
                return 0
        safe_print("[cli] console encoding error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
