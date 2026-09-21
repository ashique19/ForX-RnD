"""Selective open gates — fail-soft, default-off, not a live edge."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from forex_lab.calendar import parse_events
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.features import LABEL_MAP
from forex_lab.gates import (
    GATE_CONF,
    GATE_EVENT,
    GATE_MTF,
    apply_frame_gates,
    apply_open_gates,
    evaluate_open_gates,
    gates_enabled,
)
from forex_lab.mtf import MTF_AGREE, MTF_CONFLICT, MTF_NA, MtfStatus, last_htf_slope
from forex_lab.ui.board import (
    BoardRow,
    attach_next_event,
    board_table,
    paper_actions_label,
    paper_submit_allowed,
    paper_submit_block_reason,
    row_from_signal,
)

NFP = [
    {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "date": "2026-09-21T12:30:00-04:00",
        "impact": "High",
        "forecast": "140K",
        "previous": "130K",
    }
]

GATES_ON = {
    "enabled": True,
    "apply_to_flash": True,
    "apply_to_paper": True,
    "require_mtf_agree": True,
    "min_confidence": 0.40,
    "no_new_opens_in_event_window": True,
    "event_windows": ["before", "during", "after"],
    "fail_soft": True,
}

CFG = {
    "label_scheme": "triple_barrier",
    "horizon": 8,
    "entry_timing": "next_open",
    "atr_period": 14,
    "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
    "signals": {"min_confidence": 0.40},
    "advice": {"before_minutes": 60, "during_minutes": 15, "after_minutes": 30},
    "calendar": {"before_minutes": 60, "during_minutes": 15, "after_minutes": 30},
    "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
    "feature_extras": {"higher_tf": ["4h"], "htf_sma_window": 20, "htf_slope_span": 3},
    "board": {"mtf_confirm": {"enabled": True, "timeframes": ["4h"], "conflict_flash": "off"}},
    "gates": dict(GATES_ON),
}


def _events():
    return parse_events(NFP)


def _mtf(status: str, **kw) -> MtfStatus:
    return MtfStatus(
        status=status,
        timeframe=kw.get("timeframe", "4h"),
        direction=kw.get("direction", "up"),
        signal=kw.get("signal", "BUY"),
        note=kw.get("note", ""),
        slope=kw.get("slope", 0.01),
    )


def test_gates_default_off_in_empty_cfg():
    assert gates_enabled({}) is False
    assert gates_enabled({"gates": {"enabled": False}}) is False
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.2,
        mtf=_mtf(MTF_CONFLICT),
        events=_events(),
        cfg={"gates": {"enabled": False}},
        now=datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc),
    )
    assert d.allowed is True
    assert d.flash == "BUY"


def test_mtf_conflict_blocks_when_enabled():
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.7,
        mtf=_mtf(MTF_CONFLICT, direction="down", note="4h down vs BUY"),
        events=[],
        cfg=CFG,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert d.allowed is False
    assert GATE_MTF in d.blocked_by
    assert d.flash == "HOLD"
    assert "conflict" in d.block_reason().lower()


def test_mtf_agree_passes():
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.7,
        mtf=_mtf(MTF_AGREE, direction="up"),
        events=[],
        cfg=CFG,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert d.allowed is True
    assert GATE_MTF not in d.blocked_by


def test_low_confidence_blocks_directional():
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="SELL",
        confidence=0.21,
        mtf=_mtf(MTF_AGREE, direction="down", signal="SELL"),
        events=[],
        cfg=CFG,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert d.allowed is False
    assert GATE_CONF in d.blocked_by


def test_event_window_blocks_new_opens_even_on_hold():
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="HOLD",
        confidence=0.7,
        mtf=_mtf(MTF_NA),
        events=_events(),
        cfg=CFG,
        now=now,
    )
    assert d.allowed is False
    assert GATE_EVENT in d.blocked_by
    reason = d.block_reason().lower()
    assert "no new opens" in reason
    assert "nfp" in reason or "non-farm" in reason
    # Relative countdown, not a raw UTC stamp the desk would misread.
    assert "in " in reason or "ago" in reason or "now" in reason
    assert "+00:00" not in d.block_reason()
    assert "T16:30" not in d.block_reason()


def test_missing_calendar_and_mtf_fail_soft_no_crash():
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.7,
        mtf=None,
        events=None,
        cfg=CFG,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert d.allowed is True
    assert any(h.fail_soft for h in d.hits)
    empty = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=0.7,
        mtf=_mtf(MTF_NA),
        events=[],
        cfg=CFG,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert empty.allowed is True
    broken = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY",
        confidence=None,
        mtf="not-an-mtf",  # type: ignore[arg-type]
        events=[object()],  # type: ignore[list-item]
        cfg=CFG,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert broken.allowed is True
    assert any("fail-soft" in n.lower() or "error" in n.lower() for n in (broken.fail_soft_notes or ["fail-soft"]))


def test_apply_open_gates_rewrites_flash_and_blocks_paper():
    df = generate_synthetic_ohlcv(bars=400, seed=7)
    last = {
        "datetime": "2026-09-21 15:00:00",
        "signal": "BUY",
        "raw_signal": "BUY",
        "confidence": 0.55,
        "dir_edge": 0.22,
        "p_buy": 0.42,
        "p_sell": 0.20,
        "p_hold": 0.38,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, CFG)
    row.mtf = _mtf(MTF_CONFLICT, direction="down", note="4h down vs BUY")
    row.buy_sell = "BUY"
    row.raw_signal = "BUY"
    apply_open_gates(row, CFG, events=[], now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc))
    assert row.buy_sell == "HOLD"
    assert row.raw_signal == "BUY"
    assert row.gate_blocked is True
    assert "gated HOLD" in (row.signal_details or "")
    assert paper_submit_allowed(row.validity, row, CFG, events=[]) is False
    assert "disabled" in paper_actions_label(row)
    assert "STALE" not in paper_submit_block_reason(row.validity, row, CFG, events=[])
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    attach_next_event(row, _events(), now=now, cfg=CFG)
    assert row.next_event_warn is True
    assert row.gate_blocked is True
    table = board_table([row], events=_events(), now=now, cfg=CFG)
    assert str(table.iloc[0]["Actions"]).startswith("disabled")
    assert table.iloc[0]["Signal"] == "HOLD"


def test_apply_open_gates_off_leaves_buy():
    row = BoardRow(
        pair="EURUSD",
        timeframe="1h",
        buy_sell="BUY",
        target="n/a",
        signal_details="conf=0.5",
        status="ready",
        raw_signal="BUY",
        confidence=0.55,
        validity="OK",
        mtf=_mtf(MTF_CONFLICT),
    )
    off = {**CFG, "gates": {"enabled": False}}
    apply_open_gates(row, off, events=_events(), now=datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc))
    assert row.buy_sell == "BUY"
    assert row.gate_blocked is False
    assert paper_submit_allowed("OK") is True
    assert paper_submit_allowed("STALE", row, off) is False


def test_frame_gates_fail_soft_on_missing_slope():
    idx = pd.date_range("2024-01-02 12:00", periods=4, freq="h")
    frame = pd.DataFrame(
        {
            "pred_raw": [2, 2, 0, 2],
            "confidence": [0.7, 0.7, 0.7, 0.2],
            "tf_4h_sma_slope": [0.02, -0.02, pd.NA, 0.02],
        },
        index=idx,
    )
    pred = pd.Series([LABEL_MAP["BUY"], LABEL_MAP["BUY"], LABEL_MAP["SELL"], LABEL_MAP["BUY"]], index=idx)
    out = apply_frame_gates(pred, frame, CFG, force=True)
    # agree, conflict, missing slope (fail-soft keep SELL), low conf HOLD
    assert list(out) == [LABEL_MAP["BUY"], LABEL_MAP["HOLD"], LABEL_MAP["SELL"], LABEL_MAP["HOLD"]]


def test_frame_gates_noop_when_disabled():
    idx = pd.date_range("2024-01-02 12:00", periods=2, freq="h")
    frame = pd.DataFrame({"confidence": [0.7, 0.7], "tf_4h_sma_slope": [-0.02, -0.02]}, index=idx)
    pred = pd.Series([LABEL_MAP["BUY"], LABEL_MAP["BUY"]], index=idx)
    out = apply_frame_gates(pred, frame, {**CFG, "gates": {"enabled": False}})
    assert list(out) == [LABEL_MAP["BUY"], LABEL_MAP["BUY"]]


def test_mtf_slope_on_synthetic_still_fail_soft_without_badge():
    df = generate_synthetic_ohlcv(bars=400, seed=3)
    slope = last_htf_slope(df, CFG, "4h")
    assert slope is not None
    # No MtfStatus object at all — must not raise.
    d = evaluate_open_gates(
        pair="EURUSD",
        signal="BUY" if slope > 0 else "SELL",
        confidence=0.8,
        mtf=None,
        events=None,
        cfg=CFG,
    )
    assert d.allowed is True
