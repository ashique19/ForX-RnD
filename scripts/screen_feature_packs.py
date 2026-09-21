"""Screen pandas-ta / FRED feature packs on cached EURUSD (same walk-forward protocol).

Not a CLI command. Run: python3 scripts/screen_feature_packs.py
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

from forex_lab.backtest import walk_forward_backtest
from forex_lab.config_loader import load_config
from forex_lab.data import load_ohlcv
from forex_lab.fred import load_fred_frame


def _with_packs(cfg: dict, *, pandas_ta: bool, fred: bool) -> dict:
    out = copy.deepcopy(cfg)
    extra = dict(out.get("feature_extras") or {})
    pta = dict(extra.get("pandas_ta") or {})
    pta["enabled"] = bool(pandas_ta)
    extra["pandas_ta"] = pta
    fr = dict(extra.get("fred") or {})
    fr["enabled"] = bool(fred)
    extra["fred"] = fr
    out["feature_extras"] = extra
    out.setdefault("model", {})["compare_logistic"] = False
    return out


def _md_table(rows: list[dict]) -> str:
    lines = [
        "| Variant | Trades | Win rate | Total return | Max DD | Profit factor | vs baseline |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    base = rows[0] if rows else {}

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
            lines.append(
                f"| {row['variant']} | n/a | n/a | n/a | n/a | n/a | {row['error']} |"
            )
            continue
        verdict = "—"
        if row["variant"] != "baseline":
            d_pf = (row.get("profit_factor") or 0) - (base.get("profit_factor") or 0)
            d_ret = (row.get("total_return") or 0) - (base.get("total_return") or 0)
            d_dd = (row.get("max_dd") or 0) - (base.get("max_dd") or 0)
            # Fold PF std is ~0.5–0.6; a 0.01 PF tick is not an upgrade.
            if d_dd < -0.005 and d_pf < 0.05:
                verdict = "worse DD / null — keep off"
            elif abs(d_pf) < 0.05 and abs(d_ret) < 0.03:
                verdict = "null / fold noise — keep off"
            elif d_pf > 0 and d_ret > 0 and d_dd >= -0.005:
                verdict = "better on this sample — still not an edge; keep off unless large"
            else:
                verdict = "mixed — keep off"
        lines.append(
            f"| {row['variant']} | {row.get('n_trades', 0)} | {pct(row.get('win_rate'))} | "
            f"{pct(row.get('total_return'))} | {pct(row.get('max_dd'))} | "
            f"{pf(row.get('profit_factor'))} | {verdict} |"
        )
    return "\n".join(lines)


def main() -> None:
    cfg0 = load_config()
    df = load_ohlcv("EURUSD", cfg0)
    fred_on = _with_packs(cfg0, pandas_ta=False, fred=True)
    fred_cfg = dict((fred_on.get("feature_extras") or {}).get("fred") or {})
    frame, status = load_fred_frame(fred_cfg, fred_on, pair="EURUSD", allow_network=True)
    print(
        f"[fred] source={status.source} series={status.series} "
        f"error={status.error} rows={0 if frame is None else len(frame)}",
        flush=True,
    )
    fred_ok = frame is not None and not frame.empty

    variants = [
        ("baseline", _with_packs(cfg0, pandas_ta=False, fred=False)),
        ("pandas_ta", _with_packs(cfg0, pandas_ta=True, fred=False)),
        ("fred", _with_packs(cfg0, pandas_ta=False, fred=True)),
        ("both", _with_packs(cfg0, pandas_ta=True, fred=True)),
    ]

    rows = []
    for name, cfg in variants:
        if name in {"fred", "both"} and not fred_ok:
            rows.append(
                {
                    "variant": name,
                    "error": "FRED unavailable (no cache / download failed)",
                }
            )
            print(f"{name:12s} skipped — no FRED data", flush=True)
            continue
        t0 = time.time()
        result, _trades, _ = walk_forward_backtest(df, cfg, "EURUSD")
        m = result["model"]
        elapsed = time.time() - t0
        row = {
            "variant": name,
            "n_trades": m["n_trades"],
            "win_rate": m["win_rate"],
            "total_return": m["total_return"],
            "max_dd": m["max_drawdown"],
            "profit_factor": m["profit_factor"],
            "folds": result["folds"],
            "sec": round(elapsed, 1),
        }
        rows.append(row)
        print(
            f"{name:12s} n={m['n_trades']:5d} wr={m['win_rate'] or 0:.3f} "
            f"ret={m['total_return']:.4f} dd={m['max_drawdown']:.4f} "
            f"pf={m['profit_factor'] or 0:.4f} ({elapsed:.0f}s)",
            flush=True,
        )

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    payload = {"fred_status": status.__dict__, "rows": rows}
    (out_dir / "feature_pack_screen.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    table = _md_table(rows)
    (out_dir / "feature_pack_screen.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(f"wrote {out_dir / 'feature_pack_screen.json'}")


if __name__ == "__main__":
    main()
