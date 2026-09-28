"""Replay session_gate (SERIAL step 5) — overlap / london_ny / off."""
from __future__ import annotations

import pandas as pd

from forex_lab.model import LABEL_MAP, apply_signal_filters
from forex_lab.session import (
    apply_replay_session_gate,
    normalize_session_gate,
    session_gate_allow_list,
    session_gate_hours_note,
)


def test_normalize_session_gate():
    assert normalize_session_gate("overlap") == "overlap"
    assert normalize_session_gate("ldn_ny") == "overlap"
    assert normalize_session_gate("london_ny") == "london_ny"
    assert normalize_session_gate("london+ny") == "london_ny"
    assert normalize_session_gate("off") == "off"
    assert normalize_session_gate(None) == "off"


def test_session_gate_allow_list():
    assert session_gate_allow_list("overlap") == ["ldn_ny"]
    assert session_gate_allow_list("london_ny") == ["london", "ny"]
    assert session_gate_allow_list("off") is None


def test_hours_note_mentions_utc_and_dhaka():
    note = session_gate_hours_note("overlap")
    assert "13:00" in note and "16:00" in note
    assert "Asia/Dhaka" in note


def test_apply_replay_session_gate_sets_sessions():
    cfg = {"replay": {"session_gate": "overlap"}, "signals": {"sessions": [], "min_confidence": 0.55}}
    out = apply_replay_session_gate(cfg)
    assert out["signals"]["sessions"] == ["ldn_ny"]
    assert out["replay"]["session_gate_resolved"] == "overlap"


def test_apply_replay_session_gate_off_noop():
    cfg = {"replay": {"session_gate": "off"}, "signals": {"sessions": [], "min_confidence": 0.55}}
    apply_replay_session_gate(cfg)
    assert cfg["signals"]["sessions"] == []


def test_apply_replay_session_gate_respects_explicit_sessions():
    cfg = {
        "replay": {"session_gate": "overlap"},
        "signals": {"sessions": ["london", "ny"], "min_confidence": 0.55},
    }
    apply_replay_session_gate(cfg)
    assert cfg["signals"]["sessions"] == ["london", "ny"]
    assert cfg["replay"]["session_gate_resolved"] == "london_ny"


def test_filter_overlap_uses_sess_ldn_ny():
    idx = pd.RangeIndex(4)
    frame = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"]] * 4,
            "pred_raw": [LABEL_MAP["BUY"]] * 4,
            "confidence": [0.7] * 4,
            "dir_edge": [0.2] * 4,
            "sess_london": [0.0, 1.0, 0.0, 1.0],
            "sess_ny": [0.0, 0.0, 1.0, 1.0],
            "sess_ldn_ny": [0.0, 0.0, 0.0, 1.0],
        },
        index=idx,
    )
    cfg = {"signals": {"min_confidence": 0.4, "min_dir_edge": 0.0, "sessions": ["ldn_ny"]}}
    out = apply_signal_filters(frame, cfg)
    assert list(out) == [LABEL_MAP["HOLD"], LABEL_MAP["HOLD"], LABEL_MAP["HOLD"], LABEL_MAP["BUY"]]


def test_filter_overlap_alias():
    idx = pd.RangeIndex(2)
    frame = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"], LABEL_MAP["BUY"]],
            "pred_raw": [LABEL_MAP["BUY"], LABEL_MAP["BUY"]],
            "confidence": [0.7, 0.7],
            "dir_edge": [0.2, 0.2],
            "sess_ldn_ny": [0.0, 1.0],
        },
        index=idx,
    )
    cfg = {"signals": {"min_confidence": 0.4, "sessions": ["overlap"]}}
    out = apply_signal_filters(frame, cfg)
    assert list(out) == [LABEL_MAP["HOLD"], LABEL_MAP["BUY"]]
