"""Append-only annotations exclude suspect fills from Stage-2 counts."""
from __future__ import annotations

import json

from forex_lab import paper_shadow as ps


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_annotation_excludes_fill_without_editing_rows(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(path))
    ps.record_open(pair="EURJPY", side="SELL", position_id="pos_a", fill_id="fill_a")
    ps.record_close(pair="EURJPY", position_id="pos_a", fill_id="fill_a2")
    ps.record_open(pair="GBPUSD", side="SELL", position_id="pos_b", fill_id="fill_b")
    before = _lines(path)
    assert ps.fill_count() == 2
    assert ps.record_annotation(
        ref_fill_id="fill_b", ref_position_id="pos_b", reason="model_retrained_outside_gate"
    )
    after = _lines(path)
    assert after[: len(before)] == before  # append-only: earlier rows untouched
    ann = json.loads(after[-1])
    assert ann["event"] == "annotation" and ann["exclude_from_stage2"] is True
    assert ps.fill_count() == 1
    assert ps.fill_count(include_excluded=True) == 2
    # later close of the excluded position is excluded too
    ps.record_close(pair="GBPUSD", position_id="pos_b", fill_id="fill_b2")
    refs = ps.excluded_refs(ps.load_events())
    closes = [r for r in ps.load_events() if r.get("event") == "close" and not ps.is_excluded(r, refs)]
    assert [c["position_id"] for c in closes] == ["pos_a"]


def test_annotation_requires_ref_and_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(tmp_path / "j.jsonl"))
    assert ps.record_annotation(reason="x") is False
    assert ps.record_annotation(ref_fill_id="f", reason="") is False


def test_shadow_status_reports_excluded(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app

    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(path))
    ps.record_open(pair="EURUSD", side="BUY", position_id="p1", fill_id="f1")
    ps.record_open(pair="GBPUSD", side="SELL", position_id="p2", fill_id="f2")
    ps.record_annotation(ref_fill_id="f2", ref_position_id="p2", reason="model_retrained_outside_gate")
    body = TestClient(app).get("/shadow/status").json()
    assert body["fills"] == 1
    assert body["fills_raw"] == 2
    assert body["excluded"] == 1
