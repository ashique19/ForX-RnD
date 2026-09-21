"""Paper lookback scorer — RIGHT / WRONG / PENDING. Not a live edge."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from forex_lab.broker import PaperBroker
from forex_lab.score import (
    conf_bucket,
    filter_journal,
    journal_aggregates,
    normalize_outcome,
    outcome_from_exit,
    score_from_ohlcv,
    signed_return,
    walk_barriers,
)


def _ohlc(start: datetime, rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(rows), freq="h")
    data = [{"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1} for o, h, l, c in rows]
    df = pd.DataFrame(data, index=idx)
    df.index.name = "Datetime"
    return df


START = datetime(2026, 9, 21, 8, 0, 0)


def _pos(**over) -> dict:
    row = {
        "pair": "EURUSD",
        "side": "BUY",
        "entry_price": 1.1000,
        "entry_bar_time": str(pd.Timestamp(START)),
        "sl": 1.0900,
        "tp": 1.1200,
        "horizon": 8,
        "spread_frac": 0.0,
        "validity_at_entry": "OK",
        "confidence": 0.7,
        "conf_bucket": ">=0.60",
        "session": "london",
        "outcome": "PENDING",
    }
    row.update(over)
    return row


def test_conf_bucket_edges():
    assert conf_bucket(None) == "n/a"
    assert conf_bucket("x") == "n/a"
    assert conf_bucket(0.39) == "<0.40"
    assert conf_bucket(0.40) == "0.40-0.60"
    assert conf_bucket(0.59) == "0.40-0.60"
    assert conf_bucket(0.60) == ">=0.60"


def test_buy_tp_is_right_and_sl_is_wrong():
    tp_df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1210, 1.0995, 1.1200),
        ],
    )
    right = score_from_ohlcv(_pos(), tp_df, {"horizon": 8})
    assert right.status == "closed"
    assert right.outcome == "RIGHT"
    assert right.reason == "tp"

    sl_df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1010, 1.0890, 1.0900),
        ],
    )
    wrong = score_from_ohlcv(_pos(), sl_df, {"horizon": 8})
    assert wrong.outcome == "WRONG"
    assert wrong.reason == "sl"


def test_sell_tp_is_right_and_sl_is_wrong():
    tp_df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1010, 1.0790, 1.0800),  # TP 1.08
        ],
    )
    right = score_from_ohlcv(_pos(side="SELL", sl=1.1200, tp=1.0800), tp_df)
    assert right.outcome == "RIGHT"
    assert right.reason == "tp"

    sl_df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1210, 1.0990, 1.1200),  # SL 1.12
        ],
    )
    wrong = score_from_ohlcv(_pos(side="SELL", sl=1.1200, tp=1.0800), sl_df)
    assert wrong.outcome == "WRONG"
    assert wrong.reason == "sl"


def test_pending_until_enough_bars():
    df = _ohlc(START, [(1.1, 1.101, 1.099, 1.100)])
    scored = score_from_ohlcv(_pos(horizon=8), df)
    assert scored.status == "pending"
    assert scored.outcome == "PENDING"
    assert scored.bars_seen == 0  # no bar strictly after entry


def test_same_bar_tp_and_sl_is_wrong():
    df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1210, 1.0890, 1.1100),  # both 1.12 and 1.09
        ],
    )
    scored = score_from_ohlcv(_pos(), df)
    assert scored.outcome == "WRONG"
    assert scored.reason == "sl"


def test_horizon_timeout_scores_signed_move():
    up = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1020, 1.0990, 1.1010),
            (1.1010, 1.1030, 1.1000, 1.1020),
            (1.1020, 1.1050, 1.1010, 1.1040),
        ],
    )
    right = score_from_ohlcv(_pos(horizon=3, tp=1.2000, sl=1.0000), up)
    assert right.status == "closed"
    assert right.reason == "timeout"
    assert right.outcome == "RIGHT"
    assert right.bars_seen == 3

    down = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1010, 1.0980, 1.0990),
            (1.0990, 1.1000, 1.0970, 1.0980),
            (1.0980, 1.0990, 1.0960, 1.0970),
        ],
    )
    wrong = score_from_ohlcv(_pos(horizon=3, tp=1.2000, sl=1.0000), down)
    assert wrong.reason == "timeout"
    assert wrong.outcome == "WRONG"

    sell_down = score_from_ohlcv(
        _pos(side="SELL", horizon=3, tp=1.0000, sl=1.2000),
        down,
    )
    assert sell_down.reason == "timeout"
    assert sell_down.outcome == "RIGHT"


def test_horizon_pending_when_short_of_bars():
    df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1020, 1.0990, 1.1010),
            (1.1010, 1.1030, 1.1000, 1.1020),
        ],
    )
    scored = score_from_ohlcv(_pos(horizon=8, tp=1.2000, sl=1.0000), df)
    assert scored.status == "pending"
    assert scored.outcome == "PENDING"
    assert scored.bars_seen == 2


def test_empty_ohlcv_stays_pending():
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    scored = score_from_ohlcv(_pos(), empty)
    assert scored.outcome == "PENDING"
    assert score_from_ohlcv(_pos(), None).outcome == "PENDING"


def test_outcome_from_exit_and_normalize_timeout():
    assert outcome_from_exit("tp") == "RIGHT"
    assert outcome_from_exit("sl") == "WRONG"
    assert outcome_from_exit("manual") == "FLAT"
    assert outcome_from_exit(None) == "PENDING"
    assert (
        outcome_from_exit("timeout", side="BUY", entry=1.10, exit_px=1.11, spread_frac=0.0)
        == "RIGHT"
    )
    assert (
        outcome_from_exit("timeout", side="BUY", entry=1.10, exit_px=1.10, spread_frac=0.0)
        == "WRONG"
    )
    assert normalize_outcome({"outcome": "TIMEOUT", "realized": 0.01}) == "RIGHT"
    assert normalize_outcome({"outcome": "TIMEOUT", "realized": -0.002}) == "WRONG"
    assert normalize_outcome({"outcome": "RIGHT"}) == "RIGHT"
    assert normalize_outcome({"outcome": "PENDING"}) == "PENDING"


def test_walk_barriers_close_path_uses_close_only():
    df = _ohlc(
        START,
        [
            (1.10, 1.13, 1.07, 1.10),
            (1.10, 1.13, 1.07, 1.105),  # high would hit 1.12 TP; close does not
        ],
    )
    status, _px, reason, bars = walk_barriers(
        df,
        start_loc=1,
        horizon=8,
        side="BUY",
        entry=1.10,
        sl=1.09,
        tp=1.12,
        path="close",
    )
    assert status == "pending"
    assert reason is None
    assert bars == 1


def test_filter_journal_wrong_session_validity_conf():
    rows = [
        {
            "outcome": "WRONG",
            "session": "london",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
            "pair": "EURUSD",
        },
        {
            "outcome": "RIGHT",
            "session": "ny",
            "validity_at_entry": "OK",
            "conf_bucket": ">=0.60",
            "pair": "GBPUSD",
        },
        {
            "outcome": "TIMEOUT",
            "realized": -0.01,
            "session": "london",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
            "pair": "USDJPY",
        },
    ]
    wrongs = filter_journal(rows, wrong_only=True)
    assert {r["pair"] for r in wrongs} == {"EURUSD", "USDJPY"}
    london = filter_journal(rows, session="london")
    assert {r["pair"] for r in london} == {"EURUSD", "USDJPY"}
    stale = filter_journal(rows, validity="STALE")
    assert len(stale) == 2
    low = filter_journal(rows, conf="<0.40")
    assert len(low) == 2
    combo = filter_journal(rows, wrong_only=True, session="ny")
    assert combo == []


def test_aggregates_hit_rate_and_stale_session_conf_notes():
    closed = [
        {
            "pair": "EURUSD",
            "side": "BUY",
            "outcome": "WRONG",
            "session": "london",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
            "news_bias": "bearish",
        },
        {
            "pair": "EURUSD",
            "side": "BUY",
            "outcome": "RIGHT",
            "session": "ny",
            "validity_at_entry": "OK",
            "conf_bucket": ">=0.60",
            "news_bias": "mixed",
        },
        {
            "pair": "GBPUSD",
            "side": "SELL",
            "outcome": "WRONG",
            "session": "london",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
            "news_bias": "bullish",
        },
        {
            "pair": "USDJPY",
            "side": "BUY",
            "outcome": "TIMEOUT",
            "realized": -0.004,
            "session": "london",
            "validity_at_entry": "STALE",
            "conf_bucket": "<0.40",
            "exit_reason": "timeout",
            "news_bias": "bearish",
        },
    ]
    agg = journal_aggregates(closed, [{}])
    assert agg["n_pending"] == 1
    assert agg["right"] == 1
    assert agg["wrong"] == 3  # two WRONG + timeout with negative realized
    assert agg["hit_rate"] == pytest.approx(1 / 4)
    assert agg["error_rate"] == pytest.approx(3 / 4)
    notes = " ".join(agg["notes"]).lower()
    assert "not a live edge" in notes
    assert "stale" in notes
    assert "london" in notes
    assert "min_confidence" in notes or "low-confidence" in notes
    assert "news" in notes
    stale_row = next(r for r in agg["by_validity"] if r["bucket"] == "STALE")
    assert stale_row["wrong"] == 3
    assert stale_row["hit_rate"] == pytest.approx(0.0)
    london = next(r for r in agg["by_session"] if r["bucket"] == "london")
    assert london["n_scored"] == 3


def test_paperbroker_horizon_lookback_closes_right(tmp_path):
    df = _ohlc(
        START,
        [
            (1.1000, 1.1010, 1.0990, 1.1000),
            (1.1000, 1.1020, 1.0990, 1.1010),
            (1.1010, 1.1030, 1.1000, 1.1020),
            (1.1020, 1.1050, 1.1010, 1.1040),
        ],
    )
    b = PaperBroker(tmp_path / "h.json", cfg={"spread_pips": 0.0, "horizon": 3})
    b.submit(
        "BUY",
        "EURUSD",
        sl=1.0000,
        tp=1.2000,
        price=1.1000,
        entry_bar_time=str(df.index[0]),
        validity="OK",
        confidence=0.55,
        horizon=3,
    )
    events = b.refresh_from_ohlcv("EURUSD", df)
    assert events and events[0]["reason"] == "timeout"
    closed = b.list_closed()
    assert len(closed) == 1
    assert closed[0]["outcome"] == "RIGHT"
    assert closed[0]["exit_reason"] == "timeout"
    assert not b.list_positions()
    agg = b.aggregates()
    assert agg["hit_rate"] == pytest.approx(1.0)
    assert agg["timeout"] == 1


def test_signed_return_buy_sell():
    assert signed_return("BUY", 100.0, 101.0) == pytest.approx(0.01)
    assert signed_return("SELL", 100.0, 99.0) == pytest.approx(0.01)
    assert signed_return("SELL", 100.0, 101.0) == pytest.approx(-0.01)
