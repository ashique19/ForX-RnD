"""Dark dense terminal theme — presentation only."""
from __future__ import annotations

from pathlib import Path

from forex_lab.paths import project_root
from forex_lab.ui.board import style_board
from forex_lab.ui.theme import (
    BUY,
    HOLD,
    SELL,
    alert_tone,
    signal_cell_style,
    signal_fill,
    terminal_css,
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
