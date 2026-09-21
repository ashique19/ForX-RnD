"""Call existing forex_lab CLI commands and load on-disk research artifacts.

The Streamlit dashboard imports these helpers instead of shelling out.
No broker APIs. No live orders.
"""
from __future__ import annotations

import io
import traceback
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pandas as pd

from forex_lab.cli import cmd_backtest, cmd_fetch, cmd_signals, cmd_train
from forex_lab.config_loader import load_config
from forex_lab.data import data_path
from forex_lab.model import model_paths
from forex_lab.paths import project_root, resolve_under_root

PREFERRED_PAIRS = ("EURUSD", "GBPUSD", "USDJPY")


def ui_pairs(cfg: dict[str, Any] | None = None) -> list[str]:
    """Pairs from config, EURUSD / GBPUSD / USDJPY first when present."""
    cfg = cfg if cfg is not None else load_config()
    keys = [str(k).upper() for k in (cfg.get("pairs") or {})]
    ordered = [p for p in PREFERRED_PAIRS if p in keys]
    rest = [p for p in keys if p not in ordered]
    return ordered + rest or ["EURUSD"]


def reports_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg if cfg is not None else load_config()
    return resolve_under_root(cfg.get("paths", {}).get("reports_dir", "reports"))


def signals_path(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg if cfg is not None else load_config()
    d = resolve_under_root(cfg.get("paths", {}).get("signals_dir", "signals"))
    return d / "latest_signals.csv"


def metrics_path(cfg: dict[str, Any] | None = None) -> Path:
    return reports_dir(cfg) / "latest_metrics.json"


def report_path(cfg: dict[str, Any] | None = None) -> Path:
    return reports_dir(cfg) / "latest_report.md"


def trades_path(cfg: dict[str, Any] | None = None) -> Path:
    return reports_dir(cfg) / "latest_trades.csv"


def artifact_status(
    pair: str,
    cfg: dict[str, Any] | None = None,
    interval: str | None = None,
) -> dict[str, Any]:
    """Existence checks for the selected pair's cache / model plus shared reports."""
    cfg = cfg if cfg is not None else load_config()
    pair = pair.upper()
    interval = str(interval or cfg.get("interval", "1h"))
    csv = data_path(pair, cfg, interval)
    n_bars = None
    if csv.exists() and csv.stat().st_size > 0:
        try:
            n_bars = max(sum(1 for _ in csv.open(encoding="utf-8")) - 1, 0)
        except OSError:
            n_bars = None
    mtype = str((cfg.get("model") or {}).get("type") or "xgboost")
    model_file = model_paths(pair, cfg, mtype)["model"]
    return {
        "pair": pair,
        "interval": interval,
        "data_csv": csv,
        "data_exists": csv.exists() and csv.stat().st_size > 0,
        "n_bars": n_bars,
        "model_file": model_file,
        "model_exists": model_file.exists(),
        "signals_csv": signals_path(cfg),
        "signals_exist": signals_path(cfg).exists(),
        "metrics_json": metrics_path(cfg),
        "metrics_exist": metrics_path(cfg).exists(),
        "report_md": report_path(cfg),
        "report_exists": report_path(cfg).exists(),
        "trades_csv": trades_path(cfg),
        "trades_exist": trades_path(cfg).exists(),
        "root": project_root(),
    }


def load_signals(cfg: dict[str, Any] | None = None) -> pd.DataFrame | None:
    path = signals_path(cfg)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    if df.empty:
        return df
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.sort_values("datetime", kind="mergesort").reset_index(drop=True)
    return df


def load_metrics(cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    path = metrics_path(cfg)
    if not path.exists():
        return None
    import json

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else None


def load_report(cfg: dict[str, Any] | None = None) -> str | None:
    path = report_path(cfg)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_trades(cfg: dict[str, Any] | None = None) -> pd.DataFrame | None:
    path = trades_path(cfg)
    if not path.exists():
        return None
    df = pd.read_csv(path)
    return None if df.empty else df


def equity_from_trades(trades: pd.DataFrame) -> pd.DataFrame:
    """Compounded equity and drawdown from sequential closed-trade net returns."""
    if trades is None or trades.empty or "net_return" not in trades.columns:
        return pd.DataFrame(columns=["equity", "drawdown"])
    rets = pd.to_numeric(trades["net_return"], errors="coerce").fillna(0.0)
    equity = (1.0 + rets).cumprod()
    peak = equity.cummax().replace(0, 1.0)
    dd = equity / peak - 1.0
    if "entry_time" in trades.columns:
        idx = pd.to_datetime(trades["entry_time"], errors="coerce")
    else:
        idx = pd.RangeIndex(len(trades), name="trade")
    out = pd.DataFrame({"equity": equity.to_numpy(), "drawdown": dd.to_numpy()}, index=idx)
    out.index.name = "time"
    return out


def signals_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Newest bar first, with a `note` marker on that row."""
    display = df.iloc[::-1].reset_index(drop=True)
    notes = ["newest"] + [""] * (len(display) - 1) if len(display) else []
    display.insert(0, "note", notes)
    return display


def style_signals(df: pd.DataFrame):
    """Newest row first; highlight it and color the signal column when Styler works.

    Falls back to a plain DataFrame if pandas Styler/jinja2 is unavailable.
    """
    if df is None or df.empty:
        return df
    display = signals_display_frame(df)

    from forex_lab.ui.theme import newest_row_style, signal_cell_style

    def _row(row: pd.Series) -> list[str]:
        if str(row.get("note", "")) == "newest":
            return newest_row_style(len(row))
        return [""] * len(row)

    def _sig(val: object) -> str:
        return signal_cell_style(val)

    try:
        styler = display.style.apply(_row, axis=1)
        if "signal" in display.columns:
            mapper = getattr(styler, "map", None) or getattr(styler, "applymap", None)
            if mapper is not None:
                styler = mapper(_sig, subset=["signal"])
        fmt: dict[str, str] = {}
        for col in ("close", "confidence", "dir_edge", "p_buy", "p_sell", "p_hold"):
            if col in display.columns:
                fmt[col] = "{:.4f}"
        if fmt:
            styler = styler.format(fmt)
        return styler
    except Exception:
        return display


def _prepared_cfg(
    cfg: dict[str, Any] | None,
    *,
    period: str | None = None,
    interval: str | None = None,
) -> dict[str, Any]:
    out = dict(cfg if cfg is not None else load_config())
    if interval:
        out["interval"] = interval
    if period:
        out["period"] = period
    return out


def _run_cmd(fn, args: Namespace, cfg: dict[str, Any]) -> tuple[int, str]:
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = int(fn(args, cfg))
        return rc, buf.getvalue()
    except Exception:
        return 1, buf.getvalue() + traceback.format_exc()


def run_fetch(
    pair: str,
    *,
    period: str | None = None,
    interval: str | None = None,
    synthetic: bool = False,
    cfg: dict[str, Any] | None = None,
) -> tuple[int, str]:
    cfg = _prepared_cfg(cfg, period=period, interval=interval)
    args = Namespace(
        pair=str(pair).upper(),
        period=period,
        interval=interval,
        synthetic=bool(synthetic),
        config=None,
    )
    return _run_cmd(cmd_fetch, args, cfg)


def run_train(pair: str, *, cfg: dict[str, Any] | None = None) -> tuple[int, str]:
    cfg = _prepared_cfg(cfg)
    args = Namespace(pair=str(pair).upper(), config=None)
    return _run_cmd(cmd_train, args, cfg)


def run_backtest(
    pair: str,
    *,
    model: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> tuple[int, str]:
    cfg = _prepared_cfg(cfg)
    args = Namespace(pair=str(pair).upper(), model=model, config=None)
    return _run_cmd(cmd_backtest, args, cfg)


def run_signals(pair: str, *, cfg: dict[str, Any] | None = None) -> tuple[int, str]:
    cfg = _prepared_cfg(cfg)
    args = Namespace(pair=str(pair).upper(), config=None)
    return _run_cmd(cmd_signals, args, cfg)


def metrics_table(metrics: dict[str, Any] | None) -> pd.DataFrame:
    """One row per strategy for the dashboard comparison table."""
    if not metrics:
        return pd.DataFrame()
    rows = []
    mapping = [
        ("model", f"Model ({metrics.get('model_type') or 'primary'})"),
        ("compare_logistic", "Logistic (same walk-forward)"),
        ("baseline_sma_crossover", "SMA crossover"),
        ("baseline_always_long", "Always long"),
    ]
    for key, label in mapping:
        block = metrics.get(key)
        if not isinstance(block, dict):
            continue
        rows.append(
            {
                "strategy": label,
                "trades": block.get("n_trades"),
                "win_rate": block.get("win_rate"),
                "total_return": block.get("total_return"),
                "max_drawdown": block.get("max_drawdown"),
                "profit_factor": block.get("profit_factor"),
                "avg_return_per_trade": block.get("avg_return_per_trade"),
            }
        )
    return pd.DataFrame(rows)
