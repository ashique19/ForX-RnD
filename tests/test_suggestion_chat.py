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
    assert "research levels from gates" in line["text"]


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
    assert "prices live" in line["text"]
    assert "no model yet" in line["text"]
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


def test_multi_pair_watching_price():
    line = format_board_line(
        {"pair": "AUDUSD", "signal": "\u2014", "status": "need_train", "last": 0.69555},
        active="EURUSD",
    )
    assert line is not None
    assert line["kind"] == "status"
    assert "watching @ 0.69555" in line["text"]
    assert "prices live" in line["text"]
    assert "Train" in line["text"]
    assert "Fetch/Train" not in line["text"]


def test_window_gone_no_actionable_levels():
    line = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "HOLD",
            "raw_signal": "SELL",
            "gate_reason": "conf=0.44 < min 0.60",
            "last": 1.13,
        },
        active="EURUSD",
    )
    assert line is not None
    assert "No actionable target/stop while gated" in line["text"]


def test_open_window_levels_honesty():
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
    assert "research levels from gates" in line["text"]


def test_transition_window_just_closed():
    from forex_lab.suggestion_chat import format_transition_line

    base = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "HOLD",
            "raw_signal": "BUY",
            "gate_reason": "weekday_gate",
            "last": 1.13,
        },
        active="EURUSD",
    )
    assert base is not None
    tline = format_transition_line(
        "EURUSD", prev_kind="open_window", new_kind="window_gone", new_line=base
    )
    assert tline is not None
    assert "window just closed" in tline["text"]
    assert tline.get("transition") == "open_window->window_gone"


def test_feed_persists_kinds(tmp_path):
    from forex_lab.suggestion_chat import build_suggestion_feed, load_prev_kinds

    state = tmp_path / "state.json"
    feed = build_suggestion_feed(
        [{"pair": "EURUSD", "signal": "BUY", "last": 1.13, "target": 1.14, "stop": 1.12}],
        active="EURUSD",
        persist_state=True,
        state_path=state,
    )
    assert feed["kinds"]["EURUSD"] == "open_window"
    assert load_prev_kinds(state)["EURUSD"] == "open_window"
    feed2 = build_suggestion_feed(
        [
            {
                "pair": "EURUSD",
                "signal": "HOLD",
                "raw_signal": "BUY",
                "gate_reason": "weekday_gate",
                "last": 1.13,
            }
        ],
        active="EURUSD",
        prev_kinds=load_prev_kinds(state),
        persist_state=True,
        state_path=state,
    )
    texts = " ".join(x["text"] for x in feed2["lines"])
    assert "window just closed" in texts

def test_need_train_fingerprint_stable_across_price():
    a = format_board_line(
        {"pair": "AUDUSD", "signal": "\u2014", "status": "need_train", "last": 0.69555},
        active="EURUSD",
    )
    b = format_board_line(
        {"pair": "AUDUSD", "signal": "\u2014", "status": "need_train", "last": 0.69610},
        active="EURUSD",
    )
    assert a is not None and b is not None
    assert a["id"] == b["id"]
    assert "0.69555" in a["text"]
    assert "0.69610" in b["text"]


def test_need_train_without_price_asks_fetch_then_train():
    line = format_board_line(
        {"pair": "USDJPY", "signal": "\u2014", "status": "need_train"},
        active="EURUSD",
    )
    assert line is not None
    assert "need Fetch for bars" in line["text"]
    assert "Train" in line["text"]


def test_weekday_mute_clarity_and_stable_fp():
    a = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "HOLD",
            "raw_signal": "BUY",
            "gate_reason": "conf=0.44 < min 0.60 · weekday_gate blocks Mon,Thu (UTC); today=Thu",
            "last": 1.13,
            "confidence": 0.44,
        },
        active="EURUSD",
    )
    b = format_board_line(
        {
            "pair": "EURUSD",
            "signal": "HOLD",
            "raw_signal": "BUY",
            "gate_reason": "conf=0.49 < min 0.60 · weekday_gate blocks Mon,Thu (UTC); today=Thu",
            "last": 1.13,
            "confidence": 0.49,
        },
        active="EURUSD",
    )
    assert a is not None and b is not None
    assert "muted" in a["text"].lower()
    assert "lifts Fri UTC" in a["text"]
    assert "No actionable target/stop while muted" in a["text"]
    assert a["id"] == b["id"]  # conf ticks must not spam


