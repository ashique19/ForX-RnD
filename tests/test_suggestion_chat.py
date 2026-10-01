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
    assert "still within 72% BUY conf" in line["text"]
    assert "Buy and hold" in line["text"]
    assert "1.13276" in line["text"]
    assert "Research levels only" in line["text"]


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
    assert line["kind"] == "open_window"
    assert "lean" in line["text"]
    assert "below min_conf" in line["text"].lower()


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
    """Model/joblib/retrain copy stays in ModelBuildStrip — not suggestion chat."""
    line = format_board_line(
        {"pair": "AUDUSD", "signal": "\u2014", "status": "need_train", "last": 0.69},
        active="EURUSD",
    )
    assert line is None



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
    """need_train idle pairs are omitted from chat (trade advisories only)."""
    line = format_board_line(
        {"pair": "AUDUSD", "signal": "\u2014", "status": "need_train", "last": 0.69555},
        active="EURUSD",
    )
    assert line is None



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
    assert line["kind"] == "open_window"
    assert "below min_conf" in line["text"].lower()


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
    assert "research levels only" in line["text"].lower()


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
    """need_train rows are omitted from chat (no fingerprint spam / no model copy)."""
    a = format_board_line(
        {"pair": "NZDUSD", "signal": "HOLD", "status": "need_train", "last": 0.61},
        active="EURUSD",
    )
    b = format_board_line(
        {"pair": "NZDUSD", "signal": "HOLD", "status": "need_train", "last": 0.62},
        active="EURUSD",
    )
    assert a is None and b is None



def test_need_train_without_price_asks_fetch_then_train():
    """Untrained without price is also omitted — Fetch/Train UX lives outside chat."""
    line = format_board_line(
        {"pair": "AUDUSD", "signal": "HOLD", "status": "need_train", "last": None},
        active="EURUSD",
    )
    assert line is None



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
    assert "still within" in a["text"].lower()
    assert "Buy and hold" in a["text"] or "buy and hold" in a["text"].lower()
    assert a["kind"] == "open_window"
    assert a.get("muted_advisory") is True
    assert "not opening" in a["text"].lower()
    assert "Stoploss" in a["text"] or "stoploss" in a["text"].lower() or "levels not set" in a["text"].lower()
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
    assert "close at 1.13454" in line["text"]
    assert "Stoploss at 1.13098" in line["text"]

def test_fingerprint_stable_no_char_split():
    from forex_lab.suggestion_chat import fingerprint_stable_body

    body = (
        "EURUSD: advisory BUY @ 1.10000 - conf 50%. "
        "muted Thu (UTC weekday gate; also Mon) - lifts Fri UTC | also conf=0.50 < min 0.60. "
        "- not opening (live gate still muted)."
    )
    stable = fingerprint_stable_body("open_window", body, "weekday")
    assert "E U R U S D" not in stable
    assert "EURUSD" in stable


def test_below_min_conf_keeps_directional_lean():
    line = format_board_line(
        {
            "pair": "AUDUSD",
            "signal": "HOLD",
            "raw_signal": "SELL",
            "gate_reason": "conf=0.55 < min 0.60",
            "last": 0.69555,
            "target": 0.69200,
            "stop": 0.69800,
        },
        active="AUDUSD",
    )
    assert line is not None
    assert line["kind"] == "open_window"
    assert "AUDUSD lean" in line["text"]
    assert "Sell and hold @ 0.69555" in line["text"]
    assert "below min_conf" in line["text"].lower()
    assert "Stoploss at 0.69800" in line["text"]
    assert "close at 0.69200" in line["text"]


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


def test_desk_call_muted_day_muted_advisory_actionable():
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
    assert call.get("actionable") is True
    assert call.get("source_kind") == "open_window"
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



