"""Research-only measured round-trip cost model (MT5 demo spreads + explore-mirror slippage vs Dukascopy history).

Nothing here is read by the live desk, paper books, or any yaml. ``assumed_cost_pips`` on the paper books is untouched.
The versioned table lives in research/costs/ (built by scripts/build_mt5_cost_table.py from real samples only):
  * spread: data/costs/mt5_spreads.csv (read-only MT5 demo quotes, 5-minute samples, FX-open hours only)
  * slippage: explore_journal mt5_open / mt5_close rows with ok=true (fill vs requested price, + = adverse)
  * Duka: data/paper_shadow/measured_costs.csv (Dukascopy BidClose/AskClose history)
A pair with no data has no row and ``rt_cost_pips`` raises ``MissingCost``: it is reported, never filled.

Cost models (round-trip pips; a round trip pays one full spread plus a slippage on each of 2 fills):
  assumed       paper-book assumption (spread 1.0 + 2 x 0.2 slippage = 1.4), for comparison only
  duka_p50/p90  2 x Dukascopy one-way p50 / p90 (the convention of the 2026-10-14..22 research rounds)
  mt5_p50/p90   MT5 spread p50 / p90 + 2 x measured adverse slippage (mean / p90)
  mt5_measured  max(mt5_p90, duka_p90): the conservative default for research and backtests
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TABLE_PATH = ROOT / "research" / "costs" / "mt5_measured_costs_20261031.json"
SCHEMA = "mt5_measured_costs_v1"
COST_MODELS = ("assumed", "duka_p50", "duka_p90", "mt5_p50", "mt5_p90", "mt5_measured")
DEFAULT_MODEL = "mt5_measured"
ASSUMED_RT_PIPS = 1.4
MIN_SLIP_N = 20  # per-pair slippage needs this many fills, else the pooled slippage is used
HOUR_BUCKETS = {"asia_00_06": (0, 6), "london_07_11": (7, 11), "ldn_ny_12_15": (12, 15), "ny_16_20": (16, 20), "rollover_21_23": (21, 23)}


class MissingCost(KeyError):
    """No measured cost for this pair (reported, never filled)."""


def pip_size(pair: str) -> float:
    return 0.01 if pair.upper().endswith("JPY") else 0.0001


def _bucket(hour: int) -> str:
    for name, (a, b) in HOUR_BUCKETS.items():
        if a <= hour <= b:
            return name
    return "other"


def _q(x: pd.Series, q: float) -> float:
    return float(np.quantile(x.to_numpy(float), q))


def slippage_samples(journal_path: Path) -> pd.DataFrame:
    """Adverse slippage per fill (pips, + = worse than requested) from explore mirror rows with ok=true."""
    rows = []
    with Path(journal_path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("event") in ("mt5_open", "mt5_close") and r.get("ok") and r.get("slippage_pips") is not None:
                rows.append({"pair": r.get("pair"), "event": r["event"], "slip": float(r["slippage_pips"])})
    return pd.DataFrame(rows, columns=["pair", "event", "slip"])


def build_table(spreads_csv: Path, journal_path: Path, duka_csv: Path, *, asof: str) -> dict[str, Any]:
    sp = pd.read_csv(spreads_csv)
    sp = sp[np.isfinite(pd.to_numeric(sp["spread_pips"], errors="coerce")) & (sp["spread_pips"] > 0)].copy()
    sp["bucket"] = sp["hour_utc"].astype(int).map(_bucket)
    sl = slippage_samples(journal_path)
    duka = pd.read_csv(duka_csv).set_index("pair")
    pooled_adv = sl["slip"].clip(lower=0.0) if len(sl) else pd.Series(dtype=float)
    pooled = {"n": int(len(sl)), "adverse_mean": float(pooled_adv.mean()) if len(sl) else 0.0,
              "adverse_p90": _q(pooled_adv, 0.9) if len(sl) else 0.0,
              "signed_mean": float(sl["slip"].mean()) if len(sl) else 0.0,
              "signed_median": float(sl["slip"].median()) if len(sl) else 0.0}
    pairs: dict[str, Any] = {}
    for pair, g in sp.groupby("pair"):
        s = sl[sl["pair"] == pair]
        use_pair = len(s) >= MIN_SLIP_N
        adv = s["slip"].clip(lower=0.0) if use_pair else None
        slip_mean = float(adv.mean()) if use_pair else pooled["adverse_mean"]
        slip_p90 = _q(adv, 0.9) if use_pair else pooled["adverse_p90"]
        buckets = {b: {"n": int(len(x)), "p50": round(_q(x["spread_pips"], 0.5), 3), "p90": round(_q(x["spread_pips"], 0.9), 3)}
                   for b, x in g.groupby("bucket")}
        row: dict[str, Any] = {
            "spread_n": int(len(g)), "spread_p50": round(_q(g["spread_pips"], 0.5), 3), "spread_p90": round(_q(g["spread_pips"], 0.9), 3),
            "spread_first": str(g["ts_utc"].min()), "spread_last": str(g["ts_utc"].max()),
            "slip_n": int(len(s)), "slip_source": "pair" if use_pair else "pooled",
            "slip_adverse_mean": round(slip_mean, 4), "slip_adverse_p90": round(slip_p90, 4), "by_hour_bucket": buckets,
        }
        row["mt5_p50"] = round(row["spread_p50"] + 2 * slip_mean, 4)
        row["mt5_p90"] = round(row["spread_p90"] + 2 * slip_p90, 4)
        if pair in duka.index and np.isfinite(duka.loc[pair, "one_way_p90"]):
            row["duka_p50"] = round(2 * float(duka.loc[pair, "one_way_p50"]), 4)
            row["duka_p90"] = round(2 * float(duka.loc[pair, "one_way_p90"]), 4)
            row["mt5_measured"] = round(max(row["mt5_p90"], row["duka_p90"]), 4)
            row["binding"] = "mt5_p90" if row["mt5_p90"] > row["duka_p90"] else "duka_p90"
        pairs[str(pair)] = row
    return {"schema": SCHEMA, "asof": asof, "assumed_rt_pips": ASSUMED_RT_PIPS,
            "definition": "round trip = one full spread + slippage on each of 2 fills; mt5_measured = max(mt5_p90, duka_p90)",
            "sources": {"spreads": "data/costs/mt5_spreads.csv", "slippage": "data/paper_shadow/explore_journal.jsonl (mt5_open/mt5_close ok)",
                        "duka": "data/paper_shadow/measured_costs.csv"},
            "slippage_pooled": pooled, "pairs": pairs}


_CACHE: dict[str, Any] = {}


def load_table(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else TABLE_PATH
    key = str(p.resolve())
    if key not in _CACHE:
        t = json.loads(p.read_text(encoding="utf-8"))
        if t.get("schema") != SCHEMA:
            raise ValueError(f"unexpected cost table schema {t.get('schema')!r}")
        _CACHE[key] = t
    return _CACHE[key]


def rt_cost_pips(pair: str, model: str = DEFAULT_MODEL, table: dict[str, Any] | None = None) -> float:
    """Round-trip cost in pips for ``pair`` under ``model``. Raises MissingCost when the pair has no measured data."""
    if model not in COST_MODELS:
        raise ValueError(f"unknown cost model {model!r}; choose from {COST_MODELS}")
    if model == "assumed":
        return ASSUMED_RT_PIPS
    t = table or load_table()
    row = (t.get("pairs") or {}).get(pair.upper())
    if row is None or row.get(model) is None:
        raise MissingCost(f"{pair}: no {model} cost in table asof {t.get('asof')}")
    return float(row[model])


def cost_series(pairs: list[str], model: str = DEFAULT_MODEL, table: dict[str, Any] | None = None) -> tuple[dict[str, float], list[str]]:
    """({pair: rt_pips}, [pairs with no measured cost]) for a research run. Missing pairs are not filled."""
    out, missing = {}, []
    for p in pairs:
        try:
            out[p] = rt_cost_pips(p, model, table)
        except MissingCost:
            missing.append(p)
    return out, missing


def override_cfg_costs(cfg: dict[str, Any], pair: str, *, cost_model: str | None = None, cost_pips: float | None = None,
                       table: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """In-memory copy of cfg whose round-trip cost is replaced for one run. Never writes yaml.

    ``spread_pips`` is the round-trip cost in the walk-forward/backtest (``commission_pips`` is added to it), so the whole
    round trip goes there, and ``replay.slippage_pips`` is zeroed so slippage is not charged twice.
    Returns (new_cfg, info). With neither option given, the cfg is returned as an unchanged deep copy.
    """
    new = copy.deepcopy(cfg)
    info: dict[str, Any] = {"cost_override": False}
    if cost_pips is None and cost_model is None:
        return new, info
    if cost_pips is not None:
        rt = float(cost_pips)
        info["cost_source"] = "cli_cost_pips"
    else:
        rt = rt_cost_pips(pair, str(cost_model), table)
        info["cost_source"] = f"cost_model:{cost_model}"
    commission = float(new.get("commission_pips", 0.0) or 0.0)
    info.update({"cost_override": True, "rt_cost_pips": rt, "yaml_spread_pips": new.get("spread_pips"),
                 "yaml_slippage_pips": (new.get("replay") or {}).get("slippage_pips")})
    new["spread_pips"] = max(rt - commission, 0.0)
    new.setdefault("replay", {})["slippage_pips"] = 0.0
    return new, info