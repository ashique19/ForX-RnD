"""Weekday open gate (Mon/Thu) — unit tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from forex_lab.features import LABEL_MAP
from forex_lab.gates import GATE_WEEKDAY, evaluate_open_gates
from forex_lab.weekday_gate import (
    RESOLVED_OFF,
    RESOLVED_ON,
    apply_replay_weekday_gate,
    apply_weekday_gate_to_pred,
    build_weekday_gate_mask,
    day_names,
    parse_block_weekdays,
    weekday_gate_blocks_now,
    weekday_gate_enabled,
    weekday_gate_report_line,
)


def _cfg(**wg_kwargs):
    base = {
        "enabled": True,
        "block": ["mon", "thu"],
        "tz": "UTC",
    }
    base.update(wg_kwargs)
    return {
        "replay": {
            "session_gate": "overlap",
            "weekday_gate": base,
        },
        "signals": {"min_confidence": 0.60, "sessions": []},
        "gates": {"enabled": True, "require_mtf_agree": False, "no_new_opens_in_event_window": False},
    }


def test_parse_block_weekdays():
    assert parse_block_weekdays(["mon", "thu"]) == [0, 3]
    assert parse_block_weekdays([0, 3, "Thursday"]) == [0, 3]
    assert parse_block_weekdays(None) == []
    assert parse_block_weekdays("fri") == [4]


def test_day_names():
    assert day_names([0, 3]) == ["Mon", "Thu"]


def test_apply_replay_resolves_on():
    cfg = _cfg()
    apply_replay_weekday_gate(cfg)
    assert cfg["replay"]["weekday_gate_resolved"] == RESOLVED_ON
    assert cfg["replay"]["weekday_gate_block_days"] == [0, 3]
    assert cfg["replay"]["weekday_gate_tz"] == "UTC"
    assert weekday_gate_enabled(cfg)
    assert "Mon" in (cfg["replay"]["weekday_gate_note"] or "")


def test_apply_replay_off():
    cfg = _cfg(enabled=False)
    apply_replay_weekday_gate(cfg)
    assert cfg["replay"]["weekday_gate_resolved"] == RESOLVED_OFF
    assert not weekday_gate_enabled(cfg)


def test_empty_block_list_resolves_off():
    cfg = _cfg(block=[])
    apply_replay_weekday_gate(cfg)
    assert cfg["replay"]["weekday_gate_resolved"] == RESOLVED_OFF


def test_mask_and_pred_blocks_mon_thu():
    cfg = _cfg()
    apply_replay_weekday_gate(cfg)
    # Mon, Tue, Wed, Thu, Fri at 14:00 UTC
    idx = pd.DatetimeIndex(
        [
            "2015-02-23 14:00:00",  # Mon
            "2015-02-24 14:00:00",  # Tue
            "2015-02-25 14:00:00",  # Wed
            "2015-02-26 14:00:00",  # Thu
            "2015-02-27 14:00:00",  # Fri
        ],
        tz="UTC",
    )
    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"]] * 5,
            "side": ["BUY"] * 5,
            "confidence": [0.7] * 5,
        },
        index=idx,
    )
    mask = build_weekday_gate_mask(pred, cfg)
    assert list(mask.astype(int)) == [1, 0, 0, 1, 0]
    out = apply_weekday_gate_to_pred(pred, cfg)
    assert list(out["pred"]) == [
        LABEL_MAP["HOLD"],
        LABEL_MAP["BUY"],
        LABEL_MAP["BUY"],
        LABEL_MAP["HOLD"],
        LABEL_MAP["BUY"],
    ]
    assert out.loc[idx[0], "side"] == "HOLD"
    assert out.loc[idx[1], "side"] == "BUY"


def test_naive_index_treated_as_utc():
    cfg = _cfg()
    apply_replay_weekday_gate(cfg)
    idx = pd.DatetimeIndex(["2015-02-23 14:00:00", "2015-02-24 14:00:00"])  # naive
    pred = pd.DataFrame({"pred": [LABEL_MAP["SELL"], LABEL_MAP["SELL"]]}, index=idx)
    out = apply_weekday_gate_to_pred(pred, cfg)
    assert list(out["pred"]) == [LABEL_MAP["HOLD"], LABEL_MAP["SELL"]]


def test_off_is_noop():
    cfg = _cfg(enabled=False)
    apply_replay_weekday_gate(cfg)
    idx = pd.DatetimeIndex(["2015-02-23 14:00:00"], tz="UTC")
    pred = pd.DataFrame({"pred": [LABEL_MAP["BUY"]]}, index=idx)
    out = apply_weekday_gate_to_pred(pred, cfg)
    assert list(out["pred"]) == [LABEL_MAP["BUY"]]


def test_blocks_now_live_helper():
    cfg = _cfg()
    apply_replay_weekday_gate(cfg)
    mon = datetime(2015, 2, 23, 14, 0, tzinfo=timezone.utc)
    tue = datetime(2015, 2, 24, 14, 0, tzinfo=timezone.utc)
    blocked, reason = weekday_gate_blocks_now(cfg, now=mon)
    assert blocked is True
    assert "Mon" in reason
    blocked2, _ = weekday_gate_blocks_now(cfg, now=tue)
    assert blocked2 is False


def test_evaluate_open_gates_blocks_on_thu():
    cfg = _cfg()
    apply_replay_weekday_gate(cfg)
    thu = datetime(2015, 2, 26, 14, 0, tzinfo=timezone.utc)
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.8,
        events=[],
        cfg=cfg,
        now=thu,
    )
    assert d.allowed is False
    assert GATE_WEEKDAY in d.blocked_by
    wed = datetime(2015, 2, 25, 14, 0, tzinfo=timezone.utc)
    d2 = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.8,
        events=[],
        cfg=cfg,
        now=wed,
    )
    assert d2.allowed is True
    assert GATE_WEEKDAY not in d2.blocked_by


def test_report_line():
    cfg = _cfg()
    apply_replay_weekday_gate(cfg)
    line = weekday_gate_report_line(cfg)
    assert "on" in line
    assert "Mon" in line and "Thu" in line
    assert "UTC" in line
