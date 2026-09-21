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


def _rel_lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    rgb = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]

    def _f(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_f(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: str, bg: str) -> float:
    l1, l2 = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def test_theme_config_is_dark_terminal():
    cfg = (project_root() / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert 'base = "dark"' in cfg
    assert "primaryColor" in cfg
    assert "#16c784" in cfg
    assert "toolbarMode" in cfg and "minimal" in cfg
    assert "backgroundColor" in cfg
    assert "baseFontSize = 16" in cfg
    assert "textColor = \"#f2f5f8\"" in cfg


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
    assert "fx-empty" in css
    assert "fx-legend" in css
    assert "fx-scan" in css
    assert "flex-end" in css
    assert "st-key-paper_close" in css
    assert BUY == "#16c784" and SELL == "#ea3943"


def test_muted_captions_and_labels_are_readable():
    """Body 16px, captions 14px, secondary #c8d0db+ — not dark grey on dark."""
    from forex_lab.ui.health import awareness_table_html
    from forex_lab.ui.theme import (
        BG,
        FONT_BODY,
        FONT_CAPTION,
        FONT_CELL,
        FONT_EXPANDER,
        FONT_LABEL,
        MUTED,
        NEUTRAL,
        TEXT,
        validity_badge_html,
    )

    assert FONT_BODY == "16px"
    assert FONT_CAPTION == "14px"
    assert FONT_LABEL == "14px"
    assert FONT_CELL == "15px"
    assert FONT_EXPANDER == "16px"
    assert min(_rgb(MUTED)) >= 200
    assert MUTED.lower() >= "#c8d0db"
    assert _contrast(MUTED, BG) >= 10.0
    assert _contrast(TEXT, BG) >= 12.0
    assert _contrast(NEUTRAL, BG) >= 8.0
    css = terminal_css()
    assert MUTED in css
    assert TEXT in css
    assert "--fx-muted" in css
    assert "stSidebar" in css
    assert "stTooltipContent" in css
    assert "font-size: 16px" in css
    assert "font-size: 14px" in css
    # Old too-small caption / widget-label rem sizes must not remain.
    assert "font-size: 0.70rem !important" not in css
    assert "font-size: 0.62rem !important" not in css
    stale = validity_badge_html("STALE", compact=True)
    assert "14px" in stale
    table = awareness_table_html(
        [
            {
                "Source": "EURUSD 1h OHLCV",
                "Observing": "price",
                "Cadence": "manual",
                "Last OK": "2026-09-21 15:00:00 Asia/Dhaka",
                "Status": "STALE · data stale — refresh required",
            }
        ]
    )
    assert MUTED in table
    assert "15px" in table
    assert "data stale" in table


def test_theme_css_applies_to_captions_markdown_dataframe_expanders():
    """CSS must actually target Streamlit caption / markdown / grid / expander nodes."""
    from forex_lab.ui.theme import FONT_CAPTION, FONT_BODY, MUTED, TEXT

    css = terminal_css()
    for needle in (
        '[data-testid="stCaptionContainer"]',
        '[data-testid="stMarkdownContainer"]',
        '[data-testid="stDataFrame"]',
        '[data-testid="stExpander"]',
        '[data-testid="stWidgetLabel"]',
        '[data-testid="stHeading"]',
        "stCaption",
    ):
        assert needle in css, f"missing selector {needle}"
    # Forced sizes (px) so Streamlit cannot mix captions back to ~11px.
    assert f"font-size: {FONT_BODY}" in css
    assert f"font-size: {FONT_CAPTION}" in css
    assert f"color: var(--fx-muted)" in css
    assert f"color: var(--fx-text)" in css
    assert MUTED in css and TEXT in css
    # Masthead clock / title are large and bold.
    assert "font-size: 22px" in css
    assert ".fx-clock" in css and ".fx-title" in css
    source = Path(project_root() / "forex_lab" / "ui" / "theme.py").read_text(encoding="utf-8")
    assert "data-fx-theme" in source


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
    assert "STALE" in source


def test_stale_outranks_buy_sell_hold():
    from forex_lab.ui.theme import (
        WARN,
        WARN_BG,
        empty_state_html,
        scan_counts,
        scan_emphasis,
        scan_legend_html,
        scan_strip_html,
        signal_badge_html,
        validity_badge_html,
    )

    assert scan_emphasis("BUY", "STALE") == "STALE"
    assert scan_emphasis("SELL", "MISSING") == "MISSING"
    assert scan_emphasis("HOLD", "ERROR") == "ERROR"
    assert scan_emphasis("BUY", "OK") == "BUY"
    assert scan_emphasis("HOLD", "OK") == "HOLD"
    assert scan_emphasis("—", "OK") == "—"

    counts = scan_counts(
        [
            {"buy_sell": "BUY", "validity": "OK"},
            {"buy_sell": "SELL", "validity": "STALE"},
            {"buy_sell": "HOLD", "validity": "OK"},
            {"buy_sell": "—", "validity": "MISSING"},
        ]
    )
    assert counts["BUY"] == 1
    assert counts["SELL"] == 0  # leftover SELL on a STALE row is not a live call
    assert counts["HOLD"] == 1
    assert counts["STALE"] == 1
    assert counts["MISSING"] == 1
    strip_counts = scan_strip_html(
        session="LONDON",
        refreshed="2026-09-21 20:00:00",
        tz="Asia/Dhaka",
        counts=counts,
    )
    assert ">STALE <b>1</b>" in strip_counts.replace("\n", "")
    assert "MISSING" in strip_counts
    assert "fx-count missing" in strip_counts
    assert ">STALE <b>2</b>" not in strip_counts.replace("\n", "")

    buy = signal_badge_html("BUY", compact=True)
    hold = signal_badge_html("HOLD", compact=True)
    dash = signal_badge_html("—", compact=True)
    assert "fx-sig-buy" in buy and BUY in buy
    assert "fx-sig-quiet" in hold
    assert "fx-sig-muted" in dash

    stale = validity_badge_html("STALE", compact=True)
    ok = validity_badge_html("OK", compact=True)
    assert "fx-valid-loud" in stale
    assert WARN in stale and WARN_BG in stale
    assert "fx-valid-quiet" in ok
    assert "fx-valid-loud" not in ok

    legend = scan_legend_html()
    assert "fx-chip buy" in legend and "fx-chip stale" in legend
    assert "STALE outranks" in legend
    strip = scan_strip_html(
        session="LONDON+NY",
        refreshed="2026-09-21 19:42:01",
        tz="Asia/Dhaka",
        counts=counts,
    )
    assert "fx-scan" in strip
    assert "Asia/Dhaka" in strip
    assert "LONDON+NY" in strip
    assert "STALE" in strip
    empty = empty_state_html("Watchlist is empty. Add a pair below.", "Fetch first.")
    assert "fx-empty" in empty
    assert "Watchlist is empty. Add a pair below." in empty
    assert "<script" not in empty_state_html("<script>x</script>", "body")
