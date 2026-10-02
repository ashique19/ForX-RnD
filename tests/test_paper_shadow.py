"""Shadow journal fail-soft append (Stage-2)."""
from __future__ import annotations

import json
from pathlib import Path

from forex_lab import paper_shadow as ps


def test_record_open_appends_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(path))
    ok = ps.record_open(
        pair="EURUSD",
        side="BUY",
        conf=0.72,
        muted=False,
        below_min=False,
        entry_mid=1.085,
        sl=1.08,
        tp=1.09,
        source="board",
        position_id="pos_test",
        fill_id="fill_test",
        notes="unit",
    )
    assert ok is True
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "paper_shadow_v1"
    assert row["event"] == "open"
    assert row["pair"] == "EURUSD"
    assert row["side"] == "BUY"
    assert row["source"] == "board"
    assert row["ts_utc"].endswith("Z")
    assert "Asia/Dhaka" in row["ts_dhaka"]
    assert row["bid"] is None and row["ask"] is None
    assert row["assumed_cost_pips"] == 1.4
    assert row["measured_cost_pair"] == 0.8


def test_append_fail_soft_bad_path(monkeypatch):
    # Directory-as-file forces write failure; must not raise.
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", "/nonexistent_dir_shadow_xx/nope/journal.jsonl")
    # On Windows this may still create parents; force by pointing at an invalid device-ish path via monkeypatch of open.
    def boom(*_a, **_k):
        raise OSError("forced")

    monkeypatch.setattr(Path, "open", boom)
    assert ps.record_close(pair="EURUSD", exit_mid=1.08, source="desk") is False


def test_flags_from_gate_text():
    muted, below = ps.flags_from_gate_text("muted Thu (UTC weekday gate) | conf=0.55 < min_conf")
    assert muted is True
    assert below is True

def test_record_desk_call_appends_skip_row_and_creates_parent(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(path))
    assert ps.record_desk_call(
        pair="EURUSD",
        side="BUY",
        conf=0.70,
        source="desk",
        advisory_id="board-123",
        notes="auto paper skip (brief): Missing stop/target",
    ) is True
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["event"] == "desk_call"
    assert row["source"] == "desk"
    assert row["advisory_id"] == "board-123"
    assert row["entry_mid"] is None
    assert row["ba_available"] is False
    assert row["measured_cost_pair"] == 0.8


def test_fill_count_is_open_events_and_fails_soft(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(path))
    path.write_text(
        '{"event":"open"}\n{"event":"desk_call"}\nnot-json\n{"event":"close"}\n',
        encoding="utf-8",
    )
    assert ps.fill_count() == 1
    path.unlink()
    assert ps.fill_count() == 0
