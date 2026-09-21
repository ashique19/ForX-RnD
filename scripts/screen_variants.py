"""Screen research variants on cached EURUSD under the same walk-forward protocol.

Not a CLI command. Run: python3 scripts/screen_variants.py
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

from forex_lab.config_loader import load_config
from forex_lab.data import load_ohlcv
from forex_lab.backtest import walk_forward_backtest


def _apply(cfg: dict, **edits) -> dict:
    out = copy.deepcopy(cfg)
    for key, val in edits.items():
        if key == "barrier":
            out["barrier"] = {**(out.get("barrier") or {}), **val}
        elif key == "signals":
            out["signals"] = {**(out.get("signals") or {}), **val}
        elif key == "model":
            out["model"] = {**(out.get("model") or {}), **val}
        else:
            out[key] = val
    out.setdefault("model", {})["compare_logistic"] = False
    return out


def main() -> None:
    cfg0 = load_config()
    df = load_ohlcv("EURUSD", cfg0)
    variants = [
        ("baseline_2_2", _apply(cfg0)),
        ("rr_1p5_1", _apply(cfg0, barrier={"tp_atr": 1.5, "sl_atr": 1.0})),
        ("rr_2_1", _apply(cfg0, barrier={"tp_atr": 2.0, "sl_atr": 1.0})),
        ("cost_aware", _apply(cfg0, barrier={"cost_aware": True})),
        ("sess_ldn_ny", _apply(cfg0, signals={"sessions": ["london", "ny"]})),
        ("high_vol", _apply(cfg0, signals={"min_vol_regime": 1.0})),
        ("min_tp_8pips", _apply(cfg0, signals={"min_tp_pips": 8.0})),
        ("cal_isotonic", _apply(cfg0, model={"calibrate": "isotonic"})),
        ("cal_sigmoid", _apply(cfg0, model={"calibrate": "sigmoid"})),
        ("prune_25", _apply(cfg0, model={"prune_bottom_frac": 0.25})),
        ("conf_off", _apply(cfg0, signals={"min_confidence": 0.0})),
    ]

    rows = []
    for name, cfg in variants:
        t0 = time.time()
        result, trades, _ = walk_forward_backtest(df, cfg, "EURUSD")
        m = result["model"]
        sma = result["baseline_sma_crossover"]
        lng = result["baseline_always_long"]
        elapsed = time.time() - t0
        row = {
            "variant": name,
            "n_trades": m["n_trades"],
            "win_rate": m["win_rate"],
            "total_return": m["total_return"],
            "max_dd": m["max_drawdown"],
            "profit_factor": m["profit_factor"],
            "sma_pf": sma["profit_factor"],
            "sma_ret": sma["total_return"],
            "long_pf": lng["profit_factor"],
            "long_ret": lng["total_return"],
            "folds": result["folds"],
            "sec": round(elapsed, 1),
        }
        rows.append(row)
        print(
            f"{name:16s} n={m['n_trades']:5d} wr={m['win_rate'] or 0:.3f} "
            f"ret={m['total_return']:.4f} pf={m['profit_factor'] or 0:.4f} "
            f"sma_pf={sma['profit_factor'] or 0:.4f} long_pf={lng['profit_factor'] or 0:.4f} "
            f"({elapsed:.0f}s)",
            flush=True,
        )

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    (out_dir / "variant_screen.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out_dir / 'variant_screen.json'}")


if __name__ == "__main__":
    main()
