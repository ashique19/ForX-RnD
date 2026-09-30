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
    # Two bars; second deltas from previous close.
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


def test_decode_jetta_side_previous_close_deltas():
    out = decode_jetta_side(_side())
    stamps = sorted(out)
    assert len(stamps) == 2
    assert stamps[0] == pd.Timestamp("2024-01-01 00:00:00")
    o0, h0, l0, c0, v0 = out[stamps[0]]
    assert abs(o0 - 1.10000) < 1e-12
    assert abs(c0 - 1.10020) < 1e-12
    assert h0 >= max(o0, c0) and l0 <= min(o0, c0)

    o1, h1, l1, c1, v1 = out[stamps[1]]
    # deltas relative to previous close 1.10020
    assert abs(o1 - (1.10020 + 10 * 0.00001)) < 1e-12
    assert abs(h1 - (1.10020 + 40 * 0.00001)) < 1e-12
    assert abs(l1 - (1.10020 - 20 * 0.00001)) < 1e-12
    assert abs(c1 - (1.10020 + 25 * 0.00001)) < 1e-12
    assert h1 >= max(o1, c1) and l1 <= min(o1, c1)
    assert v1 == 562.4090361869424


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
