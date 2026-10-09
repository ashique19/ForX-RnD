"""Exploration book: separate journal, never Stage-2, never trains / gates."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from forex_lab import explore_book as eb
from forex_lab import paper_shadow as ps


def _row(pair="EURUSD", raw="BUY", sig="HOLD", conf=0.55, gate="conf=0.55 < min 0.60", **kw):
    base = {
        "pair": pair, "signal": sig, "raw_signal": raw, "confidence": conf, "gate_reason": gate,
        "validity": "OK", "status": "ready", "muted": False, "last": 1.1000, "stop": 1.0980,
        "target": 1.1020, "horizon_bars": 8,
        "last_bar_dhaka": "2026-10-09 17:00:00 Asia/Dhaka", "last_signal_dhaka": "2026-10-09 17:00:00 Asia/Dhaka",
    }
    base.update(kw)
    return base


@pytest.fixture()
def journals(tmp_path, monkeypatch):
    main = tmp_path / "journal.jsonl"
    exp = tmp_path / "explore_journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(main))
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(exp))
    ps.record_open(pair="EURJPY", side="SELL", position_id="p0", fill_id="f0")
    return main, exp


def test_explore_takes_050_to_060_and_logs_propensity(journals):
    main, exp = journals
    main_before = main.read_bytes()
    out = eb.run_pass([_row(), _row(pair="NZDUSD", raw="SELL", conf=0.45, gate="conf=0.45 < min 0.60")])
    assert out["fills_added"] == 1
    assert main.read_bytes() == main_before  # main journal untouched
    assert ps.fill_count() == 1  # Stage-2 count unchanged
    rows = [json.loads(l) for l in exp.read_text(encoding="utf-8").splitlines()]
    dec = [r for r in rows if r["event"] == "decision"]
    assert {d["pair"] for d in dec} == {"EURUSD", "NZDUSD"}
    eur = next(d for d in dec if d["pair"] == "EURUSD")
    assert eur["take"] is True and eur["would_main_take"] is False
    assert eur["propensity"] == 1.0 and eur["threshold"] == 0.50 and eur["main_threshold"] == 0.60
    nzd = next(d for d in dec if d["pair"] == "NZDUSD")
    assert nzd["take"] is False and nzd["propensity"] == 0.0 and nzd["reason"].startswith("below_explore_min")
    assert all(r["schema"] == eb.SCHEMA for r in rows)


def test_max_two_positions_per_pair(journals):
    for _ in range(3):
        eb.run_pass([_row()])
    st = eb.status()
    assert st["open"] == 2 and st["fills"] == 2 and st["stage2"] is False


def test_other_gates_still_apply(journals):
    cfg = eb.EXPLORE_CONFIG
    assert eb.explore_eligible(_row(muted=True), cfg)[1] == "muted"
    assert eb.explore_eligible(_row(gate="weekday mute"), cfg)[1].startswith("gate:")
    assert eb.explore_eligible(_row(last_signal_dhaka="2026-10-08 17:00:00 Asia/Dhaka"), cfg)[1] == "stale_signal"
    assert eb.explore_eligible(_row(validity="STALE"), cfg)[1].startswith("validity")
    assert eb.explore_eligible(_row(raw="HOLD"), cfg)[1] == "not_directional"


def test_missing_board_sl_tp_uses_recomputed_barrier_or_skips(journals):
    _, exp = journals
    row = _row(stop=None, target=None)
    out = eb.run_pass([row], barrier_fn=lambda pair, side: None)
    assert out["fills_added"] == 0 and out["decisions"][0]["reason"] == "no_sl_tp"
    out = eb.run_pass([row], barrier_fn=lambda pair, side: {"sl": 1.0978, "tp": 1.1022, "horizon_bars": 8})
    assert out["fills_added"] == 1
    opened = [json.loads(l) for l in exp.read_text(encoding="utf-8").splitlines() if '"event":"open"' in l]
    assert opened[-1]["sl"] == 1.0978 and opened[-1]["barrier_source"] == "board_atr_barrier_recomputed"


def test_exit_tp_sl_duration(journals):
    t0 = datetime(2026, 10, 9, 11, 0, tzinfo=timezone.utc)
    eb.run_pass([_row()], now=t0)
    out = eb.run_pass([_row(last=1.1025, conf=0.40)], now=t0 + timedelta(minutes=30))
    assert [c["exit_reason"] for c in out["closes"]] == ["tp"]
    assert out["closes"][0]["pnl_r"] == pytest.approx(1.25)
    eb.run_pass([_row()], now=t0 + timedelta(hours=1))
    out = eb.run_pass([_row(conf=0.40)], now=t0 + timedelta(hours=10))
    assert [c["exit_reason"] for c in out["closes"]] == ["duration"]


def test_explore_journal_cannot_be_main_journal(tmp_path, monkeypatch):
    same = tmp_path / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(same))
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(same))
    with pytest.raises(ValueError):
        eb.explore_journal_path()


def test_config_not_in_frozen_yaml_and_not_fed_to_retrain():
    root = Path(__file__).resolve().parents[1]
    for y in (root / "config").glob("*.yaml"):
        assert "explore_journal" not in y.read_text(encoding="utf-8")
    for mod in ("forex_lab/retrain.py", "forex_lab/model.py", "forex_lab/backtest.py"):
        assert "explore" not in (root / mod).read_text(encoding="utf-8").lower()
    src = (root / "forex_lab" / "explore_book.py").read_text(encoding="utf-8")
    assert "/paper/order" not in src and "train_models" not in src


def test_explore_status_endpoint_read_only(journals):
    from fastapi.testclient import TestClient

    from api.main import app

    main, exp = journals
    eb.run_pass([_row()])
    before = (main.read_bytes(), exp.read_bytes())
    body = TestClient(app).get("/explore/status").json()
    assert body["ok"] is True and body["stage2"] is False
    assert body["fills"] == 1 and body["open"] == 1
    assert (main.read_bytes(), exp.read_bytes()) == before
