"""Unit tests for Jetta H1 history decode + month merge."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from forex_lab.history import (
    JETTA_TO_DUKA_VOLUME_FACTOR,
    decode_jetta_side,
    fetch_jetta_month,
    jetta_instrument,
    jetta_month_to_bars,
)


def _side(mult: float = 0.00001) -> dict:
    # Two bars. Later o/h/l/c are deltas from the previous same field.
    return {
        "multiplier": mult,
        "shift": 60_000,
        "timestamp": 1_704_067_200_000,  # 2024-01-01 00:00 UTC
        "open": 1.10000,
        "high": 1.10050,
        "low": 1.09950,
        "close": 1.10020,
        "times": [0, 60],
        "opens": [0, 10],
        "highs": [0, 40],
        "lows": [0, -20],
        "closes": [0, 25],
        "volumes": [281.2045180934712, 562.4090361869424],
    }


def test_jetta_instrument():
    assert jetta_instrument("USDJPY") == "USD-JPY"
    assert jetta_instrument("eur/usd") == "EUR-USD"


def test_decode_jetta_side_same_field_deltas():
    out = decode_jetta_side(_side())
    stamps = sorted(out)
    assert len(stamps) == 2
    assert stamps[0] == pd.Timestamp("2024-01-01 00:00:00")
    o0, h0, l0, c0, v0 = out[stamps[0]]
    assert abs(o0 - 1.10000) < 1e-12
    assert abs(h0 - 1.10050) < 1e-12
    assert abs(l0 - 1.09950) < 1e-12
    assert abs(c0 - 1.10020) < 1e-12
    assert h0 >= max(o0, c0) and l0 <= min(o0, c0)

    o1, h1, l1, c1, v1 = out[stamps[1]]
    mult = 0.00001
    # Each field chains off the previous same field, not the previous close.
    assert abs(o1 - (o0 + 10 * mult)) < 1e-12
    assert abs(h1 - (h0 + 40 * mult)) < 1e-12
    assert abs(l1 - (l0 - 20 * mult)) < 1e-12
    assert abs(c1 - (c0 + 25 * mult)) < 1e-12
    # Close's same-field base is the previous close, so that one price is unchanged.
    assert abs(c1 - (1.10020 + 25 * mult)) < 1e-12
    # The old decoder would have shifted Open by the previous body.
    old_open = c0 + 10 * mult
    assert abs(o1 - old_open) > 1e-8
    assert h1 >= max(o1, c1) and l1 <= min(o1, c1)
    assert v1 == 562.4090361869424


def test_decode_open_bid_matches_same_field_evidence():
    """Open/bid check from EDGE_REALITY_20261009.

    The stored (wrong) Open_t - Close_{t-1} equals the true Open_t - Open_{t-1}.
    A previous-close base puts High/Low inside the body, and the wick clamp
    then stores a wickless bar. Same-field bases keep the wick.
    """
    mult = 0.00001
    payload = {
        "multiplier": mult,
        "shift": 60_000,
        "timestamp": 1_640_995_200_000,  # 2022-01-01 00:00 UTC
        "open": 1.10000,
        "high": 1.10200,
        "low": 1.09800,
        "close": 1.10100,
        "times": [0, 60, 60],
        "opens": [0, 50, 10],
        "highs": [0, -80, 5],
        "lows": [0, 20, -15],
        "closes": [0, -30, 12],
        "volumes": [1.0, 1.0, 1.0],
    }
    out = decode_jetta_side(payload)
    stamps = sorted(out)
    o0, h0, l0, c0, _v0 = out[stamps[0]]
    o1, h1, l1, c1, _v1 = out[stamps[1]]
    o2, _h2, _l2, c2, _v2 = out[stamps[2]]

    true_open_delta = 50 * mult
    wrong_open = c0 + true_open_delta
    assert abs((wrong_open - c0) - (o1 - o0)) < 1e-12
    assert abs(o1 - (o0 + true_open_delta)) < 1e-12
    assert abs(o1 - wrong_open) > 1e-8
    # Close is the one field whose same-field base is the previous close.
    assert abs(c1 - (c0 - 30 * mult)) < 1e-12
    # Bar 2 still chains off bar 1's open, not bar 1's close.
    assert abs(o2 - (o1 + 10 * mult)) < 1e-12
    assert abs(o2 - (c1 + 10 * mult)) > 1e-8
    assert abs(c2 - (c1 + 12 * mult)) < 1e-12

    # Wrong previous-close reconstruction, then the same wick clamp the old decoder applied.
    wrong_h = c0 + (-80) * mult
    wrong_l = c0 + 20 * mult
    wrong_c = c0 + (-30) * mult
    clamped_h = max(wrong_h, wrong_open, wrong_c)
    clamped_l = min(wrong_l, wrong_open, wrong_c)
    assert abs(clamped_h - max(wrong_open, wrong_c)) < 1e-12
    assert abs(clamped_l - min(wrong_open, wrong_c)) < 1e-12
    # Fixed bar keeps a wick on both sides.
    assert h1 > max(o1, c1)
    assert l1 < min(o1, c1)
    assert h0 == 1.10200 and l0 == 1.09800


def test_jetta_month_scales_volume_to_duka_units():
    bid = _side()
    ask = _side()
    # nudge ask slightly above bid
    ask["open"] = 1.10002
    ask["high"] = 1.10052
    ask["low"] = 1.09952
    ask["close"] = 1.10022
    frame = jetta_month_to_bars(bid, ask, scale_volume=True)
    assert len(frame) == 2
    # volumes were 281.20.. and 562.40.. averaged same -> / factor ~= 100 and 200
    assert abs(frame["Volume"].iloc[0] - 100.0) < 1.0
    assert abs(frame["Volume"].iloc[1] - 200.0) < 1.0
    assert (frame["AskClose"] >= frame["BidClose"]).all()
    assert (frame["High"] >= frame[["Open", "Close"]].max(axis=1)).all()
    assert (frame["Low"] <= frame[["Open", "Close"]].min(axis=1)).all()
    mult = 0.00001
    # Bid and ask each chain off their own previous field, not the previous close.
    assert abs(frame["BidOpen"].iloc[1] - (1.10000 + 10 * mult)) < 1e-12
    assert abs(frame["AskOpen"].iloc[1] - (1.10002 + 10 * mult)) < 1e-12
    assert abs(frame["BidHigh"].iloc[1] - (1.10050 + 40 * mult)) < 1e-12
    assert abs(frame["AskLow"].iloc[1] - (1.09952 - 20 * mult)) < 1e-12
    assert abs(frame["BidClose"].iloc[1] - (1.10020 + 25 * mult)) < 1e-12
    assert abs(frame["BidOpen"].iloc[1] - (1.10020 + 10 * mult)) > 1e-8


def test_fetch_jetta_month_uses_fetch_json_seam():
    calls: list[str] = []

    def fake(url: str) -> dict:
        calls.append(url)
        side = "BID" if "/BID/" in url else "ASK"
        payload = _side()
        if side == "ASK":
            payload = dict(payload)
            payload["open"] = 1.10002
            payload["high"] = 1.10052
            payload["low"] = 1.09952
            payload["close"] = 1.10022
        return payload

    frame = fetch_jetta_month(
        "EURUSD",
        2024,
        1,
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 1, 1),
        fetch_json=fake,
    )
    assert len(frame) == 2
    assert any("/EUR-USD/BID/2024/1" in u for u in calls)
    assert any("/EUR-USD/ASK/2024/1" in u for u in calls)
    assert float(JETTA_TO_DUKA_VOLUME_FACTOR) > 2.0
