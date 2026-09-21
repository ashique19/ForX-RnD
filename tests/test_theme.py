"""Dark dense terminal theme — presentation only."""
from __future__ import annotations

from pathlib import Path

from forex_lab.paths import project_root
from forex_lab.ui.board import style_board
from forex_lab.ui.theme import (
    BUY,
    HOLD,
    SELL,
    WARN,
    alert_tone,
    empty_state_html,
    masthead_html,
    scan_legend_html,
    scan_row_tone,
    signal_badge_html,
    signal_cell_style,
    signal_fill,
    terminal_css,
    validity_badge_html,
    validity_cell_style,
)


def test_theme_config_is_dark_terminal():
    cfg = (project_root() / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'base = "dark"' in cfg
    assert "primaryColor" in cfg
    assert "#16c784" in cfg
    assert "toolbarMode" in cfg and "minimal" in cfg
    assert "backgroundColor" in cfg


def test_buy_sell_hold_are_high_contrast():
    buy_bg, buy_fg = signal_fill("BUY")
    sell_bg, sell_fg = signal_fill("SELL")
    hold_bg, hold_fg = signal_fill("HOLD")
    assert buy_bg == BUY and buy_fg != buy_bg
    assert sell_bg == SELL and sell_fg != sell_bg
    assert hold_bg != BUY and hold_bg != SELL
    assert HOLD in hold_fg or hold_fg != hold_bg
    css = terminal_css()
    assert "--fx-buy" in css and BUY in css
    assert "--fx-sell" in css and SELL in css
    assert "st-key-paper_buy" in css
    assert "st-key-paper_sell" in css
    assert "fx-masthead" in css
    assert "stDeployButton" in css
    assert "fx-empty" in css
    assert "fx-legend" in css
    assert "st-key-ws_apply" in css
    assert "--fx-kicker" in css
    assert "fx-badge-hold" in css
    assert "fx-badge-stale" in css


def test_style_board_uses_dark_signal_colors():
    import pandas as pd

    df = pd.DataFrame(
        {
            "Pair": ["EURUSD", "GBPUSD", "USDJPY"],
            "Signal": ["BUY", "SELL", "HOLD"],
            "Data●": ["OK", "STALE", "MISSING"],
            "Actions": ["BUY/SELL", "disabled (STALE)", "CLOSE"],
        }
    )
    styled = style_board(df)
    html = styled.to_html() if hasattr(styled, "to_html") else ""
    # pandas Styler needs jinja2; the desk already falls back to a plain table.
    if "background-color" in html:
        assert "16c784" in html.lower() or BUY.lower() in html.lower()
        assert "ea3943" in html.lower() or SELL.lower() in html.lower()
    buy_css = signal_cell_style("BUY")
    sell_css = signal_cell_style("SELL")
    hold_css = signal_cell_style("HOLD")
    assert BUY in buy_css
    assert SELL in sell_css
    assert "background-color" in hold_css
    assert "16c784" in validity_cell_style("OK") or BUY in validity_cell_style("OK")
    assert "ea3943" in validity_cell_style("FAIL") or SELL in validity_cell_style("FAIL")
    from forex_lab.ui.theme import awareness_status_style, WARN

    assert WARN in awareness_status_style("STALE · data stale")
    assert alert_tone("stale") != alert_tone("flip")


def test_theme_does_not_touch_broker_or_timezone_defaults():
    from forex_lab.config_loader import load_config

    cfg = load_config()
    assert str((cfg.get("broker") or {}).get("backend") or "paper") == "paper"
    ui = cfg.get("ui") or {}
    assert "Dhaka" in str(ui.get("timezone") or "")
    source = Path(project_root() / "forex_lab" / "ui" / "theme.py").read_text(encoding="utf-8")
    assert "BrokerPort" in source
    assert "Asia/Dhaka" in source


def test_scan_badges_make_buy_sell_hold_and_stale_obvious():
    buy = signal_badge_html("BUY", compact=True)
    sell = signal_badge_html("SELL", compact=True)
    hold = signal_badge_html("HOLD", compact=True)
    stale = signal_badge_html("BUY", compact=True, validity="STALE")
    dash = signal_badge_html("—", compact=True, validity="STALE")
    assert BUY in buy and "BUY" in buy
    assert SELL in sell and "SELL" in sell
    assert "fx-badge-hold" in hold and "HOLD" in hold
    assert BUY not in hold
    assert "fx-badge-stale" in stale
    assert BUY not in stale
    assert "—" in dash
    assert WARN in validity_badge_html("STALE", compact=True)
    assert "STALE" in validity_badge_html("STALE", compact=True)
    assert scan_row_tone("BUY", "OK") == "buy"
    assert scan_row_tone("SELL", "OK") == "sell"
    assert scan_row_tone("HOLD", "OK") == "hold"
    assert scan_row_tone("BUY", "STALE") == "stale"
    assert scan_row_tone("HOLD", "MISSING") == "dead"
    assert "sel" in scan_row_tone("BUY", "OK", selected=True)


def test_empty_state_and_masthead_escape_and_legend():
    html = empty_state_html("Watchlist is empty", "Add a pair <EURUSD>.")
    assert "fx-empty" in html
    assert "Watchlist is empty" in html
    assert "<EURUSD>" not in html
    assert "&lt;EURUSD&gt;" in html
    legend = scan_legend_html()
    for token in ("BUY", "SELL", "HOLD", "STALE"):
        assert token in legend
    head = masthead_html("21:00:00 Asia/Dhaka", "Asia/Dhaka")
    assert "fx-masthead" in head
    assert "SIGNAL SCREEN" in head
    assert "PAPER" in head
    assert "Asia/Dhaka" in head
    assert "<script>" not in masthead_html("<script>x</script>", "tz")
    assert "&lt;script&gt;" in masthead_html("<script>x</script>", "tz")
