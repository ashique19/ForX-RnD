"""Tests for friendly suggestion-board chat formatter."""
from __future__ import annotations

from forex_lab.suggestion_chat import build_suggestion_feed, format_board_line


def test_open_buy_window_line():
    line = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "BUY",
            "last": 1.13276,
            "target": 1.13454,
            "stop": 1.13098,
            "confidence": 0.72,
        },
        active="EURUSD",
    )
    assert line is not None
    assert line["kind"] == "open_window"
    assert "open BUY window" in line["text"]
    assert "1.13276" in line["text"]
    assert "not an order" in line["text"]


def test_window_gone_gated():
    line = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "HOLD",
            "raw_signal": "BUY",
            "gate_reason": "conf=0.46 < min 0.60",
            "last": 1.13,
        },
        active="EURUSD",
    )
    assert line is not None
    assert line["kind"] == "window_gone"
    assert "Don't buy now" in line["text"]


def test_close_hint_when_flash_flips():
    line = format_board_line(
        {"pair": "EURUSD", "signal": "SELL", "last": 1.13},
        active="EURUSD",
        open_position={"pair": "EURUSD", "side": "BUY", "entry_price": 1.13392},
    )
    assert line is not None
    assert line["kind"] == "close_hint"
    assert "consider closing paper BUY" in line["text"]


def test_paper_open_monitoring_includes_pnl():
    line = format_board_line(
        {"pair": "EURUSD", "signal": "BUY", "last": 1.13},
        active="EURUSD",
        open_position={
            "pair": "EURUSD",
            "trigger": "BUY",
            "entry_price": 1.13392,
            "pnl_text": "+0.12R",
        },
    )
    assert line is not None
    assert line["kind"] == "open_pos"
    assert "paper BUY open" in line["text"]
    assert "+0.12R" in line["text"]


def test_need_train_placeholder_signal():
    line = format_board_line(
        {"pair": "AUDUSD", "signal": "\u2014", "status": "need_train", "last": 0.69},
        active="EURUSD",
    )
    assert line is not None
    assert line["kind"] == "status"
    assert "no trained flash yet" in line["text"]
    assert "\u2014" not in line["text"]


def test_gate_separator_cleaned():
    line = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "HOLD",
            "raw_signal": "BUY",
            "gate_reason": "conf=0.46 < min 0.60 \u00b7 weekday_gate blocks Mon,Thu",
            "last": 1.13,
        },
        active="EURUSD",
    )
    assert line is not None
    assert "\u00b7" not in line["text"]
    assert "weekday_gate" in line["text"]


def test_feed_active_first_and_honesty():
    feed = build_suggestion_feed(
        [
            {"pair": "BTCUSD", "signal": "HOLD", "last": 84000},
            {"pair": "EURUSD", "signal": "HOLD", "raw_signal": "BUY", "gate_reason": "weekday_gate", "last": 1.13},
        ],
        active="EURUSD",
    )
    assert feed["ok"] is True
    assert feed["auto_trade"] is False
    assert "Decision aid" in feed["honesty"]
    assert feed["lines"][0]["pair"] == "EURUSD"


def test_paper_closed_diary_line():
    from forex_lab.suggestion_chat import format_closed_trade_line

    line = format_closed_trade_line(
        {
            "pair": "EURUSD",
            "trigger": "BUY",
            "entry_price": 1.10000,
            "entry_price_text": "1.10000",
            "exit_price": 1.10150,
            "exit_price_text": "1.10150",
            "pnl_text": "+0.00150 (+0.75R)",
            "outcome": "RIGHT",
            "exit_reason": "tp",
            "source": "manual",
            "exit_time_dhaka": "2026-10-01 12:00 Asia/Dhaka",
        }
    )
    assert line is not None
    assert line["kind"] == "paper_closed"
    assert "paper BUY closed" in line["text"]
    assert "RIGHT" in line["text"]
    assert "paper diary" in line["text"]


def test_feed_includes_recent_closed():
    feed = build_suggestion_feed(
        [{"pair": "EURUSD", "signal": "HOLD", "status": "ok"}],
        active="EURUSD",
        recent_closed=[
            {
                "pair": "EURUSD",
                "trigger": "SELL",
                "entry_price_text": "1.12000",
                "exit_price_text": "1.11800",
                "pnl_text": "+0.00200",
                "outcome": "RIGHT",
                "exit_reason": "manual_close",
            }
        ],
    )
    kinds = [x["kind"] for x in feed["lines"]]
    assert "paper_closed" in kinds
    assert feed.get("paper_closed_lines") == 1
