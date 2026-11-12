"""Phase 0 diagnostic entry point.

    python -m research.phase0.run --pairs EURUSD USDJPY

Reads history and, when present, saved walk-forward predictions. Writes
``research/phase0/RESULTS_<date>.md`` and ``.json``. Does not write yaml,
models, journals, or anything under ``data/``, ``models/``, or ``signals/``.
Bars at or after the fresh holdout in ``PREREG.md`` are dropped before any
feature, label, fit, or summary.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.console import safe_print
from forex_lab.paths import project_root

from research.phase0.common import (
    DEFAULT_BLOCK,
    DEFAULT_BOOT,
    DEFAULT_PAIRS,
    DEFAULT_SEED,
    DEFAULT_SEEDS,
    FRESH_HOLDOUT_START,
    PRIOR_HOLDOUT_START,
    PUBLISHED_SCORE_LAST_BAR,
    assert_output_dir,
    history_path,
    jsonable,
    load_history_csv,
    research_cfg,
    results_stamp,
    split_fresh_holdout,
)
from research.phase0.diagnostics import calibration, cost_vs_move, model_vs_random, same_bar_sl_tp
from research.phase0.leakage import audit_leakage, purged_walk_forward, unpurged_walk_forward
from research.phase0.report import write_results


def _public_fit(fit: dict[str, Any] | None) -> dict[str, Any] | None:
    if not fit:
        return None
    skip = {"trades", "preds"}
    return {k: v for k, v in fit.items() if k not in skip}


def load_saved_predictions(pair: str, preds_dir: Path | None) -> tuple[pd.DataFrame | None, str]:
    """Read-only. Returns ``(frame, source)``. ``frame`` is None when nothing usable is on disk."""
    candidates: list[Path] = []
    if preds_dir is not None:
        candidates.append(Path(preds_dir) / f"{pair.upper()}_preds.csv")
    candidates.append(project_root() / "data" / "edge_check" / "htf_20261020" / f"{pair.upper()}_preds.csv")
    for path in candidates:
        if not path.exists():
            continue
        frame = pd.read_csv(path, index_col=0, parse_dates=True)
        if frame.empty or "pred" not in frame.columns:
            continue
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index))
        if frame.index.tz is not None:
            frame.index = frame.index.tz_convert("UTC").tz_localize(None)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        return frame, f"saved:{path}"
    return None, "walk_forward_backtest"


def _filter_preds(preds: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    idx = preds.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
        preds = preds.copy()
        preds.index = idx
    return preds.loc[preds.index < cutoff]


def analyze_pair(
    df_full: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    *,
    preds: pd.DataFrame | None,
    preds_source: str,
    model_type: str | None,
    embargo_bars: int | None,
    n_seeds: int,
    n_boot: int,
    block: int,
    seed: int,
) -> dict[str, Any]:
    df, exclusion = split_fresh_holdout(df_full, FRESH_HOLDOUT_START)
    cfg_pair, cost_info = research_cfg(cfg, pair, model_type=model_type)
    leakage = audit_leakage(df, cfg_pair, pair)
    overlap = leakage.get("training_labels_overlap_test") is True
    unpurged = unpurged_walk_forward(df, cfg_pair, pair, model_type=model_type)
    purged = None
    if overlap:
        purged = purged_walk_forward(df, cfg_pair, pair, model_type=model_type, embargo_bars=embargo_bars)
    comparison = None
    if purged is not None:
        comparison = {
            "note": (
                "Same test windows and the same in-memory research cost. "
                "Unpurged is forex_lab.backtest.walk_forward_backtest. "
                "Purged calls fit_predict_bundle after the purge and embargo. "
                + str(purged.get("purge_rule") or "")
            ),
            "embargo_bars": purged.get("embargo_bars"),
            "folds_attempted": purged.get("folds_attempted"),
            "folds_fit": purged.get("folds_fit"),
            "folds_skipped": purged.get("folds_skipped"),
            "train_rows_purged": purged.get("train_rows_purged"),
            "train_rows_embargoed": purged.get("train_rows_embargoed"),
            "overlap_rows_after_purge": purged.get("overlap_rows_after_purge"),
            "unpurged": _public_fit(unpurged),
            "purged_embargoed": _public_fit(purged),
        }
    if preds is not None:
        use_preds = _filter_preds(preds, FRESH_HOLDOUT_START)
        source = preds_source
        from forex_lab.backtest import _simulate_trades

        signal = use_preds["pred"].copy()
        signal.index = pd.DatetimeIndex(pd.to_datetime(signal.index))
        if signal.index.tz is not None:
            signal.index = signal.index.tz_convert("UTC").tz_localize(None)
        signal = pd.to_numeric(signal, errors="coerce").reindex(df.index).dropna().astype(int)
        trades = _simulate_trades(df, signal, cfg_pair, pair)
        from research.phase0.common import attach_r

        trades = attach_r(trades, df, cfg_pair)
        pred_frame = use_preds
    else:
        source = "walk_forward_backtest"
        trades = unpurged["trades"]
        pred_frame = unpurged["preds"]
    rng = np.random.default_rng(int(seed))
    versus = model_vs_random(
        df, trades, cfg_pair, pair, n_seeds=n_seeds, n_boot=n_boot, block=block, rng=rng
    )
    return {
        "error": None,
        "cost": jsonable(cost_info),
        "fresh_holdout_exclusion": exclusion,
        "leakage": leakage,
        "purge_comparison": comparison,
        "predictions_source": source,
        "model_vs_random": versus,
        "calibration": calibration(trades, pred_frame),
        "same_bar_sl_tp": same_bar_sl_tp(df, trades, cfg_pair),
        "cost_vs_move": cost_vs_move(df, cfg_pair, pair, float(cost_info["rt_pips"])),
    }


def run_diagnostics(
    pairs: list[str],
    *,
    history_dir: Path,
    preds_dir: Path | None = None,
    cfg: dict[str, Any] | None = None,
    model_type: str | None = None,
    embargo_bars: int | None = None,
    n_seeds: int = DEFAULT_SEEDS,
    n_boot: int = DEFAULT_BOOT,
    block: int = DEFAULT_BLOCK,
    seed: int = DEFAULT_SEED,
    when: datetime | None = None,
) -> dict[str, Any]:
    base = copy.deepcopy(cfg) if cfg is not None else load_config()
    stamp_dt = when or datetime.now(timezone.utc)
    out: dict[str, Any] = {
        "date": results_stamp(stamp_dt),
        "generated_at_utc": stamp_dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "pairs_requested": [p.upper() for p in pairs],
        "fresh_holdout": {
            "start_utc": str(FRESH_HOLDOUT_START),
            "prior_holdout_start_utc": str(PRIOR_HOLDOUT_START),
            "published_score_last_bar_utc": str(PUBLISHED_SCORE_LAST_BAR),
            "rule": "No Phase 0 feature, label, fit, or summary uses bars at or after start_utc.",
        },
        "pairs": {},
    }
    for pair in out["pairs_requested"]:
        path = history_path(pair, history_dir)
        safe_print(f"[phase0] {pair} history {path}")
        if not path.exists():
            out["pairs"][pair] = {
                "error": f"missing history {path}. Phase 0 does not synthesize prices.",
            }
            continue
        try:
            df = load_history_csv(path)
            preds, source = load_saved_predictions(pair, preds_dir)
            out["pairs"][pair] = analyze_pair(
                df,
                base,
                pair,
                preds=preds,
                preds_source=source,
                model_type=model_type,
                embargo_bars=embargo_bars,
                n_seeds=n_seeds,
                n_boot=n_boot,
                block=block,
                seed=seed,
            )
        except Exception as exc:
            out["pairs"][pair] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 0 FX diagnostics (research only).")
    p.add_argument("--pairs", nargs="+", default=list(DEFAULT_PAIRS), help="default: EURUSD USDJPY")
    p.add_argument("--history-dir", default="data/history", help="directory of {PAIR}_1h.csv (read only)")
    p.add_argument(
        "--preds-dir",
        default=None,
        help="optional directory of {PAIR}_preds.csv. Also checks data/edge_check/htf_20261020.",
    )
    p.add_argument("--out-dir", default=None, help="default: research/phase0 next to this file")
    p.add_argument("--date", default=None, help="YYYYMMDD stamp for the results filename. Default: today UTC.")
    p.add_argument(
        "--model-type",
        default=None,
        help="walk-forward model. Default: config model.type (xgboost). Does not edit yaml.",
    )
    p.add_argument("--seeds", type=int, default=DEFAULT_SEEDS)
    p.add_argument("--n-boot", type=int, default=DEFAULT_BOOT)
    p.add_argument("--block", type=int, default=DEFAULT_BLOCK)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument(
        "--embargo-bars",
        type=int,
        default=None,
        help="extra gap after the purge, in bars. Default: the label horizon.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent
    assert_output_dir(out_dir)
    history = Path(args.history_dir)
    if not history.is_absolute():
        history = project_root() / history
    preds_dir = Path(args.preds_dir) if args.preds_dir else None
    when = None
    if args.date:
        when = datetime.strptime(args.date, "%Y%m%d").replace(tzinfo=timezone.utc)
    result = run_diagnostics(
        list(args.pairs),
        history_dir=history,
        preds_dir=preds_dir,
        model_type=args.model_type,
        embargo_bars=args.embargo_bars,
        n_seeds=args.seeds,
        n_boot=args.n_boot,
        block=args.block,
        seed=args.seed,
        when=when,
    )
    stamp = args.date or result["date"]
    md_path, js_path = write_results(result, out_dir, stamp)
    failed = [p for p, body in result["pairs"].items() if body.get("error")]
    safe_print(f"[phase0] wrote {md_path}")
    safe_print(f"[phase0] wrote {js_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
