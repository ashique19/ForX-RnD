"""Markdown and JSON writers for the Phase 0 results. No price series are stored."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.phase0.common import fmt_num, jsonable


def _ci(block: dict[str, Any] | None) -> str:
    if not block:
        return "n/a"
    return (
        f"{fmt_num(block.get('mean'))} "
        f"[{fmt_num(block.get('ci_lo'))}, {fmt_num(block.get('ci_hi'))}] "
        f"({block.get('interval') or 'n/a'}, n={block.get('n')})"
    )


def _seed(block: dict[str, Any] | None) -> str:
    if not block:
        return "n/a"
    return (
        f"mean {fmt_num(block.get('mean'))}, "
        f"p05 {fmt_num(block.get('p05'))}, p50 {fmt_num(block.get('p50'))}, p95 {fmt_num(block.get('p95'))}, "
        f"fraction of seeds >= model {fmt_num(block.get('frac_ge_model'))} "
        f"(seeds={block.get('n_seeds')})"
    )


def _metric_row(name: str, block: dict[str, Any] | None) -> str:
    block = block or {}
    return (
        f"| {name} | {block.get('n_trades', 0)} | {fmt_num(block.get('profit_factor'))} | "
        f"{fmt_num(block.get('expectancy'), 6)} |"
    )


def _pair_section(pair: str, body: dict[str, Any]) -> list[str]:
    lines = [f"## {pair}", ""]
    if body.get("error"):
        lines.append(f"Failed: {body['error']}")
        lines.append("")
        return lines
    excl = body.get("fresh_holdout_exclusion") or {}
    lines.append(
        f"Bars kept: {excl.get('n_kept')}. Bars at or after the fresh holdout excluded: "
        f"{excl.get('n_excluded')} "
        f"({excl.get('excluded_first') or 'none'} → {excl.get('excluded_last') or 'none'}). "
        "The excluded timestamps are an audit of the cut. No price from that window is in the tables below."
    )
    lines.append("")
    leak = body.get("leakage") or {}
    lines.extend(["### (a) Leakage audit", "", leak.get("conclusion") or "n/a", ""])
    src = leak.get("production_source") or {}
    lines.append(
        "Production source check: "
        f"train slice ends at the test start = {src.get('slices_train_up_to_test_start')}, "
        f"uses holdout.train_mask = {src.get('uses_holdout_train_mask')}, "
        f"mentions purge_bars = {src.get('mentions_purge_bars')}, "
        f"mentions embargo = {src.get('mentions_embargo')}."
    )
    lines.append("")
    params = leak.get("walk_forward_params") or {}
    lines.append(
        f"Feature rows {leak.get('n_feature_rows')}. "
        f"Walk-forward train/test/step/min = "
        f"{params.get('train_bars')}/{params.get('test_bars')}/{params.get('step_bars')}/{params.get('min_train_bars')}. "
        f"Folds {leak.get('n_folds')}. Folds with a trained overlapping label "
        f"{leak.get('folds_with_trained_overlap')}. Trained overlapping rows "
        f"{leak.get('trained_overlap_rows')}. Overlapping rows removed by the holdout mask "
        f"{leak.get('overlap_rows_removed_by_holdout_mask')}. "
        f"Holdout cutoff {leak.get('holdout_cutoff') or 'disabled'}."
    )
    lines.append("")
    example = leak.get("example_fold") or {}
    rows = example.get("rows") or []
    if rows:
        lines.append(f"First overlapping fold ({example.get('fold')}), trained rows whose labels read the test window:")
        lines.append("")
        lines.append("| x iloc | bar loc | decision | label last bar | label last time |")
        lines.append("|---:|---:|---|---:|---|")
        for r in rows:
            lines.append(
                f"| {r.get('x_iloc')} | {r.get('bar_loc')} | {r.get('decision_ts')} | "
                f"{r.get('label_last_bar')} | {r.get('label_last_ts')} |"
            )
        lines.append("")
    comp = body.get("purge_comparison")
    lines.extend(["### (a) Purged plus embargoed versus unpurged", ""])
    if not comp:
        lines.append("Not run.")
        lines.append("")
    else:
        lines.append(comp.get("note") or "")
        lines.append("")
        lines.append("| Arm | Trades | PF (net return) | Expectancy (net return) | PF (R net) | Expectancy (R net) | PF (R raw) | Expectancy (R raw) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for arm in ("unpurged", "purged_embargoed"):
            b = comp.get(arm) or {}
            lines.append(
                f"| {arm} | {b.get('n_trades', 0)} | "
                f"{fmt_num((b.get('net_return') or {}).get('profit_factor'))} | "
                f"{fmt_num((b.get('net_return') or {}).get('expectancy'), 6)} | "
                f"{fmt_num((b.get('r_net') or {}).get('profit_factor'))} | "
                f"{fmt_num((b.get('r_net') or {}).get('expectancy'), 6)} | "
                f"{fmt_num((b.get('r_raw') or {}).get('profit_factor'))} | "
                f"{fmt_num((b.get('r_raw') or {}).get('expectancy'), 6)} |"
            )
        lines.append("")
        skipped = comp.get("folds_skipped") or []
        skip_txt = "none" if not skipped else "; ".join(
            f"fold {s.get('fold')} {s.get('reason')} (n_train={s.get('n_train')})" for s in skipped
        )
        lines.append(
            f"Purged folds fit {comp.get('folds_fit')} of {comp.get('folds_attempted')}. "
            f"Train rows purged {comp.get('train_rows_purged')}, embargoed {comp.get('train_rows_embargoed')}. "
            f"Overlapping rows remaining after the mask: {comp.get('overlap_rows_after_purge')}. "
            f"Embargo bars: {comp.get('embargo_bars')}. Skipped folds: {skip_txt}."
        )
        if comp.get("folds_fit") != comp.get("folds_attempted"):
            lines.append(
                "A skipped fold is not filled with the unpurged predictions. "
                "Profit factor on that arm is then not over the same test bars."
            )
        lines.append("")
    rnd = body.get("model_vs_random") or {}
    lines.extend(["### (b) Model versus random and flipped sides", ""])
    lines.append(f"Prediction source: {body.get('predictions_source')}.")
    lines.append("")
    if rnd.get("note") == "no trades":
        lines.append("No trades after the signal filters, so there is no expectancy to compare.")
        lines.append("")
    else:
        lines.append(f"Trades: {rnd.get('n_trades')}. Seeds: {rnd.get('n_seeds')}. Bootstrap draws: {rnd.get('n_boot')}. Block length: {rnd.get('block')} trades.")
        lines.append("")
        lines.append("| Series | Mean R [95% block CI] |")
        lines.append("|---|---|")
        lines.append(f"| Model after cost | {_ci(rnd.get('model_r_net'))} |")
        lines.append(f"| Model zero cost | {_ci(rnd.get('model_r_raw'))} |")
        lines.append(f"| Flipped side, resimulated, after cost | {_ci(rnd.get('flipped_resimulated_r_net'))} |")
        lines.append(f"| Flipped side, resimulated, zero cost | {_ci(rnd.get('flipped_resimulated_r_raw'))} |")
        lines.append(f"| Flipped sign, same path, after cost | {_ci(rnd.get('flipped_sign_r_net'))} |")
        lines.append(f"| Flipped sign, same path, zero cost | {_ci(rnd.get('flipped_sign_r_raw'))} |")
        lines.append("")
        lines.append("Random sides, cross-seed distribution of mean R (not a bootstrap CI):")
        lines.append("")
        lines.append(f"- Resimulated, after cost: {_seed(rnd.get('random_resimulated_r_net'))}")
        lines.append(f"- Resimulated, zero cost: {_seed(rnd.get('random_resimulated_r_raw'))}")
        lines.append(f"- Sign matched, after cost: {_seed(rnd.get('random_sign_r_net'))}")
        lines.append(f"- Sign matched, zero cost: {_seed(rnd.get('random_sign_r_raw'))}")
        lines.append("")
        lines.append("Return-space profit factor uses the production metric on the same trades.")
        lines.append("")
        lines.append("| | Trades | PF | Expectancy |")
        lines.append("|---|---:|---:|---:|")
        lines.append(_metric_row("After cost (net return)", rnd.get("model_net_return")))
        lines.append(_metric_row("Zero cost (raw return)", rnd.get("model_raw_return")))
        lines.append("")
        lines.append("Entry-time splits. The fresh holdout is already removed. The published holdout was scored by earlier studies; the tail after 2026-10-12 18:00 UTC was not, and it is not the frozen window.")
        lines.append("")
        lines.append("| Window | Trades | PF net R | Expectancy net R |")
        lines.append("|---|---:|---:|---:|")
        labels = {
            "before_prior_holdout": "entries before 2025-10-01",
            "published_holdout": "entries 2025-10-01 through 2026-10-12 18:00",
            "unscored_tail_before_fresh_holdout": "entries after 2026-10-12 18:00 and before 2026-11-01",
        }
        for key, label in labels.items():
            w = (rnd.get("windows_by_entry") or {}).get(key) or {}
            r = w.get("r_net") or {}
            lines.append(f"| {label} | {w.get('n_trades', 0)} | {fmt_num(r.get('profit_factor'))} | {fmt_num(r.get('expectancy'), 6)} |")
        lines.append("")
    cal = body.get("calibration") or {}
    lines.extend(["### (c) Calibration and exit reason", ""])
    if cal.get("note"):
        lines.append(str(cal["note"]))
        lines.append("")
    lines.append("| Decile | n | conf min | conf max | mean net R | mean raw R | win rate |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|")
    for d in cal.get("deciles") or []:
        lines.append(
            f"| {d.get('decile')} | {d.get('n')} | {fmt_num(d.get('conf_min'))} | {fmt_num(d.get('conf_max'))} | "
            f"{fmt_num(d.get('mean_r_net'), 6)} | {fmt_num(d.get('mean_r_raw'), 6)} | {fmt_num(d.get('win_rate'))} |"
        )
    if not cal.get("deciles"):
        lines.append("| n/a | 0 | n/a | n/a | n/a | n/a | n/a |")
    lines.append("")
    lines.append("| Exit | n | sum net R | mean net R | sum net return |")
    lines.append("|---|---:|---:|---:|---:|")
    for e in cal.get("by_exit") or []:
        lines.append(
            f"| {e.get('exit')} | {e.get('n')} | {fmt_num(e.get('sum_r_net'), 4)} | "
            f"{fmt_num(e.get('mean_r_net'), 6)} | {fmt_num(e.get('sum_net_return'), 6)} |"
        )
    if not cal.get("by_exit"):
        lines.append("| n/a | 0 | n/a | n/a | n/a |")
    lines.append("")
    amb = body.get("same_bar_sl_tp") or {}
    lines.extend(
        [
            "### (d) Same-bar stop and target",
            "",
            f"Trades {amb.get('n_trades', 0)}. Trades whose exit bar touches both barriers: "
            f"{amb.get('trades_affected', 0)} ({fmt_num(amb.get('share_of_trades'))} of trades). "
            f"Ambiguous bars: {amb.get('ambiguous_bars', 0)}. "
            f"Simulator `sl_conflict` count: {amb.get('simulator_sl_conflict', 0)} "
            f"(matches the scan: {amb.get('sl_conflict_matches_scan')}).",
            "",
            amb.get("note") or "",
            "",
        ]
    )
    cost = body.get("cost_vs_move") or {}
    lines.extend(["### (e) Round-trip cost versus the absolute 8-bar move", "", cost.get("note") or "", ""])
    lines.append(
        f"Cost {fmt_num(cost.get('cost_pips'), 2)} pips. Horizon {cost.get('horizon')} bars. "
        f"Entry {cost.get('entry_timing')}. Bars {cost.get('n_bars')}."
    )
    lines.append("")
    lines.append("| Session | n | median abs pips | mean abs pips | cost / median | fraction of moves larger than the cost |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in cost.get("by_session") or []:
        lines.append(
            f"| {row.get('session')} | {row.get('n')} | {fmt_num(row.get('median_abs_pips'), 2)} | "
            f"{fmt_num(row.get('mean_abs_pips'), 2)} | {fmt_num(row.get('cost_over_median'), 3)} | "
            f"{fmt_num(row.get('frac_move_gt_cost'), 3)} |"
        )
    lines.append("")
    return lines


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Phase 0 diagnostics — {result.get('date')}",
        "",
        "Research only. No live order, no yaml write, no model write, no change to min_confidence, costs, sessions, or risk.",
        "",
        f"Generated {result.get('generated_at_utc')}. Pairs: {', '.join(result.get('pairs_requested') or [])}.",
        "",
        "## Fresh holdout",
        "",
        "The window in `research/phase0/PREREG.md` is excluded before features, labels, fits, and summaries. "
        f"Cutoff {((result.get('fresh_holdout') or {}).get('start_utc'))}.",
        "",
        "## Method",
        "",
        "Leakage indices follow `forex_lab.backtest.walk_forward_backtest`: train iloc `[start - train_bars, start)`, test iloc `[start, start + test_bars)`, holdout mask only. "
        "A label at bar t reads bars t+1 through t+horizon. "
        "The purged arm calls `fit_predict_bundle` on the same test windows after dropping overlapping labels and an embargo of one horizon (default) before the test. "
        "Round-trip cost is the research figure (EURUSD 1.4 pips, USDJPY 2.4 pips) applied in memory as `spread_pips`. "
        "R = return × entry / (sl_atr × ATR14 at the decision bar). "
        "Random resimulation keeps the model's decision timestamps and unit size. "
        "The sign-matched null keeps the model's raw R and cost drag. "
        "Block bootstrap resamples contiguous trades in entry-time order.",
        "",
    ]
    for pair in result.get("pairs_requested") or []:
        lines.extend(_pair_section(pair, (result.get("pairs") or {}).get(pair) or {"error": "missing"}))
    lines.extend(
        [
            "## What this run does not decide",
            "",
            "A profit factor above 1 on this sample is not a promotion. The fresh holdout was not scored. "
            "Config, gates, min_confidence, session filters, and position sizing are unchanged.",
            "",
        ]
    )
    return "\n".join(lines)


def write_results(result: dict[str, Any], out_dir: Path, stamp: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"RESULTS_{stamp}.md"
    js_path = out_dir / f"RESULTS_{stamp}.json"
    md_path.write_text(render_markdown(result), encoding="utf-8")
    js_path.write_text(json.dumps(jsonable(result), indent=2), encoding="utf-8")
    return md_path, js_path
