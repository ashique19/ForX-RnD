"""Dark dense terminal theme for the Streamlit desk.

Presentation only. Does not change BrokerPort, paper fills, session windows,
or Asia/Dhaka display clocks. BUY / SELL / HOLD colors are high-contrast scan
aids — research labels, not orders. STALE / MISSING outrank the flash so a
stale row never reads as a live call.
"""
from __future__ import annotations

from html import escape as _esc

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
TEXT = "#f2f5f8"
TEXT_BRIGHT = "#f8fafc"
# Secondary copy: light grey on #0a0e14 (never dark-grey-on-dark).
MUTED = "#c8d0db"
DIM = "#b3becb"
# Readability first. px so Streamlit cannot shrink captions back to ~11px.
FONT_ROOT_PX = 16
FONT_BODY = "16px"
FONT_CAPTION = "14px"
FONT_LABEL = "14px"
FONT_CHIP = "13px"
FONT_CELL = "15px"
FONT_EXPANDER = "16px"
FONT_VALID_COMPACT = "13px"
FONT_VALID_LOUD = "14px"
FONT_SIG_COMPACT = "16px"
FONT_CLOCK = "22px"
FONT_TITLE = "22px"

BUY = "#16c784"
BUY_FG = "#04140c"
BUY_BG = "#0f3d2a"
SELL = "#ea3943"
SELL_FG = "#ffffff"
SELL_BG = "#3d1216"
HOLD = "#c5ced8"
HOLD_FG = "#e8edf3"
HOLD_BG = "#2a313c"
NEUTRAL = "#c0c9d4"
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


DATA_BLOCKS = frozenset({VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR})


def normalize_signal(sig: object) -> str:
    raw = str(sig or "").strip().upper()
    if raw in {"", "—", "-", "N/A", "NA"}:
        return "—"
    return raw


def scan_emphasis(signal: object, validity: object) -> str:
    """What to read first. Unusable data outranks BUY / SELL / HOLD."""
    raw_v = str(validity or "").strip().upper()
    v = raw_v.split()[0] if raw_v else ""
    if v in DATA_BLOCKS:
        return v
    s = normalize_signal(signal)
    if s in {"BUY", "SELL", "HOLD", "—"}:
        return s
    return s or "—"


def scan_counts(rows) -> dict[str, int]:
    """BUY/SELL/HOLD plus validity tallies for the masthead scan strip.

    Unusable data (STALE / MISSING / ERROR) outranks the flash: those rows
    increment the validity chip only, never BUY/SELL/HOLD, even if a leftover
    class is still on the row.
    """
    counts = {
        "BUY": 0,
        "SELL": 0,
        "HOLD": 0,
        "—": 0,
        VALIDITY_OK: 0,
        VALIDITY_CLOSED: 0,
        VALIDITY_STALE: 0,
        VALIDITY_MISSING: 0,
        VALIDITY_ERROR: 0,
    }
    for row in rows or []:
        if isinstance(row, dict):
            s = row.get("buy_sell") or row.get("Signal")
            v = row.get("validity") or row.get("Data●")
        else:
            s = getattr(row, "buy_sell", None)
            v = getattr(row, "validity", None)
        vn = str(v or "").strip().upper()
        token = vn.split()[0] if vn else ""
        if token in {"FAIL", "ERROR"}:
            token = VALIDITY_ERROR
        if token in DATA_BLOCKS:
            counts[token] += 1
            continue
        sn = normalize_signal(s)
        if sn in counts:
            counts[sn] += 1
        if token in counts:
            counts[token] += 1
    return counts


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
        return f"color: {TEXT}; font-weight: 700"
    if v == "close":
        return _cell(HOLD_BG, HOLD_FG)
    return ""


def newest_row_style(n_cols: int) -> list[str]:
    return [f"background-color: {ELEVATED}; font-weight: 600; color: {TEXT}"] * n_cols


def empty_state_html(title: str, body: str, *, kicker: str = "EMPTY") -> str:
    return (
        f'<div class="fx-empty">'
        f'<div class="fx-empty-kicker">{_esc(kicker)}</div>'
        f'<div class="fx-empty-title">{_esc(title)}</div>'
        f'<div class="fx-empty-body">{_esc(body)}</div>'
        f"</div>"
    )


