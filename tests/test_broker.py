"""PaperBroker / BrokerPort — local practice fills, no venue."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from forex_lab.broker import (
    BrokerError,
    BrokerPort,
    OrderGateway,
    PaperBroker,
    journal_aggregates,
    make_broker,
    position_for_pair,
)


def _ohlc(start: datetime, rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(rows), freq="h")
    data = [{"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1} for o, h, l, c in rows]
    df = pd.DataFrame(data, index=idx)
    df.index.name = "Datetime"
    return df


def test_make_broker_paper_only(tmp_path):
    cfg = {"broker": {"backend": "paper", "store": str(tmp_path / "p.json"), "default_size": 1.0}}
    b = make_broker(cfg)
    assert isinstance(b, PaperBroker)
    assert isinstance(b, BrokerPort)
    assert OrderGateway is BrokerPort
    assert position_for_pair(b, "EURUSD") is None
    with pytest.raises(BrokerError, match="not implemented"):
        make_broker({"broker": {"backend": "mt5"}})
    with pytest.raises(BrokerError, match="unknown"):
        make_broker({"broker": {"backend": "foo"}})


def test_submit_requires_price_and_one_position(tmp_path):
    b = PaperBroker(tmp_path / "p.json", cfg={"spread_pips": 0.0, "horizon": 8})
    with pytest.raises(BrokerError, match="no reference price"):
        b.submit("BUY", "EURUSD")
    fill = b.submit("BUY", "EURUSD", sl=1.09, tp=1.12, price=1.10, timeframe="1h", news_bias="mixed", news_note="fixture headline")
    assert fill["kind"] == "open"
    assert fill["price"] == pytest.approx(1.10)
    assert b.list_positions()[0]["news_bias"] == "mixed"
    assert "fixture" in b.list_positions()[0]["news_note"]
    assert len(b.list_positions()) == 1
    assert position_for_pair(b, "EURUSD")["side"] == "BUY"
    with pytest.raises(BrokerError, match="already has an open"):
        b.submit("SELL", "EURUSD", price=1.11)
    assert len(b.list_fills()) == 1


def test_tp_hit_is_right_and_sl_hit_is_wrong(tmp_path):
    start = datetime(2026, 9, 21, 8, 0, 0)
    # bar 0 = entry bar; subsequent bars can hit
    df = _ohlc(
        start,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1210, 1.0995, 1.1200),  # TP 1.12 for BUY
        ],
    )
    b = PaperBroker(tmp_path / "buy.json", cfg={"spread_pips": 0.0, "horizon": 8})
    b.submit(
        "BUY",
        "EURUSD",
        sl=1.0900,
        tp=1.1200,
        price=1.1000,
        entry_bar_time=str(df.index[0]),
        validity="OK",
        confidence=0.7,
        model_signal="BUY",
        horizon=8,
    )
    events = b.refresh_from_ohlcv("EURUSD", df)
    assert events and events[0]["reason"] == "tp"
    closed = b.list_closed()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "RIGHT"
    assert not b.list_positions()

    df_sl = _ohlc(
        start,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1010, 1.0890, 1.0900),  # SL 1.09
        ],
    )
    b2 = PaperBroker(tmp_path / "sell.json", cfg={"spread_pips": 0.0, "horizon": 8})
    b2.submit(
        "BUY",
        "EURUSD",
        sl=1.0900,
        tp=1.1200,
        price=1.1000,
        entry_bar_time=str(df_sl.index[0]),
        validity="STALE",
        confidence=0.3,
        horizon=8,
    )
    b2.refresh_from_ohlcv("EURUSD", df_sl)
    assert b2.list_closed()[0]["outcome"] == "WRONG"


def test_pending_until_enough_bars(tmp_path):
    start = datetime(2026, 9, 21, 8, 0, 0)
    df = _ohlc(start, [(1.1, 1.101, 1.099, 1.100)])
    b = PaperBroker(tmp_path / "p.json", cfg={"spread_pips": 0.0, "horizon": 8})
    b.submit(
        "SELL",
        "EURUSD",
        sl=1.12,
        tp=1.08,
        price=1.10,
        entry_bar_time=str(df.index[0]),
        validity="OK",
        confidence=0.5,
        horizon=8,
    )
    b.refresh_from_ohlcv("EURUSD", df)
    pos = b.list_positions()
    assert len(pos) == 1
    assert pos[0]["outcome"] == "PENDING"


def test_manual_close_is_flat(tmp_path):
    b = PaperBroker(tmp_path / "p.json", cfg={"spread_pips": 0.0})
    b.submit("BUY", "GBPUSD", price=1.25, sl=1.24, tp=1.27)
    fill = b.close(b.list_positions()[0]["id"], price=1.26, reason="manual")
    assert fill["kind"] == "close"
    assert b.list_closed()[0]["outcome"] == "FLAT"
    assert not b.list_positions()


def test_aggregates_and_improvement_notes(tmp_path):
    closed = [
        {
            "pair": "EURUSD",
            "outcome": "WRONG",
            "session": "london",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
        },
        {
            "pair": "EURUSD",
            "outcome": "RIGHT",
            "session": "london",
            "validity_at_entry": "OK",
            "conf_bucket": ">=0.60",
        },
        {
            "pair": "GBPUSD",
            "outcome": "WRONG",
            "session": "ny",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
        },
    ]
    agg = journal_aggregates(closed, [])
    assert agg["right"] == 1
    assert agg["wrong"] == 2
    assert agg["error_rate"] == pytest.approx(2 / 3)
    notes = " ".join(agg["notes"]).lower()
    assert "stale" in notes
    assert "min_confidence" in notes or "confidence" in notes
    assert any("STALE" in r["bucket"] for r in agg["by_validity"])


def test_persists_to_disk(tmp_path):
    path = tmp_path / "paper.json"
    b = PaperBroker(path, cfg={"spread_pips": 0.0})
    b.submit("SELL", "USDJPY", price=150.0, timeframe="1h")
    b2 = PaperBroker(path, cfg={"spread_pips": 0.0})
    assert len(b2.list_positions()) == 1
    assert b2.list_positions()[0]["pair"] == "USDJPY"


def test_modify_sl_is_paper_extra_not_on_port(tmp_path):
    b = PaperBroker(tmp_path / "sl.json", cfg={"spread_pips": 0.0})
    b.submit("BUY", "EURUSD", sl=1.0900, tp=1.1200, price=1.1000)
    pos = b.list_positions()[0]
    out = b.modify_sl(pos["id"], 1.0950, price=1.1000, note="user click")
    assert out["sl"] == pytest.approx(1.0950)
    assert out["sl_prev"] == pytest.approx(1.0900)
    with pytest.raises(BrokerError, match="widen"):
        b.modify_sl(pos["id"], 1.0800, price=1.1000)
    with pytest.raises(BrokerError, match="below"):
        b.modify_sl(pos["id"], 1.1000, price=1.1000)
    assert not hasattr(BrokerPort, "modify_sl") or "modify_sl" not in BrokerPort.__abstractmethods__


def test_paper_session_matches_board_asia_wrap_and_overlap(tmp_path):
    """Paper journal session labels must match board.sessions (Asia 21-07, london+ny)."""
    from forex_lab.broker import session_name

    cfg = {
        "board": {"sessions": {"asia": [21, 7], "london": [7, 16], "ny": [13, 21]}},
        "spread_pips": 0.0,
    }
    # Sunday FX open / late Asia — previously hard-coded as "off"
    assert session_name(datetime(2026, 9, 20, 22, 0, 0), cfg) == "asia"
    assert session_name(datetime(2026, 9, 21, 21, 30, 0), cfg) == "asia"
    # London∩NY — previously "overlap"
    assert session_name(datetime(2026, 9, 21, 14, 0, 0), cfg) == "london+ny"
    assert session_name(datetime(2026, 9, 21, 10, 0, 0), cfg) == "london"
    # Weekend closed
    assert session_name(datetime(2026, 9, 19, 12, 0, 0), cfg) == "closed"

    b = PaperBroker(tmp_path / "sess.json", cfg=cfg)
    b.submit(
        "BUY",
        "EURUSD",
        price=1.10,
        entry_bar_time="2026-09-20 22:00:00 UTC",
        validity="OK",
    )
    assert b.list_positions()[0]["session"] == "asia"

