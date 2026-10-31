"""Research-only measured cost model (forex_lab/cost_model.py)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from forex_lab import cost_model as cm


def _fixtures(tmp_path, spread_eur=1.0, duka_p90_one_way=0.3):
    rows = []
    for i in range(100):
        rows.append({"ts_utc": f"2026-10-14 {i % 24:02d}:00:00", "hour_utc": i % 24, "weekday": 2, "pair": "EURUSD",
                     "symbol": "EURUSD", "bid": 1.1, "ask": 1.1001, "spread_pips": spread_eur + (0.5 if i % 5 == 0 else 0.0),
                     "tick_n": 10, "tick_spread_p50": 1.0, "tick_spread_p90": 1.1})
    for i in range(30):  # USDJPY exists in spreads but not in the Duka table
        rows.append({"ts_utc": "2026-10-14 05:00:00", "hour_utc": 5, "weekday": 2, "pair": "USDJPY", "symbol": "USDJPY",
                     "bid": 150.0, "ask": 150.01, "spread_pips": 1.2, "tick_n": 1, "tick_spread_p50": 1.2, "tick_spread_p90": 1.2})
    sp = tmp_path / "sp.csv"
    pd.DataFrame(rows).to_csv(sp, index=False)
    jr = tmp_path / "j.jsonl"
    with jr.open("w", encoding="utf-8") as fh:
        for i in range(40):
            fh.write(json.dumps({"event": "mt5_open", "pair": "EURUSD", "ok": True, "slippage_pips": 0.2 if i % 4 == 0 else 0.0}) + "\n")
        fh.write(json.dumps({"event": "mt5_open", "pair": "EURUSD", "ok": False, "slippage_pips": 9.0}) + "\n")  # rejected: ignored
        fh.write(json.dumps({"event": "decision", "pair": "EURUSD", "slippage_pips": 9.0}) + "\n")
    du = tmp_path / "d.csv"
    pd.DataFrame([{"pair": "EURUSD", "one_way_p50": 0.2, "one_way_p90": duka_p90_one_way}]).to_csv(du, index=False)
    return sp, jr, du


def test_table_uses_only_ok_fills_and_pair_slippage(tmp_path):
    sp, jr, du = _fixtures(tmp_path)
    t = cm.build_table(sp, jr, du, asof="2026-10-31")
    e = t["pairs"]["EURUSD"]
    assert e["slip_n"] == 40 and e["slip_source"] == "pair"
    assert e["spread_p50"] == 1.0 and e["spread_p90"] == pytest.approx(1.5)
    assert e["slip_adverse_mean"] == pytest.approx(0.05)


def test_conservative_default_is_max_of_mt5_p90_and_duka(tmp_path):
    sp, jr, du = _fixtures(tmp_path, duka_p90_one_way=0.3)  # duka RT p90 = 0.6 < mt5 p90
    e = cm.build_table(sp, jr, du, asof="x")["pairs"]["EURUSD"]
    assert e["mt5_measured"] == max(e["mt5_p90"], e["duka_p90"]) == e["mt5_p90"] and e["binding"] == "mt5_p90"
    sp, jr, du = _fixtures(tmp_path, duka_p90_one_way=2.0)  # duka RT p90 = 4.0 > mt5 p90
    e = cm.build_table(sp, jr, du, asof="x")["pairs"]["EURUSD"]
    assert e["mt5_measured"] == 4.0 and e["binding"] == "duka_p90"


def test_missing_pair_is_reported_not_filled(tmp_path):
    sp, jr, du = _fixtures(tmp_path)
    t = cm.build_table(sp, jr, du, asof="x")
    assert "mt5_measured" not in t["pairs"]["USDJPY"]  # no Duka row => no conservative number
    with pytest.raises(cm.MissingCost):
        cm.rt_cost_pips("USDJPY", "mt5_measured", t)
    with pytest.raises(cm.MissingCost):
        cm.rt_cost_pips("GBPNZD", "mt5_p90", t)
    out, missing = cm.cost_series(["EURUSD", "USDJPY"], "mt5_measured", t)
    assert list(out) == ["EURUSD"] and missing == ["USDJPY"]
    assert cm.rt_cost_pips("USDJPY", "mt5_p90", t) > 0  # the measured MT5 number still exists


def test_unknown_model_and_assumed(tmp_path):
    with pytest.raises(ValueError):
        cm.rt_cost_pips("EURUSD", "bogus", {"pairs": {}})
    assert cm.rt_cost_pips("EURUSD", "assumed", {"pairs": {}}) == 1.4


def test_override_cfg_is_in_memory_copy_only():
    cfg = {"spread_pips": 1.0, "commission_pips": 0.0, "replay": {"slippage_pips": 0.2}}
    t = {"asof": "x", "pairs": {"EURUSD": {"mt5_measured": 1.4, "mt5_p90": 1.0}}}
    new, info = cm.override_cfg_costs(cfg, "EURUSD", cost_model="mt5_measured", table=t)
    assert new["spread_pips"] == 1.4 and new["replay"]["slippage_pips"] == 0.0 and info["cost_override"]
    assert cfg == {"spread_pips": 1.0, "commission_pips": 0.0, "replay": {"slippage_pips": 0.2}}  # original untouched
    same, info2 = cm.override_cfg_costs(cfg, "EURUSD")
    assert same == cfg and same is not cfg and not info2["cost_override"]
    new2, _ = cm.override_cfg_costs(cfg, "EURUSD", cost_pips=1.4)
    assert new2["spread_pips"] == 1.4


def test_committed_table_loads_and_is_conservative():
    t = cm.load_table()
    assert t["schema"] == cm.SCHEMA
    for pair, r in t["pairs"].items():
        assert r["mt5_measured"] >= r["mt5_p90"] and r["mt5_measured"] >= r["duka_p90"]

def test_cli_cost_flags_parse_and_override_in_memory():
    import argparse

    from forex_lab import cli

    p = cli.build_parser()
    a = p.parse_args(["retrain", "--pair", "USDJPY", "--cost-model", "mt5_measured", "--json"])
    assert a.cost_model == "mt5_measured" and a.cost_pips is None and not a.promote
    a2 = p.parse_args(["replay", "--pair", "EURUSD", "--cost-pips", "1.4"])
    assert a2.cost_pips == 1.4
    cfg = {"spread_pips": 1.0, "replay": {"slippage_pips": 0.2}}
    out = cli._apply_cost_override(a2, cfg, "EURUSD")
    assert out["spread_pips"] == 1.4 and cfg["spread_pips"] == 1.0
    plain = cli._apply_cost_override(argparse.Namespace(cost_model=None, cost_pips=None), cfg, "EURUSD")
    assert plain is cfg
