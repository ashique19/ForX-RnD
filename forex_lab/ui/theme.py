"""Dark dense terminal theme for the Streamlit desk.

Presentation only. Does not change BrokerPort, paper fills, session windows,
or Asia/Dhaka display clocks. BUY / SELL / HOLD colors are high-contrast scan
aids — research labels, not orders.
"""
from __future__ import annotations

from html import escape

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


def _status_token(val: object) -> str:
    raw = str(val or "").strip()
    if not raw:
        return ""
    token = raw.split("·", 1)[0].strip().split()[0].upper()
    return "FAIL" if token == "ERROR" else token


def validity_cell_style(val: object) -> str:
    v = _status_token(val) or str(val).upper()
    if v == VALIDITY_OK:
        return _cell(BUY_BG, BUY)
    if v == VALIDITY_CLOSED:
        return _cell(NEUTRAL_BG, MUTED)
    if v == VALIDITY_STALE:
        return _cell(WARN_BG, WARN)
    if v == VALIDITY_MISSING:
        return _cell(NEUTRAL_BG, NEUTRAL)
    if v in {VALIDITY_ERROR, "FAIL"}:
        return _cell(SELL_BG, SELL)
    if v == "OFF":
        return _cell(NEUTRAL_BG, MUTED)
    return ""


