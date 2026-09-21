"""Dark dense terminal theme for the Streamlit desk.

Presentation only. Does not change BrokerPort, paper fills, session windows,
or Asia/Dhaka display clocks. BUY / SELL / HOLD colors are high-contrast scan
aids — research labels, not orders.
"""
from __future__ import annotations

from forex_lab.freshness import (
    VALIDITY_CLOSED,
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
)
from forex_lab.mtf import MTF_AGREE, MTF_CONFLICT

# High-contrast terminal palette (also mirrored as CSS variables).
BG = "#0a0e14"
SURFACE = "#121820"
ELEVATED = "#171f29"
BORDER = "#243040"
TEXT = "#e6edf3"
TEXT_BRIGHT = "#f4f7fa"
MUTED = "#8b9aab"
DIM = "#5c6b7a"

BUY = "#16c784"
BUY_FG = "#04140c"
BUY_BG = "#0f3d2a"
SELL = "#ea3943"
SELL_FG = "#ffffff"
SELL_BG = "#3d1216"
HOLD = "#9aa5b1"
HOLD_FG = "#d5dce3"
HOLD_BG = "#2a313c"
NEUTRAL = "#6b7785"
NEUTRAL_BG = "#1a222c"

WARN = "#f5a524"
WARN_FG = "#1a1204"
WARN_BG = "#3a2a0c"
ERROR = "#ea3943"
INFO = "#3b82f6"
EVENT = "#a78bfa"
OK = BUY

SIGNAL_FILL = {
    "BUY": (BUY, BUY_FG),
    "SELL": (SELL, SELL_FG),
    "HOLD": (HOLD_BG, HOLD_FG),
    "—": (NEUTRAL_BG, NEUTRAL),
}

VALIDITY_TONE = {
    VALIDITY_OK: OK,
    VALIDITY_CLOSED: MUTED,
    VALIDITY_STALE: WARN,
    VALIDITY_MISSING: NEUTRAL,
    VALIDITY_ERROR: ERROR,
}

SESSION_FILL = {
    "ASIA": "#3730a3",
    "LONDON": "#1d4ed8",
    "NY": "#0f766e",
    "ASIA+LONDON": "#1e3a8a",
    "LONDON+NY": "#b45309",
    "ASIA+NY": "#6d28d9",
    "ASIA+LONDON+NY": "#b45309",
    "CLOSED": "#3a4450",
    "OFF": "#2a313c",
    "N/A": "#2a313c",
}

ALERT_TONE = {
    "flip": INFO,
    "stale": WARN,
    "missing": NEUTRAL,
    "event": EVENT,
}


def signal_fill(sig: object) -> tuple[str, str]:
    s = str(sig).upper() if sig and str(sig).strip() not in {"—", "-", "n/a"} else "—"
    return SIGNAL_FILL.get(s, SIGNAL_FILL["—"])


def validity_tone(validity: object) -> str:
    v = str(validity or "MISSING").upper()
    return VALIDITY_TONE.get(v, NEUTRAL)


def session_fill(name: object) -> str:
    return SESSION_FILL.get(str(name or "N/A").upper(), "#334155")


def alert_tone(kind: object) -> str:
    return ALERT_TONE.get(str(kind or ""), "#334155")


def _cell(bg: str, fg: str, *, weight: int = 700) -> str:
    return f"background-color: {bg}; color: {fg}; font-weight: {weight}"


def signal_cell_style(val: object) -> str:
    v = str(val).upper()
    if v == "BUY":
        return _cell(BUY_BG, BUY)
    if v == "SELL":
        return _cell(SELL_BG, SELL)
    if v == "HOLD":
        return _cell(HOLD_BG, HOLD_FG)
    return ""


def validity_cell_style(val: object) -> str:
    v = str(val).upper()
    if v == VALIDITY_OK:
        return _cell(BUY_BG, BUY)
    if v == VALIDITY_CLOSED:
        return _cell(NEUTRAL_BG, MUTED)
    if v == VALIDITY_STALE:
        return _cell(WARN_BG, WARN)
    if v == VALIDITY_MISSING:
        return _cell(NEUTRAL_BG, NEUTRAL)
    if v == VALIDITY_ERROR:
        return _cell(SELL_BG, SELL)
    return ""


def mtf_cell_style(val: object) -> str:
    v = str(val).lower()
    if v == MTF_AGREE:
        return _cell(BUY_BG, BUY)
    if v == MTF_CONFLICT:
        return _cell(WARN_BG, WARN)
    return ""


def session_cell_style(val: object) -> str:
    v = str(val).upper()
    if v in {"CLOSED", "OFF", "N/A"}:
        return _cell(NEUTRAL_BG, MUTED)
    if "+" in v:
        return _cell(WARN_BG, WARN)
    bg = SESSION_FILL.get(v)
    if bg:
        return _cell(bg, "#ffffff")
    return _cell(NEUTRAL_BG, MUTED)


def event_cell_style(val: object) -> str:
    if str(val).startswith("⚠"):
        return _cell(WARN_BG, WARN)
    return ""


def action_cell_style(val: object) -> str:
    v = str(val).lower()
    if v.startswith("disabled"):
        return _cell(NEUTRAL_BG, MUTED, weight=600)
    if v == "buy/sell":
        return "color: #e6edf3; font-weight: 700"
    if v == "close":
        return _cell(HOLD_BG, HOLD_FG)
    return ""


def newest_row_style(n_cols: int) -> list[str]:
    return [f"background-color: {ELEVATED}; font-weight: 600; color: {TEXT}"] * n_cols