def test_desk_call_open_window_when_mute_lifts():
    """Fri-prep: when weekday mute is absent, BUY surfaces actionable open_window desk_call.

    Does not flip weekday_gate config — only asserts the path after mute lifts (e.g. Fri UTC).
    """
    from forex_lab.suggestion_chat import build_suggestion_feed

    feed = build_suggestion_feed(
        [
            {
                "pair": "EURUSD",
                "signal": "BUY",
                "last": 1.13276,
                "target": 1.13454,
                "stop": 1.13098,
                "confidence": 0.72,
                # no weekday_gate / muted — simulates Fri UTC after mute lifts
            },
            {
                "pair": "AUDUSD",
                "signal": "HOLD",
                "last": 0.69,
                "confidence": 0.4,
            },
        ],
        active="EURUSD",
    )
    call = feed.get("desk_call")
    assert call is not None
    assert call["actionable"] is True
    assert call["source_kind"] == "open_window"
    assert call["pair"] == "EURUSD"
    assert call.get("paper_action", {}).get("side") == "BUY"
    assert "open window" in call["headline"].lower() or "window" in call["headline"].lower()



def test_desk_call_prefers_unmuted_over_higher_conf_muted():
    """Unmuted open_window outranks muted advisory even when muted conf is higher.

    Fri-path readiness: when mute lifts on one pair, that window wins desk_call
    over a still-muted advisory on another pair.
    """
    from forex_lab.suggestion_chat import build_suggestion_feed

    feed = build_suggestion_feed(
        [
            {
                "pair": "GBPJPY",
                "signal": "HOLD",
                "raw_signal": "SELL",
                "gate_reason": "weekday_gate blocks Mon,Thu (UTC); today=Thu",
                "confidence": 0.88,
                "last": 208.0,
            },
            {
                "pair": "EURUSD",
                "signal": "BUY",
                "last": 1.13276,
                "target": 1.13454,
                "stop": 1.13098,
                "confidence": 0.62,
            },
        ],
        active="GBPJPY",
    )
    call = feed.get("desk_call")
    assert call is not None
    assert call["pair"] == "EURUSD"
    assert call.get("muted_advisory") is False
    assert call.get("conf_pct") == 62
    assert call["source_kind"] == "open_window"
    ow = [ln for ln in (feed.get("lines") or []) if ln.get("kind") == "open_window"]
    assert ow and ow[0]["pair"] == "EURUSD"
    assert ow[0].get("muted_advisory") in (None, False)


def test_board_conf_sort_among_unmuted():
    from forex_lab.suggestion_chat import build_suggestion_feed

    feed = build_suggestion_feed(
        [
            {
                "pair": "AUDUSD",
                "signal": "SELL",
                "last": 0.69,
                "target": 0.685,
                "stop": 0.695,
                "confidence": 0.61,
            },
            {
                "pair": "EURUSD",
                "signal": "BUY",
                "last": 1.13,
                "target": 1.14,
                "stop": 1.12,
                "confidence": 0.79,
            },
        ],
        active="AUDUSD",
    )
    call = feed.get("desk_call")
    assert call["pair"] == "EURUSD"
    assert call.get("conf_pct") == 79
    ow = [ln for ln in (feed.get("lines") or []) if ln.get("kind") == "open_window"]
    assert [ln["pair"] for ln in ow[:2]] == ["EURUSD", "AUDUSD"]


def test_mtf_conflict_is_window_gone_not_buy_advisory():
    line = format_board_line(
        {
            "pair": "NZDUSD",
            "signal": "HOLD",
            "raw_signal": "BUY",
            "gate_reason": "MTF conflict (4h down vs BUY) | muted Thu (UTC weekday gate)",
            "last": 0.58012,
            "confidence": 0.75,
            "stop": 0.578,
            "target": 0.583,
        },
        active="NZDUSD",
    )
    assert line is not None
    assert line["kind"] == "window_gone"
    assert "desk HOLD" in line["text"]
    assert "MTF conflict" in line["text"]
    assert "Buy and hold" not in line["text"]
    assert line.get("muted_advisory") in (None, False)
