"""Walk-forward label-overlap audit and a purged, embargoed refit.

Production ``walk_forward_backtest`` (the slice at ``tr_end = start``) does not
read ``walk_forward.purge_bars`` or ``embargo_bars``. ``holdout.train_mask``
drops a row only when its own bar, or the bar ``horizon`` steps later, is at
or after the single cutoff in ``research/holdout.json``.

A triple-barrier label at decision bar ``t`` is a function of bars
``t+1 .. t+horizon`` (inclusive) on the OHLCV mesh. The training slice ends
on the bar before the test slice, so the last ``horizon`` training labels
read prices inside the test window. This module traces those indices and,
when the overlap is there, refits with the same ``fit_predict_bundle`` on a
mask that purges the overlap and then embargoes ``embargo_bars`` more bars
(default: the horizon).
"""
from __future__ import annotations

import inspect
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.backtest import (
    _attach_policy_columns,
    _metrics_from_trades,
    _simulate_trades,
    walk_forward_backtest,
)
from forex_lab.features import make_dataset, true_range_atr
from forex_lab.holdout import train_mask
from forex_lab.model import apply_signal_filters, fit_predict_bundle

from research.phase0.common import attach_r, metrics_on, naive_index

PURGE_RULE = (
    "A training row at OHLCV location t is purged when its label scan "
    "[t+1, t+horizon] intersects the test bars [test_first, test_last]. "
    "It is embargoed when that scan does not intersect the test but its last "
    "bar still falls in [test_first - embargo_bars, test_first). "
    "embargo_bars defaults to the label horizon. The holdout cutoff mask still applies."
)


def label_scan(bar_loc: int, horizon: int, n_bars: int) -> tuple[int, int] | None:
    """Inclusive ``[first, last]`` OHLCV locations a label at ``bar_loc`` reads.

    Matches ``triple_barrier_labels`` for both ``next_open`` and ``same_close``:
    the scan is bars ``t+1 .. t+horizon``. ``label_ready_at`` uses the same last bar.
    """
    h = int(horizon)
    loc = int(bar_loc)
    if loc < 0 or h <= 0:
        return None
    first = loc + 1
    last = loc + h
    if first >= n_bars or last >= n_bars or last < first:
        return None
    return first, last


def scan_intersects(span: tuple[int, int] | None, first: int, last: int) -> bool:
    if span is None:
        return False
    return span[0] <= last and span[1] >= first