TERMINAL_CSS = """
:root {
  --fx-bg: #0a0e14;
  --fx-surface: #121820;
  --fx-elev: #171f29;
  --fx-border: #243040;
  --fx-text: #e6edf3;
  --fx-muted: #8b9aab;
  --fx-buy: #16c784;
  --fx-sell: #ea3943;
  --fx-hold: #9aa5b1;
  --fx-warn: #f5a524;
}
html, body, .stApp, [data-testid="stAppViewContainer"] {
  background: var(--fx-bg) !important;
  color: var(--fx-text);
}
.stApp {
  font-feature-settings: "tnum" 1;
}
[data-testid="stHeader"] {
  background: rgba(10,14,20,0.92) !important;
  border-bottom: 1px solid var(--fx-border);
}
[data-testid="stToolbar"] { min-height: 2rem; }
footer { visibility: hidden; height: 0; }
#MainMenu { visibility: hidden; }
[data-testid="stDecoration"] { display: none !important; }
.block-container {
  padding-top: 1.05rem !important;
  padding-bottom: 1.4rem !important;
  padding-left: 1.1rem !important;
  padding-right: 1.1rem !important;
  max-width: 100% !important;
}
[data-testid="stMainBlockContainer"] {
  padding-top: 1.05rem !important;
}
div[data-testid="stVerticalBlock"] {
  gap: 0.32rem !important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
  gap: 0.45rem !important;
}
[data-testid="stCaptionContainer"], .stCaption {
  font-size: 0.72rem !important;
  line-height: 1.28 !important;
  color: var(--fx-muted) !important;
}
h1, h2, h3 {
  letter-spacing: 0.04em;
  font-weight: 800 !important;
}
.stMarkdown p { margin-bottom: 0.25rem; }
hr { margin: 0.35rem 0 !important; border-color: var(--fx-border) !important; }
[data-testid="stExpander"] {
  border: 1px solid var(--fx-border) !important;
  border-radius: 4px !important;
  background: var(--fx-surface) !important;
}
[data-testid="stExpander"] details { gap: 0.2rem; }
[data-testid="stMetricValue"] {
  font-size: 1.12rem !important;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricLabel"] {
  font-size: 0.68rem !important;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--fx-muted) !important;
}
[data-testid="stMetricDelta"] { font-size: 0.72rem !important; }
div[data-testid="stAlert"] {
  padding: 0.45rem 0.65rem !important;
}
[data-testid="stButton"] button {
  min-height: 1.7rem !important;
  padding: 0.12rem 0.45rem !important;
  font-size: 0.74rem !important;
  font-weight: 800 !important;
  letter-spacing: 0.05em !important;
  border-radius: 4px !important;
}
[data-testid="stButton"] button p {
  font-size: 0.74rem !important;
  font-weight: 800 !important;
}
[class*="st-key-board_open"] button {
  background: transparent !important;
  border: 1px solid var(--fx-border) !important;
  color: var(--fx-text) !important;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
  letter-spacing: 0.06em !important;
}
[class*="st-key-board_open"] button[kind="primary"] {
  border-color: var(--fx-buy) !important;
  color: var(--fx-buy) !important;
  box-shadow: inset 2px 0 0 var(--fx-buy);
}
[class*="st-key-paper_buy"] button {
  background: var(--fx-buy) !important;
  color: #04140c !important;
  border: 0 !important;
}
[class*="st-key-paper_sell"] button {
  background: var(--fx-sell) !important;
  color: #fff !important;
  border: 0 !important;
}
[class*="st-key-paper_close"] button {
  background: #3a424d !important;
  color: #e6edf3 !important;
  border: 0 !important;
}
[class*="st-key-paper_buy"] button:disabled,
[class*="st-key-paper_sell"] button:disabled {
  opacity: 0.38 !important;
  filter: grayscale(0.25);
}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  font-size: 0.78rem !important;
}
[data-testid="stTabs"] button {
  font-size: 0.78rem !important;
  font-weight: 700 !important;
}
.fx-masthead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 0 8px;
  border-bottom: 1px solid var(--fx-border);
  margin-bottom: 4px;
}
.fx-masthead-left, .fx-masthead-right {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.fx-brand {
  font-weight: 900;
  letter-spacing: 0.14em;
  font-size: 0.78rem;
  color: var(--fx-buy);
}
.fx-title {
  font-weight: 800;
  letter-spacing: 0.12em;
  font-size: 0.92rem;
  color: var(--fx-text);
}
.fx-chip {
  font-size: 0.62rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  padding: 2px 6px;
  border-radius: 3px;
  background: #0f3d2a;
  color: var(--fx-buy);
}
.fx-chip.muted {
  background: var(--fx-elev);
  color: var(--fx-muted);
}
.fx-clock {
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  font-size: 0.92rem;
  letter-spacing: 0.02em;
}
.fx-tz {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--fx-muted);
  text-transform: uppercase;
}
.fx-status {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  font-size: 0.72rem;
  font-weight: 650;
  color: var(--fx-muted);
  padding: 2px 0 4px;
  font-variant-numeric: tabular-nums;
}
.fx-status b { color: var(--fx-text); font-weight: 800; }
.fx-status .ok { color: var(--fx-buy); }
.fx-status .warn { color: var(--fx-warn); }
.fx-status .bad { color: var(--fx-sell); }
"""


def terminal_css() -> str:
    return TERMINAL_CSS


def inject_terminal_css() -> None:
    """Apply dense dark CSS. Safe no-op when Streamlit is not importing the app."""
    import streamlit as st

    st.markdown(
        f'<style data-fx-theme="terminal">{TERMINAL_CSS}</style>',
        unsafe_allow_html=True,
    )