def awareness_status_style(val: object) -> str:
    """Status cell for the Awareness table (OK / STALE / FAIL · error)."""
    return validity_cell_style(val)


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
  --fx-bright: #f4f7fa;
  --fx-muted: #8b9aab;
  --fx-dim: #5c6b7a;
  --fx-buy: #16c784;
  --fx-sell: #ea3943;
  --fx-hold: #9aa5b1;
  --fx-warn: #f5a524;
  --fx-kicker: 0.625rem;
  --fx-cap: 0.75rem;
  --fx-ui: 0.8125rem;
  --fx-num: 0.875rem;
  --fx-title: 0.9375rem;
  --fx-space: 0.5rem;
}
html, body, .stApp, [data-testid="stAppViewContainer"] {
  background: var(--fx-bg) !important;
  color: var(--fx-text);
}
.stApp {
  font-family: "Segoe UI", "IBM Plex Sans", system-ui, sans-serif;
  font-feature-settings: "tnum" 1, "ss01" 1;
  -webkit-font-smoothing: antialiased;
}
/* Residual Streamlit chrome — keep the sidebar hamburger, hide Deploy / running / footer. */
[data-testid="stHeader"] {
  background: linear-gradient(180deg, rgba(10,14,20,0.96), rgba(10,14,20,0.35)) !important;
  border-bottom: 0 !important;
}
[data-testid="stToolbar"] { min-height: 2rem; right: 0.4rem; }
footer, [data-testid="stFooter"] { visibility: hidden; height: 0; display: none !important; }
#MainMenu { visibility: hidden; }
[data-testid="stDecoration"] { display: none !important; }
[data-testid="stStatusWidget"] { visibility: hidden; height: 0; }
.stDeployButton, [data-testid="stDeployButton"], [data-testid="stAppDeployButton"],
.stAppDeployButton, div[class*="stDeployButton"] { display: none !important; }
[data-testid="stHeaderActionElements"] button[kind="header"] { display: none !important; }
.block-container {
  padding-top: 0.7rem !important;
  padding-bottom: 1.2rem !important;
  padding-left: 1.05rem !important;
  padding-right: 1.05rem !important;
  max-width: 100% !important;
}
[data-testid="stMainBlockContainer"] { padding-top: 0.7rem !important; }
[data-testid="stCaptionContainer"], .stCaption {
  font-size: var(--fx-cap) !important;
  line-height: 1.3 !important;
  color: var(--fx-muted) !important;
}
h1, h2, h3, h4 {
  letter-spacing: 0.06em;
  font-weight: 800 !important;
  line-height: 1.2 !important;
}
.stMarkdown p { margin-bottom: 0.22rem; }
hr { margin: 0.4rem 0 !important; border-color: var(--fx-border) !important; }
[data-testid="stVerticalBlock"] { gap: 0.48rem !important; }
[data-testid="stExpander"] {
  border: 1px solid var(--fx-border) !important;
  border-radius: 4px !important;
  background: var(--fx-surface) !important;
}
[data-testid="stExpander"] details { gap: 0.2rem; }
[data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span {
  font-size: var(--fx-ui) !important;
  font-weight: 700 !important;
}
section[data-testid="stSidebar"] {
  background: var(--fx-surface) !important;
  border-right: 1px solid var(--fx-border);
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2 {
  font-size: 0.92rem !important;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
[data-testid="stMetricValue"] {
  font-size: 1.08rem !important;
  font-variant-numeric: tabular-nums;
}
[data-testid="stMetricLabel"] {
  font-size: var(--fx-kicker) !important;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fx-muted) !important;
}
[data-testid="stMetricDelta"] { font-size: var(--fx-cap) !important; }
div[data-testid="stAlert"] { padding: 0.4rem 0.6rem !important; }
[data-testid="stWidgetLabel"] p,
.stSelectbox label p, .stNumberInput label p, .stTextInput label p, .stCheckbox label p {
  font-size: var(--fx-kicker) !important;
  letter-spacing: 0.07em !important;
  text-transform: uppercase !important;
  color: var(--fx-muted) !important;
  font-weight: 700 !important;
}
[data-testid="stButton"] button {
  min-height: 1.85rem !important;
  padding: 0.14rem 0.5rem !important;
  font-size: var(--fx-cap) !important;
  font-weight: 800 !important;
  letter-spacing: 0.05em !important;
  border-radius: 4px !important;
}
[data-testid="stButton"] button p {
  font-size: var(--fx-cap) !important;
  font-weight: 800 !important;
}
/* Scan-row pair + paper actions share one height so columns line up. */
[data-testid="stHorizontalBlock"]:has([class*="st-key-board_open"]) {
  align-items: center !important;
  gap: 0.35rem !important;
  min-height: 2.2rem;
}
[class*="st-key-board_open"] button {
  background: transparent !important;
  border: 1px solid var(--fx-border) !important;
  color: var(--fx-text) !important;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
  letter-spacing: 0.06em !important;
  min-height: 2rem !important;
}
[class*="st-key-board_open"] button[kind="primary"] {
  border-color: var(--fx-buy) !important;
  color: var(--fx-buy) !important;
  box-shadow: inset 3px 0 0 var(--fx-buy);
  background: #0f3d2a !important;
}
[class*="st-key-paper_buy"] button {
  background: var(--fx-buy) !important;
  color: #04140c !important;
  border: 0 !important;
  min-height: 2rem !important;
}
[class*="st-key-paper_sell"] button {
  background: var(--fx-sell) !important;
  color: #fff !important;
  border: 0 !important;
  min-height: 2rem !important;
}
[class*="st-key-paper_close"] button {
  background: #3a424d !important;
  color: #e6edf3 !important;
  border: 0 !important;
  min-height: 2rem !important;
}
[class*="st-key-paper_buy"] button:disabled,
[class*="st-key-paper_sell"] button:disabled {
  opacity: 0.34 !important;
  filter: grayscale(0.4);
  cursor: not-allowed !important;
  box-shadow: inset 0 0 0 1px var(--fx-warn) !important;
}
/* Workspace + watchlist: labelled inputs sit above buttons — lift the actions. */
[class*="st-key-ws_apply"],
[class*="st-key-ws_reset"],
[class*="st-key-ws_save"],
[class*="st-key-watch_add_btn"],
[class*="st-key-watch_remove_btn"] {
  margin-top: 1.72rem !important;
}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  font-size: var(--fx-ui) !important;
}
[data-testid="stTabs"] button {
  font-size: var(--fx-ui) !important;
  font-weight: 700 !important;
  letter-spacing: 0.04em;
}
.fx-masthead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 2px 0 10px 1.75rem;
  border-bottom: 1px solid var(--fx-border);
  margin-bottom: 6px;
  min-height: 1.7rem;
}
.fx-masthead-left, .fx-masthead-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.fx-brand {
  font-weight: 900;
  letter-spacing: 0.16em;
  font-size: 0.78rem;
  color: var(--fx-buy);
}
.fx-title {
  font-weight: 800;
  letter-spacing: 0.14em;
  font-size: var(--fx-title);
  color: var(--fx-bright);
}
.fx-chip {
  font-size: var(--fx-kicker);
  font-weight: 800;
  letter-spacing: 0.08em;
  padding: 3px 7px;
  border-radius: 3px;
  background: #0f3d2a;
  color: var(--fx-buy);
  line-height: 1.2;
}
.fx-chip.muted { background: var(--fx-elev); color: var(--fx-muted); }
.fx-chip.buy { background: var(--fx-buy); color: #04140c; }
.fx-chip.sell { background: var(--fx-sell); color: #fff; }
.fx-chip.hold {
  background: transparent;
  color: var(--fx-hold);
  box-shadow: inset 0 0 0 1px var(--fx-hold);
}
.fx-chip.stale { background: var(--fx-warn); color: #1a1204; }
.fx-clock {
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  font-size: 1rem;
  letter-spacing: 0.02em;
  color: var(--fx-bright);
}
.fx-tz {
  font-size: var(--fx-kicker);
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--fx-muted);
  text-transform: uppercase;
}
.fx-status {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  font-size: var(--fx-cap);
  font-weight: 650;
  color: var(--fx-muted);
  padding: 2px 0 2px;
  font-variant-numeric: tabular-nums;
}
.fx-status b { color: var(--fx-text); font-weight: 800; }
.fx-status .ok { color: var(--fx-buy); }
.fx-status .warn { color: var(--fx-warn); }
.fx-status .bad { color: var(--fx-sell); }
.fx-awareness-kicker, .fx-section {
  font-size: var(--fx-kicker);
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--fx-muted);
}
.fx-section { margin: 2px 0 4px; color: var(--fx-text); letter-spacing: 0.12em; }
.fx-awareness-status { padding: 2px 0 2px; }
.fx-legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  padding: 2px 0 8px;
}
.fx-legend-note {
  font-size: var(--fx-kicker);
  color: var(--fx-muted);
  letter-spacing: 0.04em;
  font-weight: 650;
}
.fx-row-accent {
  height: 3px;
  border-radius: 2px;
  margin: 4px 0 2px;
}
.fx-row-accent.fx-row-buy { background: var(--fx-buy); }
.fx-row-accent.fx-row-sell { background: var(--fx-sell); }
.fx-row-accent.fx-row-hold { background: var(--fx-hold); opacity: 0.4; }
.fx-row-accent.fx-row-stale { background: var(--fx-warn); height: 4px; }
.fx-row-accent.fx-row-dead { background: var(--fx-sell); opacity: 0.75; }
.fx-row-accent.fx-row-sel { box-shadow: 0 0 0 1px var(--fx-buy); }
.fx-row-accent.fx-row-warn { outline: 1px dashed var(--fx-warn); outline-offset: 1px; }
.fx-badge {
  font-weight: 800;
  text-align: center;
  letter-spacing: 0.08em;
  border-radius: 3px;
  line-height: 1.15;
  font-variant-numeric: tabular-nums;
}
.fx-badge-signal { box-shadow: 0 0 0 1px rgba(0,0,0,0.35); }
.fx-badge-hold {
  background: transparent !important;
  box-shadow: inset 0 0 0 1px var(--fx-hold);
  color: var(--fx-hold) !important;
}
.fx-badge-stale {
  background: #3a2a0c !important;
  color: var(--fx-warn) !important;
  box-shadow: inset 0 0 0 1px var(--fx-warn);
}
.fx-validity {
  text-align: center;
  line-height: 1.05;
}
.fx-validity .dot { font-size: 0.9rem; }
.fx-validity .lab {
  font-size: var(--fx-kicker);
  font-weight: 800;
  letter-spacing: 0.05em;
}
.fx-validity.stale .lab, .fx-validity.fail .lab { letter-spacing: 0.08em; }
.fx-empty {
  border: 1px dashed var(--fx-border);
  background: var(--fx-surface);
  padding: 14px 16px;
  border-radius: 6px;
  text-align: left;
}
.fx-empty-title {
  font-size: var(--fx-ui);
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--fx-text);
}
.fx-empty-detail {
  font-size: var(--fx-cap);
  color: var(--fx-muted);
  margin-top: 4px;
  line-height: 1.35;
}
.fx-blocked {
  font-size: var(--fx-kicker);
  font-weight: 800;
  letter-spacing: 0.04em;
  color: var(--fx-warn);
  line-height: 1.25;
  margin-top: 3px;
}
.fx-drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
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


