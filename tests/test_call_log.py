from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from forex_lab import call_log as cl

ROOT = Path(__file__).resolve().parents[1]
REAL = [ROOT / "data" / "paper_shadow" / n for n in ("journal.jsonl", "explore_journal.jsonl", "calls.jsonl")]


def _sc():
    import importlib.util

    spec = importlib.util.spec_from_file_location("score_calls", ROOT / "scripts" / "score_calls.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


@pytest.fixture(autouse=True)
def _real_files_untouched():
    before = {p: _sha(p) for p in REAL}
    yield
    assert {p: _sha(p) for p in REAL} == before


def _row(**kw):
    base = {"pair": "EURUSD", "signal": "HOLD", "raw_signal": "BUY", "confidence": 0.55, "last": 1.1000,
            "stop": None, "target": None, "last_signal_dhaka": "2026-10-09 19:00:00 Asia/Dhaka",
            "last_bar_dhaka": "2026-10-09 19:00:00 Asia/Dhaka", "gate_reason": "conf=0.55 < min 0.60",
            "validity": "OK", "status": "ready"}
    base.update(kw)
    return base


def test_calls_log_isolated_and_append_only(tmp_path, monkeypatch):
    p = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FORX_CALLS_LOG", str(p))
    now = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
    fn = lambda pair, side: {"sl": 1.0978, "tp": 1.1022, "horizon_bars": 8}
    r1 = cl.log_board([_row(), _row(pair="USDJPY", raw_signal="HOLD", signal="HOLD")], source="test", now=now, barrier_fn=fn)
    assert r1["written"] == 2
    r2 = cl.log_board([_row()], source="test", now=now, barrier_fn=fn)
    assert r2 == {"path": str(p), "written": 0, "duplicates_skipped": 1}
    recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]
    eu = [r for r in recs if r["pair"] == "EURUSD"][0]
    assert eu["signal_bar_utc"] == "2026-10-09T13:00:00Z"
    assert eu["stop"] == 1.0978 and eu["barrier_source"] == "board_atr_barrier_recomputed"
    assert eu["stage2"] is False and eu["never_feeds_training"] is True
    assert [r for r in recs if r["pair"] == "USDJPY"][0]["stop"] is None
    assert len(cl.load_calls(p)) == 2


def test_call_log_refuses_journal_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("FORX_CALLS_LOG", str(tmp_path / "journal.jsonl"))
    with pytest.raises(ValueError):
        cl.calls_path()


def _bars(start="2026-10-05 00:00", n=20, base=1.1000, step=0.0):
    idx = pd.date_range(start, periods=n, freq="1h")
    close = [base + step * i for i in range(n)]
    return pd.DataFrame({"Open": close, "High": [c + 0.0002 for c in close], "Low": [c - 0.0002 for c in close], "Close": close}, index=idx)


def _call(**kw):
    c = {"call_id": "c1", "pair": "EURUSD", "raw_signal": "BUY", "signal": "BUY", "confidence": 0.62,
         "signal_bar_utc": "2026-10-05T02:00:00Z", "price": 1.1004, "stop": 1.0984, "target": 1.1024, "horizon_bars": 8}
    c.update(kw)
    return c


def test_score_tp_sl_timeout_pending():
    sc = _sc()

    now = pd.Timestamp("2026-10-06 00:00")
    up = _bars(step=0.0005)  # rises 5 pips/bar -> TP (20 pips from entry) hit
    s = sc.score_call(_call(), up, cost_rt_pips=1.0, now_utc=now)
    assert s["status"] == "scored" and s["exit"] == "tp" and s["pips"] == pytest.approx(19.0)
    down = _bars(step=-0.0005)
    s = sc.score_call(_call(price=1.0990, stop=1.0970, target=1.1010), down, cost_rt_pips=1.0, now_utc=now)
    assert s["exit"] == "sl" and s["pips"] == pytest.approx(-21.0)
    flat = _bars(step=0.0)
    s = sc.score_call(_call(price=1.1, stop=1.098, target=1.102), flat, cost_rt_pips=1.0, now_utc=now)
    assert s["exit"] == "timeout" and s["pips"] == pytest.approx(-1.0)
    s = sc.score_call(_call(), up, cost_rt_pips=1.0, now_utc=pd.Timestamp("2026-10-05 06:00"))
    assert s["status"] == "pending"
    assert sc.score_call(_call(raw_signal="HOLD"), up, cost_rt_pips=1.0, now_utc=now)["status"] == "unscorable"


def test_buckets_and_aggregate():
    sc = _sc()

    assert sc.bucket(0.62) == "[0.60,0.65)" and sc.bucket(0.5) == "[0.50,0.55)" and sc.bucket(None) == "n/a"
    rows = [{"status": "scored", "confidence": 0.61, "pair": "EURUSD", "main_eligible": True, "pips": 10.0, "r": 0.5, "win": True},
            {"status": "scored", "confidence": 0.63, "pair": "EURUSD", "main_eligible": True, "pips": -5.0, "r": -0.25, "win": False}]
    agg = sc.aggregate(rows, "bucket")
    assert agg[0]["n"] == 2 and agg[0]["hit_rate"] == 0.5 and agg[0]["pf"] == 2.0