def empty_inline_html(text: str) -> str:
    return f'<div class="fx-empty-inline">{_esc(text)}</div>'


def scan_legend_html() -> str:
    return (
        '<div class="fx-legend">'
        '<span class="fx-chip buy">BUY</span>'
        '<span class="fx-chip sell">SELL</span>'
        '<span class="fx-chip hold">HOLD</span>'
        '<span class="fx-chip stale">STALE</span>'
        '<span class="fx-legend-note">STALE outranks the flash — refresh (Fetch) first. '
        "Research labels, not orders.</span>"
        "</div>"
    )


def scan_strip_html(
    *,
    session: str = "",
    refreshed: str = "",
    tz: str = "Asia/Dhaka",
    counts: dict[str, int] | None = None,
) -> str:
    counts = counts or {}
    n_stale = int(counts.get(VALIDITY_STALE, 0))
    n_missing = int(counts.get(VALIDITY_MISSING, 0)) + int(counts.get(VALIDITY_ERROR, 0))

    def _count(label: str, n: int, cls: str) -> str:
        on = " on" if int(n) else ""
        return (
            f'<span class="fx-count {cls}{on}">{_esc(label)} '
            f"<b>{int(n)}</b></span>"
        )

    chips = "".join(
        [
            _count("BUY", counts.get("BUY", 0), "buy"),
            _count("SELL", counts.get("SELL", 0), "sell"),
            _count("HOLD", counts.get("HOLD", 0), "hold"),
            _count("STALE", n_stale, "stale"),
        ]
    )
    if n_missing:
        chips += _count("MISSING", n_missing, "missing")
    return (
        f'<div class="fx-scan">'
        f'<div class="fx-scan-meta">'
        f'<span class="fx-scan-kicker">Board</span>'
        f'<span class="fx-scan-sess">{_esc(session or "n/a")}</span>'
        f'<span class="fx-clock-mini">{_esc(refreshed or "—")}</span>'
        f'<span class="fx-tz">{_esc(tz or "Asia/Dhaka")}</span>'
        f"</div>"
        f'<div class="fx-scan-counts">{chips}</div>'
        f"</div>"
    )


def signal_badge_html(sig: object, *, weak: bool = False, compact: bool = False) -> str:
    s = normalize_signal(sig)
    bg, fg = signal_fill(s)
    label = s if not weak or s in {"—", "HOLD"} else f"{s} (weak)"
    kind = "dash" if s == "—" else s.lower()
    classes = ["fx-sig", f"fx-sig-{kind}"]
    if weak:
        classes.append("fx-sig-weak")
    if compact:
        classes.append("fx-sig-compact")
    if s == "HOLD":
        classes.append("fx-sig-quiet")
    if s == "—":
        classes.append("fx-sig-muted")
    size = FONT_SIG_COMPACT if compact else ("18px" if weak and s in {"BUY", "SELL"} else "22px")
    pad = "6px 8px" if compact else "10px 12px"
    opacity = "0.72" if weak else "1"
    return (
        f'<div class="{" ".join(classes)}" style="background:{bg};color:{fg};font-weight:800;'
        f"font-size:{size};text-align:center;padding:{pad};border-radius:3px;"
        f"letter-spacing:0.1em;box-shadow:0 0 0 1px rgba(0,0,0,0.35);opacity:{opacity}\">"
        f"{_esc(label)}</div>"
    )


def validity_badge_html(validity: object, *, compact: bool = False) -> str:
    raw = str(validity or "MISSING").strip()
    v = raw.split()[0].upper() if raw else "MISSING"
    tone = validity_tone(v)
    blocked = v in DATA_BLOCKS
    if compact and blocked:
        if v == VALIDITY_STALE:
            bg, fg = WARN_BG, WARN
        elif v == VALIDITY_MISSING:
            bg, fg = NEUTRAL_BG, NEUTRAL
        else:
            bg, fg = SELL_BG, SELL
        return (
            f'<div class="fx-valid fx-valid-loud fx-valid-{v.lower()}" title="{_esc(v)}" '
            f'style="background:{bg};color:{fg};font-weight:800;font-size:{FONT_VALID_LOUD};'
            f'letter-spacing:0.08em;text-align:center;padding:5px 7px;border-radius:3px">'
            f"{_esc(v)}</div>"
        )
    if compact:
        return (
            f'<div class="fx-valid fx-valid-quiet fx-valid-{v.lower()}" title="{_esc(v)}" '
            f'style="text-align:center;line-height:1.05">'
            f'<span style="color:{tone};font-size:1.05rem">●</span>'
            f'<div style="font-size:{FONT_VALID_COMPACT};font-weight:800;letter-spacing:0.05em;color:{tone}">'
            f"{_esc(v)}</div></div>"
        )
    fg = WARN_FG if v in {VALIDITY_OK, VALIDITY_STALE} else "#fff"
    return (
        f'<div class="fx-valid" style="background:{tone};color:{fg};font-weight:800;'
        f"font-size:14px;letter-spacing:0.08em;text-align:center;padding:5px 10px;"
        f'border-radius:3px;display:inline-block">{_esc(v)}</div>'
    )