def _staleish(validity: object) -> bool:
    v = str(validity or "").upper()
    return v in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}


def scan_row_tone(
    signal: object,
    validity: object,
    *,
    selected: bool = False,
    event_warn: bool = False,
) -> str:
    """CSS token for the scan-row accent: buy / sell / hold / stale / dead."""
    v = str(validity or "").upper()
    if v == VALIDITY_STALE:
        base = "stale"
    elif v in {VALIDITY_MISSING, VALIDITY_ERROR}:
        base = "dead"
    else:
        s = str(signal or "").upper()
        if s == "BUY":
            base = "buy"
        elif s == "SELL":
            base = "sell"
        else:
            base = "hold"
    extra = []
    if selected:
        extra.append("sel")
    if event_warn and base not in {"stale", "dead"}:
        extra.append("warn")
    return " ".join([base, *extra])


def row_accent_html(tone: str) -> str:
    classes = " ".join(f"fx-row-{t}" for t in str(tone or "hold").split() if t)
    return f'<div class="fx-row-accent {classes}" aria-hidden="true"></div>'


def empty_state_html(title: str, detail: str = "") -> str:
    body = f'<div class="fx-empty-detail">{escape(detail)}</div>' if detail else ""
    return (
        f'<div class="fx-empty">'
        f'<div class="fx-empty-title">{escape(title)}</div>'
        f"{body}</div>"
    )


