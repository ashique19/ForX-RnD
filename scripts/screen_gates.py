"""EURUSD walk-forward: baseline vs selective gates vs label barrier variants.

Not a CLI command. Run: python3 scripts/screen_gates.py

Gates are applied to the *same* fold predictions as baseline (no retrain) so the
comparison isolates the filter. Cost-aware / asymmetric ATR change labels, so
those variants retrain. Event-window gate is a no-op in WF (no historical
Forex Factory dump) — fail-soft pass, same as a missing calendar on the desk.
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forex_lab.backtest import _metrics_from_trades, _simulate_trades, walk_forward_backtest
from forex_lab.config_loader import load_config
from forex_lab.data import load_ohlcv
from forex_lab.features import true_range_atr
from forex_lab.gates import apply_frame_gates


def _base(cfg0: dict) -> dict:
    out = copy.deepcopy(cfg0)
    out.setdefault("model", {})["compare_logistic"] = False
    extra = dict(out.get("feature_extras") or {})
    pta = dict(extra.get("pandas_ta") or {})
    pta["enabled"] = False
    extra["pandas_ta"] = pta
    fr = dict(extra.get("fred") or {})
    fr["enabled"] = False
    extra["fred"] = fr
    out["feature_extras"] = extra
    return out


def _gates_on(cfg: dict, **edits) -> dict:
    out = copy.deepcopy(cfg)
    block = {
        "enabled": True,
        "apply_to_flash": True,
        "apply_to_paper": True,
        "require_mtf_agree": True,
        "min_confidence": None,
        "no_new_opens_in_event_window": True,
        "fail_soft": True,
    }
    block.update(edits)
    out["gates"] = block
    return out


def _metrics_row(name: str, m: dict, elapsed: float, notes: str = "") -> dict:
    return {
        "variant": name,
        "n_trades": m.get("n_trades"),
        "win_rate": m.get("win_rate"),
        "total_return": m.get("total_return"),
        "max_dd": m.get("max_drawdown"),
        "profit_factor": m.get("profit_factor"),
        "sec": round(elapsed, 1),
        "notes": notes,
    }


def _verdict(row: dict, base: dict) -> str:
    if row.get("variant") == "baseline":
        return "—"
    if row.get("error"):
        return str(row["error"])
    d_pf = (row.get("profit_factor") or 0) - (base.get("profit_factor") or 0)
    d_ret = (row.get("total_return") or 0) - (base.get("total_return") or 0)
    d_dd = (row.get("max_dd") or 0) - (base.get("max_dd") or 0)
    # More negative DD is worse. Fold PF std is ~0.5; 0.01 PF is noise.
    if d_pf > 0 and d_ret > 0 and d_dd >= -1e-4:
        return "better on this sample — still not an edge"
    if abs(d_pf) < 0.05 and abs(d_ret) < 0.03 and abs(d_dd) < 0.01:
        return "null / fold noise — keep off"
    if d_pf < 0 or d_ret < 0 or d_dd < -0.002:
        return "worse — keep off"
    return "mixed — keep off"


def _md_table(rows: list[dict]) -> str:
    lines = [
        "| Variant | Trades | Win rate | Total return | Max DD | Profit factor | vs baseline |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    base = next((r for r in rows if r.get("variant") == "baseline"), rows[0] if rows else {})

    def pct(x):
        if x is None:
            return "n/a"
        return f"{100 * x:.2f}%"

    def pf(x):
        if x is None:
            return "n/a"
        return f"{x:.4f}"

    for row in rows:
        if row.get("error"):
            lines.append(f"| {row['variant']} | n/a | n/a | n/a | n/a | n/a | {row['error']} |")
            continue
        lines.append(
            f"| {row['variant']} | {row.get('n_trades', 0)} | {pct(row.get('win_rate'))} | "
            f"{pct(row.get('total_return'))} | {pct(row.get('max_dd'))} | "
            f"{pf(row.get('profit_factor'))} | {_verdict(row, base)} |"
        )
    return "\n".join(lines)


def _resimulate(df, preds, cfg, pair: str, gated_cfg: dict):
    filtered = apply_frame_gates(preds["pred"], preds, gated_cfg, force=True)
    atr = true_range_atr(df, int(cfg.get("atr_period", 14)))
    trades = _simulate_trades(df, filtered, cfg, pair, atr=atr)
    return _metrics_from_trades(trades)


def main() -> None:
    cfg0 = _base(load_config())
    df = load_ohlcv("EURUSD", cfg0)
    pair = "EURUSD"

    t0 = time.time()
    result, _trades, preds = walk_forward_backtest(df, cfg0, pair)
    elapsed = time.time() - t0
    rows = [
        _metrics_row(
            "baseline",
            result["model"],
            elapsed,
            notes="gates.enabled=false, cost_aware=false, tp/sl=2/2",
        )
    ]
    print(
        f"baseline        n={result['model']['n_trades']:5d} "
        f"wr={result['model']['win_rate'] or 0:.3f} "
        f"ret={result['model']['total_return']:.4f} "
        f"dd={result['model']['max_drawdown']:.4f} "
        f"pf={result['model']['profit_factor'] or 0:.4f} ({elapsed:.0f}s)",
        flush=True,
    )

    t1 = time.time()
    gated = _gates_on(cfg0)
    gm = _resimulate(df, preds, cfg0, pair, gated)
    rows.append(
        _metrics_row(
            "gated_mtf_conf",
            gm,
            time.time() - t1,
            notes="same fold preds; require_mtf_agree + min_confidence; event gate n/a in WF",
        )
    )
    print(
        f"gated_mtf_conf  n={gm['n_trades']:5d} wr={gm['win_rate'] or 0:.3f} "
        f"ret={gm['total_return']:.4f} dd={gm['max_drawdown']:.4f} "
        f"pf={gm['profit_factor'] or 0:.4f}",
        flush=True,
    )

    t2 = time.time()
    gated50 = _gates_on(cfg0, min_confidence=0.50)
    g50 = _resimulate(df, preds, cfg0, pair, gated50)
    rows.append(
        _metrics_row(
            "gated_mtf_conf50",
            g50,
            time.time() - t2,
            notes="same preds; MTF agree + min_confidence=0.50",
        )
    )
    print(
        f"gated_mtf_conf50 n={g50['n_trades']:5d} wr={g50['win_rate'] or 0:.3f} "
        f"ret={g50['total_return']:.4f} dd={g50['max_drawdown']:.4f} "
        f"pf={g50['profit_factor'] or 0:.4f}",
        flush=True,
    )

    label_cfgs = [
        ("cost_aware", {**copy.deepcopy(cfg0), "barrier": {**(cfg0.get("barrier") or {}), "cost_aware": True}}),
        (
            "asymmetric_1p5_1",
            {**copy.deepcopy(cfg0), "barrier": {**(cfg0.get("barrier") or {}), "tp_atr": 1.5, "sl_atr": 1.0}},
        ),
    ]
    for name, cfg in label_cfgs:
        t = time.time()
        res, _tr, _p = walk_forward_backtest(df, cfg, pair)
        elapsed = time.time() - t
        rows.append(
            _metrics_row(
                name,
                res["model"],
                elapsed,
                notes="retrained labels+exits matched; gates off",
            )
        )
        m = res["model"]
        print(
            f"{name:16s} n={m['n_trades']:5d} wr={m['win_rate'] or 0:.3f} "
            f"ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} "
            f"pf={m['profit_factor'] or 0:.4f} ({elapsed:.0f}s)",
            flush=True,
        )

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    payload = {
        "pair": pair,
        "protocol": "train 2000 / test 250 / step 250, next-open, 1 pip, one-position, XGBoost",
        "event_gate": "not applied in WF (no historical calendar; fail-soft pass)",
        "claim": "not a live edge",
        "rows": rows,
    }
    (out_dir / "gate_screen.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    table = _md_table(rows)
    md = (
        "# EURUSD walk-forward — selective gates vs label barriers\n\n"
        "Research only. Same protocol as `reports/latest_report.md` unless noted. "
        "**Not a trading system.** Event-window gate is UI-only here "
        "(walk-forward has no historical Forex Factory dump; missing calendar fail-softs).\n\n"
        f"{table}\n\n"
        "Default: `gates.enabled: false`, `barrier.cost_aware: false`, `tp_atr=sl_atr=2.0`. "
        "Turn a variant on only if PF, total return, **and** max DD improve (or a clear non-regression). "
        "A 0.01 PF tick is fold noise (fold PF std ~0.5).\n"
    )
    (out_dir / "gate_screen.md").write_text(md, encoding="utf-8")
    print(table)
    print(f"wrote {out_dir / 'gate_screen.md'}")


if __name__ == "__main__":
    main()