def resolve_wf(n: int, cfg: dict[str, Any]) -> tuple[int, int, int, int]:
    """Same short-sample adjustment as ``walk_forward_backtest``."""
    wf = cfg.get("walk_forward") or {}
    train_bars = int(wf.get("train_bars", 2000))
    test_bars = int(wf.get("test_bars", 250))
    step_bars = int(wf.get("step_bars", 250))
    min_train = int(wf.get("min_train_bars", 500))
    if n < min_train + 50:
        train_bars = max(min_train, n // 2)
        test_bars = max(50, n // 10)
        step_bars = test_bars
    return train_bars, test_bars, step_bars, min_train


def fold_bounds(n: int, train_bars: int, test_bars: int, step_bars: int, min_train: int) -> list[tuple[int, int, int, int]]:
    """``(tr_start, tr_end, te_start, te_end)`` in feature-row iloc, test end exclusive."""
    start = train_bars
    if start >= n:
        start = max(min_train, int(n * 0.6))
    folds: list[tuple[int, int, int, int]] = []
    while start < n:
        tr_start = max(0, start - train_bars)
        tr_end = start
        te_end = min(n, start + test_bars)
        if tr_end - tr_start < min_train or te_end - start < 10:
            break
        folds.append((tr_start, tr_end, start, te_end))
        start += step_bars
    return folds


def production_source_flags() -> dict[str, bool]:
    src = inspect.getsource(walk_forward_backtest)
    return {
        "mentions_purge_bars": "purge_bars" in src,
        "mentions_embargo": "embargo" in src,
        "slices_train_up_to_test_start": "tr_end = start" in src and "X.iloc[tr_start:tr_end]" in src,
        "uses_holdout_train_mask": "train_mask" in src,
    }


def production_train_positions(
    trainable: np.ndarray,
    tr_start: int,
    tr_end: int,
    train_bars: int,
    min_train: int,
) -> tuple[np.ndarray, str]:
    """Feature-row positions ``walk_forward_backtest`` would fit for one fold.

    Holdout mask on ``[tr_start, tr_end)``. If that leaves fewer than
    ``min_train`` rows, the last ``train_bars`` holdout-legal rows in the sample
    (the frozen pre-cutoff window).
    """
    window = np.arange(tr_start, tr_end)
    kept = window[np.asarray(trainable[tr_start:tr_end], dtype=bool)]
    if len(kept) >= min_train:
        return kept.astype(int), "fold_window"
    frozen = np.flatnonzero(np.asarray(trainable, dtype=bool))
    frozen = frozen[-int(train_bars) :]
    return frozen.astype(int), "frozen_precutoff"


def _stamp(index: pd.DatetimeIndex, loc: int) -> str | None:
    if loc < 0 or loc >= len(index):
        return None
    ts = pd.Timestamp(index[loc])
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def trace_folds(
    x_index: pd.DatetimeIndex,
    bar_index: pd.DatetimeIndex,
    trainable: np.ndarray,
    *,
    horizon: int,
    cfg: dict[str, Any],
    cutoff: pd.Timestamp | None,
) -> dict[str, Any]:
    """Trace training-label overlap with each test window. No model is fit."""
    x_index = naive_index(x_index)
    bar_index = naive_index(bar_index)
    n = len(x_index)
    n_bars = len(bar_index)
    locs = bar_index.get_indexer(x_index)
    trainable = np.asarray(trainable, dtype=bool)
    if len(trainable) != n:
        raise ValueError("trainable mask must align with the feature index")
    train_bars, test_bars, step_bars, min_train = resolve_wf(n, cfg)
    bounds = fold_bounds(n, train_bars, test_bars, step_bars, min_train)
    folds: list[dict[str, Any]] = []
    example: dict[str, Any] | None = None
    overlap_trained = 0
    overlap_removed = 0
    folds_with_overlap = 0

    for fold_i, (tr_start, tr_end, te_start, te_end) in enumerate(bounds):
        test_locs = locs[te_start:te_end]
        test_locs = test_locs[test_locs >= 0]
        if len(test_locs) == 0:
            continue
        test_first = int(test_locs.min())
        test_last = int(test_locs.max())
        raw = np.arange(tr_start, tr_end)
        overlap_raw: list[int] = []
        for i in raw:
            span = label_scan(int(locs[i]), horizon, n_bars) if locs[i] >= 0 else None
            if scan_intersects(span, test_first, test_last):
                overlap_raw.append(int(i))
        removed = [i for i in overlap_raw if not bool(trainable[i])]
        trained, branch = production_train_positions(trainable, tr_start, tr_end, train_bars, min_train)
        trained_overlap: list[int] = []
        if len(trained) >= min_train:
            for i in trained:
                span = label_scan(int(locs[i]), horizon, n_bars) if locs[i] >= 0 else None
                if scan_intersects(span, test_first, test_last):
                    trained_overlap.append(int(i))
        else:
            branch = "skipped_short_train"
        overlap_trained += len(trained_overlap)
        overlap_removed += len(removed)
        if trained_overlap:
            folds_with_overlap += 1
        row = {
            "fold": fold_i,
            "branch": branch,
            "train_iloc": [int(tr_start), int(tr_end)],
            "test_iloc": [int(te_start), int(te_end)],
            "test_first_bar": test_first,
            "test_last_bar": test_last,
            "test_first_ts": _stamp(bar_index, test_first),
            "test_last_ts": _stamp(bar_index, test_last),
            "n_overlap_in_train_window": len(overlap_raw),
            "n_overlap_removed_by_holdout_mask": len(removed),
            "n_overlap_trained": len(trained_overlap),
            "n_trained": int(len(trained)) if branch != "skipped_short_train" else 0,
        }
        folds.append(row)
        if example is None and trained_overlap:
            shown = []
            for i in trained_overlap[:12]:
                loc = int(locs[i])
                span = label_scan(loc, horizon, n_bars)
                shown.append(
                    {
                        "x_iloc": int(i),
                        "bar_loc": loc,
                        "decision_ts": _stamp(bar_index, loc),
                        "label_first_bar": None if span is None else span[0],
                        "label_last_bar": None if span is None else span[1],
                        "label_last_ts": None if span is None else _stamp(bar_index, span[1]),
                    }
                )
            example = {"fold": fold_i, "rows": shown}

    source = production_source_flags()
    overlaps = folds_with_overlap > 0
    if source["mentions_purge_bars"] or source["mentions_embargo"]:
        source_sentence = (
            "walk_forward_backtest now mentions purge_bars or embargo, so this replica of the "
            "unpurged slice needs to be re-read before the overlap count is trusted."
        )
    else:
        source_sentence = "walk_forward_backtest does not mention purge_bars or embargo."
    if not bounds:
        conclusion = "No walk-forward fold was produced, so the boundary overlap is not observable on this sample."
    elif overlaps:
        conclusion = (
            f"Yes. On {folds_with_overlap} of {len(folds)} walk-forward folds, at least one training "
            f"label scans a bar inside that fold's test window "
            f"({overlap_trained} trained rows). The training slice is "
            f"X.iloc[tr_start:tr_end] with tr_end equal to the test start, and the label at t "
            f"reads bars t+1 through t+horizon. holdout.train_mask removed {overlap_removed} of the "
            f"overlapping rows in the raw train window, and only where the label settles at or after "
            f"the single cutoff ({cutoff}). It does not purge other fold boundaries. "
            f"{source_sentence}"
        )
    else:
        conclusion = (
            f"No trained row on {len(folds)} folds has a label scan that intersects its test bars. "
            f"The holdout mask removed {overlap_removed} overlapping rows in the raw train windows. "
            f"{source_sentence}"
        )
    return {
        "horizon": int(horizon),
        "label_scan": "[t+1, t+horizon] inclusive on the OHLCV mesh",
        "walk_forward_params": {
            "train_bars": train_bars,
            "test_bars": test_bars,
            "step_bars": step_bars,
            "min_train_bars": min_train,
        },
        "holdout_cutoff": None if cutoff is None else str(pd.Timestamp(cutoff)),
        "holdout_purges_only_final_cutoff": True,
        "production_source": source,
        "n_folds": len(folds),
        "folds_with_trained_overlap": folds_with_overlap,
        "trained_overlap_rows": overlap_trained,
        "overlap_rows_removed_by_holdout_mask": overlap_removed,
        "training_labels_overlap_test": overlaps if bounds else None,
        "example_fold": example,
        "folds": folds,
        "conclusion": conclusion,
        "purge_rule": PURGE_RULE,
    }


def audit_leakage(df: pd.DataFrame, cfg: dict[str, Any], pair: str, cutoff: pd.Timestamp | None = None) -> dict[str, Any]:
    """Build the real feature index and trace fold boundaries. No fit."""
    from forex_lab.data_integrity import assert_bar_integrity
    from forex_lab.holdout import load_cutoff

    assert_bar_integrity(df, pair, cfg, context="phase0 leakage audit")
    x, _y, _ohlcv = make_dataset(df, cfg, pair=pair)
    horizon = int(cfg.get("horizon") or 0)
    cut = load_cutoff() if cutoff is None else pd.Timestamp(cutoff)
    if cut is not None and pd.Timestamp(cut).tzinfo is not None:
        cut = pd.Timestamp(cut).tz_convert("UTC").tz_localize(None)
    trainable = train_mask(x.index, cutoff=cut, horizon=horizon, bar_index=df.index).to_numpy()
    traced = trace_folds(x.index, df.index, trainable, horizon=horizon, cfg=cfg, cutoff=cut)
    traced["pair"] = pair.upper()
    traced["n_feature_rows"] = int(len(x))
    traced["entry_timing"] = str(cfg.get("entry_timing", "next_open"))
    return traced


def purged_train_positions(
    candidates: np.ndarray,
    locs: np.ndarray,
    *,
    horizon: int,
    n_bars: int,
    test_locs: np.ndarray,
    embargo_bars: int,
) -> tuple[np.ndarray, int, int, int]:
    """Drop overlap, then embargo. Returns positions, n_purged, n_embargoed, n_still_overlapping."""
    if len(test_locs) == 0:
        return np.asarray(candidates, dtype=int), 0, 0, 0
    test_first = int(np.min(test_locs))
    test_last = int(np.max(test_locs))
    embargo = max(0, int(embargo_bars))
    floor = test_first - embargo
    keep: list[int] = []
    n_purge = 0
    n_embargo = 0
    for i in candidates:
        loc = int(locs[int(i)])
        span = label_scan(loc, horizon, n_bars) if loc >= 0 else None
        overlaps = scan_intersects(span, test_first, test_last) or loc >= test_first
        if span is None or overlaps:
            n_purge += 1
            continue
        if embargo > 0 and span[1] >= floor:
            n_embargo += 1
            continue
        keep.append(int(i))
    kept = np.asarray(keep, dtype=int)
    still = 0
    for i in kept:
        span = label_scan(int(locs[int(i)]), horizon, n_bars)
        if scan_intersects(span, test_first, test_last):
            still += 1
    return kept, n_purge, n_embargo, still


def _predict_test(cfg, x_tr, y_tr, x_te, ohlcv, pair, model_type):
    balanced = bool((cfg.get("model") or {}).get("class_weight_balanced", True))
    _model, frame, _cols = fit_predict_bundle(
        cfg, x_tr, y_tr, x_te, model_type=model_type, balanced=balanced
    )
    frame = _attach_policy_columns(frame, x_te, ohlcv, cfg, pair)
    frame["pred"] = apply_signal_filters(frame, cfg)
    return frame


def purged_walk_forward(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    *,
    model_type: str | None = None,
    embargo_bars: int | None = None,
    cutoff: pd.Timestamp | None = None,
) -> dict[str, Any]:
    """Same test windows as production, with purge plus embargo on the train rows.

    Reuses ``fit_predict_bundle``, ``apply_signal_filters``, and ``_simulate_trades``.
    Does not write a model.
    """
    from forex_lab.data_integrity import assert_bar_integrity
    from forex_lab.holdout import load_cutoff

    assert_bar_integrity(df, pair, cfg, context="phase0 purged walk-forward")
    x, y, ohlcv = make_dataset(df, cfg, pair=pair)
    horizon = int(cfg.get("horizon") or 0)
    embargo = horizon if embargo_bars is None else int(embargo_bars)
    cut = load_cutoff() if cutoff is None else pd.Timestamp(cutoff)
    if cut is not None and pd.Timestamp(cut).tzinfo is not None:
        cut = pd.Timestamp(cut).tz_convert("UTC").tz_localize(None)
    trainable = train_mask(x.index, cutoff=cut, horizon=horizon, bar_index=df.index).to_numpy()
    bar_index = naive_index(df.index)
    locs = bar_index.get_indexer(naive_index(x.index))
    n = len(x)
    n_bars = len(bar_index)
    train_bars, test_bars, step_bars, min_train = resolve_wf(n, cfg)
    mtype = (model_type or (cfg.get("model") or {}).get("type") or "xgboost").lower()
    frames: list[pd.DataFrame] = []
    skipped: list[dict[str, Any]] = []
    n_purge = 0
    n_embargo = 0
    still = 0
    attempted = 0
    for fold_i, (tr_start, tr_end, te_start, te_end) in enumerate(
        fold_bounds(n, train_bars, test_bars, step_bars, min_train)
    ):
        attempted += 1
        test_locs = locs[te_start:te_end]
        test_locs = test_locs[test_locs >= 0]
        candidates, branch = production_train_positions(trainable, tr_start, tr_end, train_bars, min_train)
        kept, p_n, e_n, still_n = purged_train_positions(
            candidates, locs, horizon=horizon, n_bars=n_bars, test_locs=test_locs, embargo_bars=embargo
        )
        n_purge += p_n
        n_embargo += e_n
        still += still_n
        if len(kept) < min_train:
            skipped.append({"fold": fold_i, "reason": "below_min_train_after_purge", "n_train": int(len(kept)), "branch": branch})
            continue
        y_tr = y.iloc[kept]
        if y_tr.nunique() < 2:
            skipped.append({"fold": fold_i, "reason": "single_class", "n_train": int(len(kept)), "branch": branch})
            continue
        x_te = x.iloc[te_start:te_end]
        if len(x_te) < 10:
            skipped.append({"fold": fold_i, "reason": "short_test", "n_train": int(len(kept)), "branch": branch})
            continue
        frame = _predict_test(cfg, x.iloc[kept], y_tr, x_te, ohlcv, pair, mtype)
        frames.append(frame)
    if not frames:
        trades = pd.DataFrame()
        preds = pd.DataFrame()
    else:
        preds = pd.concat(frames)
        preds = preds[~preds.index.duplicated(keep="last")]
        atr_full = true_range_atr(df, int(cfg.get("atr_period", 14)))
        trades = _simulate_trades(df, preds["pred"], cfg, pair, atr=atr_full)
    trades = attach_r(trades, df, cfg)
    net = metrics_on(trades, trades["net_return"]) if len(trades) else metrics_on(trades, [])
    raw = metrics_on(trades, trades["raw_return"]) if len(trades) else metrics_on(trades, [])
    r_net = metrics_on(trades, trades["r_net"]) if len(trades) else metrics_on(trades, [])
    r_raw = metrics_on(trades, trades["r_raw"]) if len(trades) else metrics_on(trades, [])
    # Production metric helper, so a reviewer can match profit_factor on net returns.
    prod_net = _metrics_from_trades(trades if len(trades) else pd.DataFrame())
    return {
        "pair": pair.upper(),
        "model_type": mtype,
        "embargo_bars": int(embargo),
        "horizon": int(horizon),
        "purge_rule": PURGE_RULE,
        "folds_attempted": attempted,
        "folds_fit": len(frames),
        "folds_skipped": skipped,
        "train_rows_purged": int(n_purge),
        "train_rows_embargoed": int(n_embargo),
        "overlap_rows_after_purge": int(still),
        "n_test_bars": int(len(preds)),
        "n_trades": int(len(trades)),
        "net_return": net,
        "raw_return": raw,
        "r_net": r_net,
        "r_raw": r_raw,
        "production_metric_profit_factor": prod_net.get("profit_factor"),
        "trades": trades,
        "preds": preds,
    }


def unpurged_walk_forward(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    *,
    model_type: str | None = None,
) -> dict[str, Any]:
    """Production walk-forward. The passed cfg is an in-memory copy."""
    mtype = (model_type or (cfg.get("model") or {}).get("type") or "xgboost").lower()
    result, trades, preds = walk_forward_backtest(df, cfg, pair, model_type=mtype)
    trades = attach_r(trades, df, cfg)
    net = metrics_on(trades, trades["net_return"]) if len(trades) else metrics_on(trades, [])
    raw = metrics_on(trades, trades["raw_return"]) if len(trades) else metrics_on(trades, [])
    r_net = metrics_on(trades, trades["r_net"]) if len(trades) else metrics_on(trades, [])
    r_raw = metrics_on(trades, trades["r_raw"]) if len(trades) else metrics_on(trades, [])
    prod_pf = (result.get("model") or {}).get("profit_factor")
    return {
        "pair": pair.upper(),
        "model_type": mtype,
        "folds": int(result.get("folds") or 0),
        "n_test_bars": int(result.get("n_test_bars") or 0),
        "n_trades": int(len(trades)),
        "net_return": net,
        "raw_return": raw,
        "r_net": r_net,
        "r_raw": r_raw,
        "production_metric_profit_factor": prod_pf,
        "production_pf_matches_rescore": _pf_match(prod_pf, net.get("profit_factor"), net.get("profit_factor_infinite")),
        "trades": trades,
        "preds": preds,
        "holdout_split": result.get("holdout"),
    }


def _pf_match(prod: Any, rescored: Any, infinite: bool) -> bool:
    if infinite:
        return prod == float("inf")
    if prod is None or rescored is None:
        return prod is None and rescored is None
    try:
        return abs(float(prod) - float(rescored)) < 1e-9
    except (TypeError, ValueError):
        return False
