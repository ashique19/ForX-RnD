"""Advisory cards — never orders."""
from __future__ import annotations

from datetime import datetime, timezone

from forex_lab.advise import (
    ACTION_CLOSE,
    ACTION_HOLD,
    ACTION_NO_NEW,
    ACTION_TIGHTEN,
    suggest_actions,
    tighten_sl_from_atr,
)
from forex_lab.calendar import parse_events
from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.mtf import MTF_CONFLICT, MtfStatus

SAMPLE = [
    {
        "title": "Non-Farm Employment Change",
        "country": "USD",
        "date": "2026-09-21T12:30:00-04:00",
        "impact": "High",
        "forecast": "140K",
        "previous": "130K",
    }
]

CFG = {
    "label_scheme": "triple_barrier",
    "horizon": 8,
    "entry_timing": "next_open",
    "atr_period": 14,
    "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
    "advice": {
        "enabled": True,
        "before_minutes": 60,
        "during_minutes": 15,
        "after_minutes": 30,
        "before_action_open": "tighten_sl",
        "during_action_open": "hold",
        "after_action_open": "hold",
        "flatten_action": "close",
        "tighten_sl_atr": 1.0,
    },
    "calendar": {"highlight": ["NFP", "Non-Farm", "FOMC", "CPI"]},
}


def _events():
    return parse_events(SAMPLE)


def test_flat_before_nfp_suggests_no_new_opens():
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    cards = suggest_actions(
        pair="EURUSD",
        signal="BUY",
        validity="OK",
        position=None,
        events=_events(),
        cfg=CFG,
        now=now,
    )
    assert cards and cards[0].action == ACTION_NO_NEW
    assert cards[0].auto_submit is False
    assert "not an order" in cards[0].disclaimer.lower() or "never auto-submitted" in cards[0].disclaimer.lower()
    assert "Non-Farm" in (cards[0].event_title or "")


def test_open_before_nfp_flattens_to_close():
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    cards = suggest_actions(
        pair="EURUSD",
        signal="BUY",
        validity="OK",
        position={"side": "BUY", "sl": 1.09, "pair": "EURUSD"},
        events=_events(),
        cfg=CFG,
        now=now,
    )
    assert cards[0].action == ACTION_CLOSE


def test_open_before_generic_high_tightens_sl():
    generic = [
        {
            "title": "RBA Gov Speaks",
            "country": "AUD",
            "date": "2026-09-21T12:30:00-04:00",
            "impact": "High",
            "forecast": "",
            "previous": "",
        }
    ]
    events = parse_events(generic)
    assert events and not events[0].highlight
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    last = float(df["Close"].iloc[-1])
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    wide_sl = last - 0.05
    cards = suggest_actions(
        pair="AUDUSD",
        signal="BUY",
        validity="OK",
        position={"side": "BUY", "sl": wide_sl, "pair": "AUDUSD"},
        events=events,
        cfg=CFG,
        ohlcv=df,
        last_price=last,
        now=now,
    )
    assert cards
    assert cards[0].action in {ACTION_TIGHTEN, ACTION_HOLD}
    if cards[0].action == ACTION_TIGHTEN:
        assert cards[0].suggested_sl is not None
        assert cards[0].suggested_sl > wide_sl
        assert cards[0].suggested_sl < last


def test_mtf_conflict_flat_is_hold_off():
    mtf = MtfStatus(status=MTF_CONFLICT, timeframe="4h", direction="down", signal="BUY", note="4h down vs BUY")
    cards = suggest_actions(
        pair="EURUSD",
        signal="BUY",
        validity="OK",
        position=None,
        events=[],
        cfg=CFG,
        mtf=mtf,
        now=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert cards and cards[0].action == ACTION_NO_NEW
    assert "MTF" in cards[0].title or "MTF" in cards[0].detail


def test_stale_advice_is_na():
    cards = suggest_actions(
        pair="EURUSD",
        signal="BUY",
        validity="STALE",
        position=None,
        events=_events(),
        cfg=CFG,
    )
    assert cards and cards[0].action == "none"


def test_tighten_sl_uses_atr_and_refuses_widen():
    df = generate_synthetic_ohlcv(bars=80, seed=4)
    last = float(df["Close"].iloc[-1])
    proposed, note = tighten_sl_from_atr(
        df, CFG, "BUY", current_sl=last - 0.05, last_price=last, validity="OK"
    )
    assert proposed is not None
    assert proposed < last
    assert "ATR" in note
    none, reason = tighten_sl_from_atr(
        df, CFG, "BUY", current_sl=last - 1e-8, last_price=last, validity="OK"
    )
    assert none is None
    assert "tighter" in reason.lower() or "already" in reason.lower()


def test_advice_disabled():
    cfg = {**CFG, "advice": {**CFG["advice"], "enabled": False}}
    assert (
        suggest_actions(
            pair="EURUSD",
            signal="BUY",
            validity="OK",
            position=None,
            events=_events(),
            cfg=cfg,
            now=datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc),
        )
        == []
    )