def section_html(label: str) -> str:
    return f'<div class="fx-section">{escape(label)}</div>'


def masthead_html(clock: str, tz: str) -> str:
    return (
        '<div class="fx-masthead">'
        '<div class="fx-masthead-left">'
        '<span class="fx-brand">FX</span>'
        '<span class="fx-title">SIGNAL SCREEN</span>'
        '<span class="fx-chip">PAPER</span>'
        '<span class="fx-chip muted">RESEARCH</span>'
        "</div>"
        '<div class="fx-masthead-right">'
        f'<span class="fx-clock">{escape(clock)}</span>'
        f'<span class="fx-tz">{escape(tz)}</span>'
        "</div></div>"
    )


def scan_legend_html() -> str:
    return (
        '<div class="fx-legend">'
        '<span class="fx-chip buy">BUY</span>'
        '<span class="fx-chip sell">SELL</span>'
        '<span class="fx-chip hold">HOLD</span>'
        '<span class="fx-chip stale">STALE</span>'
        '<span class="fx-legend-note">'
        "Filled calls · outline HOLD · amber STALE · paper BUY/SELL off when STALE/MISSING"
        "</span></div>"
    )


def status_strip_html(parts: list[tuple[str, str, str]]) -> str:
    bits: list[str] = []
    for kicker, value, tone in parts:
        cls = f' class="{escape(tone)}"' if tone else ""
        bits.append(
            f'<span><span class="fx-awareness-kicker">{escape(kicker)}</span> '
            f"<b{cls}>{escape(value)}</b></span>"
        )
    return f'<div class="fx-status">{"".join(bits)}</div>'


