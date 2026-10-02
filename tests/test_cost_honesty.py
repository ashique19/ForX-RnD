"""Stage-3 cost honesty probe helpers."""
from __future__ import annotations

import json
from pathlib import Path

from forex_lab import cost_honesty as ch
from forex_lab import paper_shadow as ps


def test_enrich_assumed_when_ba_missing():
    out = ch.enrich_assumed_cost_fields(bid=None, ask=None, pair="EURUSD")
    assert out["ba_available"] is False
    assert out["assumed_cost_pips"] == 1.4
    assert out["assumed_spread_pips"] == 1.0
    assert out["assumed_slippage_pips"] == 0.2
    assert out["measured_spread_pips"] is None


def test_enrich_measured_when_ba_present():
    out = ch.enrich_assumed_cost_fields(bid=1.1000, ask=1.10010, pair="EURUSD")
    assert out["ba_available"] is True
    assert out["assumed_cost_pips"] is None
    assert out["measured_spread_pips"] == 1.0


def test_build_event_stamps_assumed_cost(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(path))
    ok = ps.record_open(pair="EURUSD", side="BUY", entry_mid=1.1, source="board")
    assert ok is True
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert row["ba_available"] is False
    assert row["assumed_cost_pips"] == 1.4


def test_run_cost_probe_smoke():
    report = ch.run_cost_probe(sample_n=50)
    assert report["schema"] == "cost_honesty_probe_v1"
    assert "assumed" in report and report["assumed"]["assumed_spread_pips"] == 1.0
    assert isinstance(report.get("pairs"), list) and len(report["pairs"]) >= 1
    # EURUSD tip is mid-only in this repo snapshot.
    eurusd = next((p for p in report["pairs"] if p["pair"] == "EURUSD"), None)
    if eurusd and eurusd.get("tip_exists"):
        assert eurusd["tip_mid_only"] is True


def test_measured_cost_pair_dual_stamp_and_reject(tmp_path, monkeypatch):
    table = tmp_path / "measured.json"
    table.write_text(json.dumps({
        "schema": "measured_costs_v1",
        "as_of": "2026-10-02",
        "pairs": [
            {"pair": "EURUSD", "window_start": "2025-01-01", "window_end": "2025-01-02", "n_bars": 2, "one_way_p50": 0.4321, "one_way_p90": 0.7, "stamp_rejected": False, "reject_reason": None, "notes": "test"},
            {"pair": "BTCUSD", "window_start": "", "window_end": "", "n_bars": 0, "one_way_p50": None, "one_way_p90": None, "stamp_rejected": True, "reject_reason": "no BA", "notes": "test"},
        ],
    }), encoding="utf-8")
    monkeypatch.setenv("FORX_MEASURED_COSTS_TABLE", str(table))
    out = ch.enrich_assumed_cost_fields(bid=None, ask=None, pair="EURUSD")
    assert out["measured_cost_pair"] == 0.8642
    assert out["assumed_cost_pips"] == 1.4
    rejected = ch.enrich_assumed_cost_fields(bid=None, ask=None, pair="BTCUSD")
    assert rejected["measured_cost_pair"] is None


def test_measured_cost_lookup_missing_is_fail_soft(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_MEASURED_COSTS_TABLE", str(tmp_path / "missing.json"))
    assert ch.measured_cost_for_pair("EURUSD") is None