TERMINAL_CSS = """
:root {
  --fx-bg: #0a0e14;
  --fx-surface: #121820;
  --fx-elev: #171f29;
  --fx-border: #243040;
  --fx-text: #f2f5f8;
  --fx-text-bright: #f8fafc;
  --fx-muted: #c8d0db;
  --fx-dim: #b3becb;
  --fx-buy: #16c784;
  --fx-sell: #ea3943;
  --fx-hold: #c5ced8;
  --fx-warn: #f5a524;
  --fx-row-h: 2.75rem;
  --fx-font-body: 16px;
  --fx-font-caption: 14px;
  --fx-font-label: 14px;
  --fx-font-chip: 13px;
  --fx-font-cell: 15px;
  --fx-font-expander: 16px;
  --fx-font-clock: 22px;
  --fx-font-title: 22px;
}
html {
  font-size: 16px !important;
}
html, body, .stApp, [data-testid="stAppViewContainer"] {
  background: var(--fx-bg) !important;
  color: var(--fx-text) !important;
}
body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  font-size: 16px !important;
  color: var(--fx-text) !important;
}
.stApp {
  font-feature-settings: "tnum" 1, "ss01" 1;
  font-variant-numeric: tabular-nums;
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
  padding-bottom: 1.6rem !important;
  padding-left: 1.2rem !important;
  padding-right: 1.2rem !important;
  max-width: 100% !important;
}
[data-testid="stMainBlockContainer"] {
  padding-top: 1.05rem !important;
}
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stText"],
.stMarkdown p {
  font-size: 16px !important;
  color: var(--fx-text) !important;
  line-height: 1.45 !important;
}
[data-testid="stMarkdownContainer"] p strong,
[data-testid="stMarkdownContainer"] strong {
  color: var(--fx-text-bright) !important;
  font-weight: 800 !important;
}
small, .stMarkdown small {
  font-size: 14px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stCaptionContainer"], .stCaption,
[data-testid="stCaptionContainer"] p, .stCaption p,
[data-testid="stCaptionContainer"] * {
  font-size: 14px !important;
  line-height: 1.45 !important;
  color: var(--fx-muted) !important;
}
h1, [data-testid="stHeading"] h1 {
  font-size: 1.5rem !important;
  letter-spacing: 0.03em;
  font-weight: 800 !important;
  color: var(--fx-text-bright) !important;
}
h2, [data-testid="stHeading"] h2 {
  font-size: 1.28rem !important;
  letter-spacing: 0.03em;
  font-weight: 800 !important;
  color: var(--fx-text-bright) !important;
}
h3, [data-testid="stHeading"] h3, h4, h5, h6 {
  font-size: 1.12rem !important;
  letter-spacing: 0.03em;
  font-weight: 800 !important;
  color: var(--fx-text) !important;
}
.stMarkdown p { margin-bottom: 0.35rem; }
hr { margin: 0.5rem 0 !important; border-color: var(--fx-border) !important; }
[data-testid="stExpander"] {
  border: 1px solid var(--fx-border) !important;
  border-radius: 4px !important;
  background: var(--fx-surface) !important;
  margin-bottom: 0.45rem !important;
}
[data-testid="stExpander"] details { gap: 0.35rem; }
[data-testid="stExpander"] summary {
  padding: 0.45rem 0.55rem !important;
}
[data-testid="stExpander"] summary p {
  font-size: 16px !important;
  font-weight: 700 !important;
  letter-spacing: 0.02em;
  color: var(--fx-text) !important;
}
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] p,
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] li {
  font-size: 16px !important;
  color: var(--fx-text) !important;
}
[data-testid="stExpander"] [data-testid="stCaptionContainer"],
[data-testid="stExpander"] [data-testid="stCaptionContainer"] p,
[data-testid="stExpander"] [data-testid="stCaptionContainer"] *,
[data-testid="stExpander"] .stCaption,
[data-testid="stExpander"] .stCaption p {
  font-size: 14px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stMetricValue"] {
  font-size: 1.35rem !important;
  font-variant-numeric: tabular-nums;
  color: var(--fx-text-bright) !important;
}
[data-testid="stMetricLabel"] {
  font-size: 14px !important;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--fx-muted) !important;
  font-weight: 800 !important;
}
[data-testid="stMetricDelta"] { font-size: 14px !important; color: var(--fx-muted) !important; }
div[data-testid="stAlert"] {
  padding: 0.55rem 0.75rem !important;
  font-size: 14px !important;
}
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label,
[data-testid="stWidgetLabel"] {
  font-size: 14px !important;
  letter-spacing: 0.05em !important;
  text-transform: uppercase !important;
  color: var(--fx-muted) !important;
  font-weight: 800 !important;
}
[data-testid="stSidebar"] {
  background: var(--fx-surface) !important;
  color: var(--fx-text) !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
  color: var(--fx-text) !important;
  font-size: 16px !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] .stCaption p {
  font-size: 14px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 14px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stTooltipContent"],
[role="tooltip"],
div[data-baseweb="tooltip"] {
  font-size: 14px !important;
  line-height: 1.45 !important;
  color: var(--fx-text) !important;
  background: var(--fx-elev) !important;
}
div[data-testid="stHorizontalBlock"] {
  align-items: center !important;
  gap: 0.42rem !important;
}
div[data-testid="stHorizontalBlock"]:has([data-testid="stWidgetLabel"]):has([data-testid="stButton"]) {
  align-items: flex-end !important;
}
[data-testid="column"] {
  padding-left: 0.28rem !important;
  padding-right: 0.28rem !important;
}
[data-testid="stButton"] button {
  min-height: 2.15rem !important;
  padding: 0.28rem 0.7rem !important;
  font-size: 15px !important;
  font-weight: 800 !important;
  letter-spacing: 0.04em !important;
  border-radius: 4px !important;
  line-height: 1.1 !important;
}
[data-testid="stButton"] button p {
  font-size: 15px !important;
  font-weight: 800 !important;
}
[class*="st-key-board_open"] button,
[class*="st-key-paper_buy"] button,
[class*="st-key-paper_sell"] button,
[class*="st-key-paper_close"] button {
  min-height: var(--fx-row-h) !important;
  height: var(--fx-row-h) !important;
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
[class*="st-key-alert_clear"] button,
[class*="st-key-alert_dismiss"] button,
[class*="st-key-board_close_drawer"] button {
  min-height: 2.05rem !important;
}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  font-size: 15px !important;
}
[data-testid="stDataFrame"] [role="gridcell"],
[data-testid="stDataFrame"] [role="columnheader"],
[data-testid="stTable"] td,
[data-testid="stTable"] th {
  font-size: 15px !important;
}
[data-testid="stTabs"] button {
  font-size: 15px !important;
  font-weight: 700 !important;
}
.fx-masthead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 14px;
  padding: 6px 0 12px;
  border-bottom: 1px solid var(--fx-border);
  margin-bottom: 10px;
  min-height: 2.2rem;
}
.fx-masthead-left, .fx-masthead-right {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}
.fx-brand {
  font-weight: 900;
  letter-spacing: 0.14em;
  font-size: 16px;
  color: var(--fx-buy);
}
.fx-title {
  font-weight: 900;
  letter-spacing: 0.1em;
  font-size: 22px;
  color: var(--fx-text-bright);
}
.fx-chip {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.08em;
  padding: 4px 9px;
  border-radius: 3px;
  background: #0f3d2a;
  color: var(--fx-buy);
}
.fx-chip.muted {
  background: var(--fx-elev);
  color: var(--fx-muted);
}
.fx-chip.buy { background: #0f3d2a; color: var(--fx-buy); }
.fx-chip.sell { background: #3d1216; color: var(--fx-sell); }
.fx-chip.hold { background: #2a313c; color: var(--fx-hold); }
.fx-chip.stale { background: #3a2a0c; color: var(--fx-warn); }
.fx-clock {
  font-variant-numeric: tabular-nums;
  font-weight: 800;
  font-size: 22px;
  letter-spacing: 0.02em;
  color: var(--fx-text-bright);
}
.fx-clock-mini {
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  font-size: 15px;
  color: var(--fx-text);
}
.fx-tz {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--fx-muted);
  text-transform: uppercase;
}
.fx-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 10px;
  padding: 4px 0 10px;
}
.fx-legend-note {
  font-size: 14px;
  color: var(--fx-muted);
  font-weight: 600;
  letter-spacing: 0.01em;
}
.fx-scan {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px 16px;
  flex-wrap: wrap;
  padding: 10px 12px;
  margin: 4px 0 12px;
  background: var(--fx-elev);
  border: 1px solid var(--fx-border);
  border-radius: 4px;
}
.fx-scan-meta {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.fx-scan-kicker {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--fx-muted);
}
.fx-scan-sess {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--fx-text);
}
.fx-scan-counts {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.fx-count {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.06em;
  padding: 5px 9px;
  border-radius: 3px;
  background: var(--fx-surface);
  color: var(--fx-muted);
  border: 1px solid var(--fx-border);
}
.fx-count b { font-variant-numeric: tabular-nums; padding-left: 4px; color: var(--fx-text); }
.fx-count.buy.on { background: #0f3d2a; color: var(--fx-buy); border-color: #1a5c3e; }
.fx-count.buy.on b { color: var(--fx-buy); }
.fx-count.sell.on { background: #3d1216; color: var(--fx-sell); border-color: #6b1c24; }
.fx-count.sell.on b { color: var(--fx-sell); }
.fx-count.hold.on { background: #2a313c; color: var(--fx-hold); border-color: #3a4450; }
.fx-count.hold.on b { color: var(--fx-hold); }
.fx-count.stale.on { background: #3a2a0c; color: var(--fx-warn); border-color: #6b4a12; }
.fx-count.stale.on b { color: var(--fx-warn); }
.fx-count.missing.on { background: #1a222c; color: var(--fx-muted); border-color: #3a4450; }
.fx-count.missing.on b { color: var(--fx-muted); }
.fx-board-head {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.07em;
  color: var(--fx-muted);
  text-transform: uppercase;
  padding: 4px 0 8px;
}
.fx-sig { line-height: 1.15; }
.fx-sig-quiet, .fx-sig-muted { font-weight: 700 !important; }
.fx-valid-loud { line-height: 1.2; }
.fx-empty {
  border: 1px dashed var(--fx-border);
  background: var(--fx-elev);
  border-radius: 4px;
  padding: 16px 18px;
  margin: 8px 0 12px;
}
.fx-empty-kicker {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--fx-muted);
  margin-bottom: 6px;
}
.fx-empty-title {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.03em;
  color: var(--fx-text-bright);
}
.fx-empty-body {
  font-size: 14px;
  color: var(--fx-muted);
  margin-top: 6px;
  line-height: 1.45;
}
.fx-empty-inline {
  font-size: 14px;
  color: var(--fx-muted);
  padding: 6px 0 10px;
  font-weight: 600;
}
.fx-drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 4px 0 8px;
}
.fx-drawer-pair {
  font-weight: 800;
  letter-spacing: 0.08em;
  font-size: 20px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--fx-text-bright);
}
.fx-status {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  font-size: 14px;
  font-weight: 650;
  color: var(--fx-muted);
  padding: 4px 0 6px;
  font-variant-numeric: tabular-nums;
}
.fx-status b { color: var(--fx-text); font-weight: 800; }
.fx-status .ok { color: var(--fx-buy); }
.fx-status .warn { color: var(--fx-warn); }
.fx-status .bad { color: var(--fx-sell); }
.fx-awareness-kicker {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--fx-muted);
}
.fx-awareness-status { padding: 6px 0 4px; }
.fx-awareness-table {
  font-size: 15px;
  color: var(--fx-text);
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