def signal_badge_html(
    sig: object,
    *,
    weak: bool = False,
    compact: bool = False,
    validity: object = None,
) -> str:
    raw = str(sig).upper() if sig and str(sig).strip() not in {"—", "-", "n/a"} else "—"
    stale = _staleish(validity) or raw == "—"
    label = raw if not weak or raw in {"—", "HOLD"} else f"{raw} (weak)"
    if compact:
        size, pad = "0.78rem", "4px 5px"
    else:
        size, pad = ("1.05rem" if weak and raw in {"BUY", "SELL"} else "1.28rem"), "8px 10px"
    extra = "fx-badge-signal"
    if stale:
        bg, fg = WARN_BG, WARN
        extra += " fx-badge-stale"
        if raw not in {"—", "HOLD"}:
            label = f"{raw} · STALE" if not compact else "—"
        else:
            label = "—"
    elif raw == "HOLD":
        bg, fg = "transparent", HOLD
        extra += " fx-badge-hold"
    else:
        bg, fg = signal_fill(raw)
    op = 0.7 if weak and not stale else 1
    return (
        f'<div class="fx-badge {extra}" style="background:{bg};color:{fg};font-size:{size};'
        f"padding:{pad};opacity:{op}\">{escape(label)}</div>"
    )


def validity_badge_html(validity: object, *, compact: bool = False) -> str:
    v = str(validity or "MISSING").upper()
    tone = validity_tone(v)
    kind = "ok"
    if v == VALIDITY_STALE:
        kind = "stale"
    elif v in {VALIDITY_ERROR, "FAIL"}:
        kind = "fail"
    elif v in {VALIDITY_MISSING, VALIDITY_CLOSED, "OFF"}:
        kind = "muted"
    if compact:
        return (
            f'<div class="fx-validity {kind}" title="{escape(v)}">'
            f'<span class="dot" style="color:{tone}">●</span>'
            f'<div class="lab" style="color:{tone}">{escape(v)}</div></div>'
        )
    if v == VALIDITY_OK:
        fg = BUY_FG
    elif v == VALIDITY_STALE:
        fg = WARN_FG
    elif v in {VALIDITY_ERROR, "FAIL"}:
        fg = SELL_FG
    else:
        fg = MUTED
    return (
        f'<div class="fx-badge" style="background:{tone};color:{fg};font-size:0.72rem;'
        f'padding:4px 8px;display:inline-block;letter-spacing:0.08em">{escape(v)}</div>'
    )


def session_badge_html(name: object) -> str:
    label = str(name or "n/a")
    bg = session_fill(label)
    return (
        f'<div class="fx-badge" style="background:{bg};color:#fff;font-size:0.68rem;'
        f'padding:3px 6px;display:inline-block;letter-spacing:0.08em">{escape(label)}</div>'
    )


def mtf_badge_html(status: object, note: str, *, compact: bool = False) -> str:
    s = str(status or "n/a")
    colors = {MTF_AGREE: BUY, MTF_CONFLICT: WARN, "n/a": HOLD_BG}
    fg = {MTF_AGREE: BUY_FG, MTF_CONFLICT: WARN_FG, "n/a": TEXT}
    bg = colors.get(s, HOLD_BG)
    color = fg.get(s, TEXT)
    label = note if note else s
    return (
        f'<div class="fx-badge" style="background:{bg};color:{color};font-size:0.66rem;'
        f'padding:3px 5px;display:block;letter-spacing:0.04em">{escape(label)}</div>'
    )


def blocked_caption_html(reason: str) -> str:
    return f'<div class="fx-blocked">{escape(reason)}</div>'