def test_btcusd_pin_skip_honesty():
    line = format_board_line(
        {"pair": "BTCUSD", "signal": "\u2014", "status": "need_train", "last": 84320.22},
        active="EURUSD",
    )
    assert line is not None
    assert line["kind"] == "status"
    assert "pin-skip" in line["text"].lower() or "pin_skip" in line["id"]
    assert "Train idle" not in line["text"]
    assert line["id"].endswith("|pin_skip")


def test_open_window_lists_stop_target_at_price():
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
    assert "@ 1.13276" in line["text"]
    assert "target @ 1.13454" in line["text"]
    assert "stop @ 1.13098" in line["text"]

def test_fingerprint_stable_no_char_split():
    from forex_lab.suggestion_chat import fingerprint_stable_body

    body = (
        "EURUSD: muted (weekday gate) - don't buy now. "
        "muted Thu (UTC weekday gate; also Mon) - lifts Fri UTC | also conf=0.50 < min 0.60. "
        "No actionable target/stop while muted."
    )
    stable = fingerprint_stable_body("window_gone", body, "weekday")
    assert "E U R U S D" not in stable
    assert "EURUSD" in stable


def test_desk_call_prefers_open_window():
    from forex_lab.suggestion_chat import build_suggestion_feed

    feed = build_suggestion_feed(
        [
            {"pair": "AUDUSD", "signal": "HOLD", "last": 0.69, "confidence": 0.9},
            {
                "pair": "EURUSD",
                "signal": "BUY",
                "last": 1.13276,
                "target": 1.13454,
                "stop": 1.13098,
                "confidence": 0.72,
            },
        ],
        active="EURUSD",
    )
    call = feed.get("desk_call")
    assert call is not None
    assert call["pair"] == "EURUSD"
    assert call["source_kind"] == "open_window"
    assert call["actionable"] is True
    assert call.get("paper_action", {}).get("side") == "BUY"
    assert "Desk call" in call["headline"]


def test_desk_call_muted_day_not_actionable():
    from forex_lab.suggestion_chat import build_suggestion_feed

    feed = build_suggestion_feed(
        [
            {
                "pair": "EURUSD",
                "signal": "HOLD",
                "raw_signal": "BUY",
                "gate_reason": "weekday_gate blocks Mon,Thu (UTC); today=Thu",
                "confidence": 0.5,
                "last": 1.13,
            },
            {
                "pair": "NZDUSD",
                "signal": "HOLD",
                "raw_signal": "BUY",
                "gate_reason": "weekday_gate blocks Mon,Thu (UTC); today=Thu",
                "confidence": 0.68,
                "last": 0.62,
            },
        ],
        active="EURUSD",
    )
    call = feed.get("desk_call")
    assert call is not None
    assert call["actionable"] is False
    assert "no open window" in call["headline"].lower() or "mute" in call["headline"].lower()


def test_open_pos_richer_narrative():
    from forex_lab.suggestion_chat import format_board_line

    line = format_board_line(
        {"pair": "EURUSD", "signal": "BUY", "last": 1.13},
        active="EURUSD",
        open_position={
            "pair": "EURUSD",
            "trigger": "BUY",
            "entry_price": 1.13392,
            "pnl_text": "+0.12R",
            "source": "manual",
            "duration": "2h",
            "sl": 1.13000,
            "tp": 1.13800,
        },
    )
    assert line is not None
    assert line["kind"] == "open_pos"
    assert "via manual" in line["text"]
    assert "held 2h" in line["text"]
    assert "sl 1.13000" in line["text"]

