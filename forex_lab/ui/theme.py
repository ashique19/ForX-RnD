"""Light (default) dense desk theme — optional dark toggle.

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

# Readability first. px so Streamlit cannot shrink captions back to ~11px.
FONT_ROOT_PX = 17
FONT_BODY = "17px"
FONT_CAPTION = "15px"
FONT_LABEL = "15px"
FONT_CHIP = "13px"
FONT_CELL = "16px"
FONT_EXPANDER = "17px"
FONT_VALID_COMPACT = "14px"
FONT_VALID_LOUD = "15px"
FONT_SIG_COMPACT = "17px"
FONT_CLOCK = "22px"
FONT_TITLE = "22px"

DESK_MODES = ("Decision", "Calendar", "Paper", "Lab", "Awareness")
DEFAULT_MODE = "Decision"
DEFAULT_THEME = "light"

# BUY / SELL stay the same hue on both palettes (high-contrast scan aids).
BUY = "#16c784"
SELL = "#ea3943"
ERROR = "#ea3943"
INFO = "#2563eb"
EVENT = "#7c3aed"
OK = BUY

LIGHT = {
    "BG": "#f4f6f8",
    "SURFACE": "#ffffff",
    "ELEVATED": "#ffffff",
    "CARD": "#ffffff",
    "BORDER": "#cbd5e1",
    "BORDER_STRONG": "#94a3b8",
    "TEXT": "#0f172a",
    "TEXT_BRIGHT": "#020617",
    "MUTED": "#475569",
    "DIM": "#64748b",
    "BUY_FG": "#042f2e",
    "BUY_BG": "#ccfbf1",
    "SELL_FG": "#ffffff",
    "SELL_BG": "#fee2e2",
    "HOLD": "#334155",
    "HOLD_FG": "#1e293b",
    "HOLD_BG": "#e2e8f0",
    "NEUTRAL": "#475569",
    "NEUTRAL_BG": "#e2e8f0",
    "WARN": "#b45309",
    "WARN_FG": "#78350f",
    "WARN_BG": "#fef3c7",
    "EMA_FAST": "#d97706",
    "EMA_SLOW": "#2563eb",
    "RSI_LINE": "#7c3aed",
}

DARK = {
    "BG": "#0a0e14",
    "SURFACE": "#151e28",
    "ELEVATED": "#1c2734",
    "CARD": "#1a2430",
    "BORDER": "#4a5d73",
    "BORDER_STRONG": "#6b8299",
    "TEXT": "#f2f5f8",
    "TEXT_BRIGHT": "#f8fafc",
    "MUTED": "#c8d0db",
    "DIM": "#b3becb",
    "BUY_FG": "#04140c",
    "BUY_BG": "#0f3d2a",
    "SELL_FG": "#ffffff",
    "SELL_BG": "#3d1216",
    "HOLD": "#c5ced8",
    "HOLD_FG": "#e8edf3",
    "HOLD_BG": "#2a313c",
    "NEUTRAL": "#c0c9d4",
    "NEUTRAL_BG": "#1a222c",
    "WARN": "#f5a524",
    "WARN_FG": "#1a1204",
    "WARN_BG": "#3a2a0c",
    "EMA_FAST": "#f5c542",
    "EMA_SLOW": "#60a5fa",
    "RSI_LINE": "#c4b5fd",
}

SESSION_FILL = {
    "ASIA": "#3730a3",
    "LONDON": "#1d4ed8",
    "NY": "#0f766e",
    "ASIA+LONDON": "#1e3a8a",
    "LONDON+NY": "#b45309",
    "ASIA+NY": "#6d28d9",
    "ASIA+LONDON+NY": "#b45309",
    "CLOSED": "#64748b",
    "OFF": "#94a3b8",
    "N/A": "#94a3b8",
}

ALERT_TONE = {
    "flip": INFO,
    "stale": "#b45309",
    "missing": "#64748b",
    "event": EVENT,
}


def _bind_palette(pal: dict[str, str]) -> None:
    g = globals()
    g.update(pal)
    g["SIGNAL_FILL"] = {
        "BUY": (BUY, pal["BUY_FG"]),
        "SELL": (SELL, pal["SELL_FG"]),
        "HOLD": (pal["HOLD_BG"], pal["HOLD_FG"]),
        "—": (pal["NEUTRAL_BG"], pal["NEUTRAL"]),
    }
    g["VALIDITY_TONE"] = {
        VALIDITY_OK: BUY,
        VALIDITY_CLOSED: pal["MUTED"],
        VALIDITY_STALE: pal["WARN"],
        VALIDITY_MISSING: pal["NEUTRAL"],
        VALIDITY_ERROR: ERROR,
    }


_bind_palette(LIGHT)
ACTIVE_THEME = DEFAULT_THEME


def apply_palette(name: str | None = None) -> str:
    """Switch module colors. Returns the resolved theme name (light|dark)."""
    global ACTIVE_THEME
    mode = str(name or DEFAULT_THEME).strip().lower()
    if mode not in {"light", "dark"}:
        mode = DEFAULT_THEME
    _bind_palette(DARK if mode == "dark" else LIGHT)
    ACTIVE_THEME = mode
    return mode


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


def empty_state_html(
    title: str,
    body: str,
    *,
    kicker: str = "EMPTY",
    action: str = "",
) -> str:
    act = f'<div class="fx-empty-action">{_esc(action)}</div>' if action else ""
    return (
        f'<div class="fx-empty">'
        f'<div class="fx-empty-kicker">{_esc(kicker)}</div>'
        f'<div class="fx-empty-title">{_esc(title)}</div>'
        f'<div class="fx-empty-body">{_esc(body)}</div>'
        f"{act}</div>"
    )


def empty_inline_html(text: str) -> str:
    return f'<div class="fx-empty-inline">{_esc(text)}</div>'


CHROME_STATE_PREFIX = "chrome_open_"


def chrome_state_key(name: str) -> str:
    """Session-state key for an auxiliary chrome card. Default is collapsed."""
    slug = "".join(ch if ch.isalnum() else "_" for ch in str(name or "").strip().lower())
    slug = slug.strip("_") or "aux"
    return f"{CHROME_STATE_PREFIX}{slug}"


def chrome_card_head_html(title: str, *, expanded: bool = False, note: str = "") -> str:
    """Header copy for a collapsible helper card. Chevron is a separate icon button."""
    extra = f'<span class="fx-chrome-note">{_esc(note)}</span>' if note else ""
    state = "open" if expanded else "closed"
    return (
        f'<div class="fx-chrome-head {state}">'
        f'<span class="fx-chrome-title">{_esc(title)}</span>'
        f"{extra}</div>"
    )


def section_head_html(kicker: str, title: str, *, note: str = "") -> str:
    extra = f'<span class="fx-section-note">{_esc(note)}</span>' if note else ""
    return (
        f'<div class="fx-section-head">'
        f'<span class="fx-section-kicker">{_esc(kicker)}</span>'
        f'<span class="fx-section-title">{_esc(title)}</span>'
        f"{extra}</div>"
    )


def card_html(
    title: str,
    body: str = "",
    *,
    kicker: str = "",
    tone: str = "info",
) -> str:
    kick = f'<div class="fx-card-kicker">{_esc(kicker)}</div>' if kicker else ""
    body_bit = f'<div class="fx-card-body">{_esc(body)}</div>' if body else ""
    return (
        f'<div class="fx-card fx-card-{_esc(tone)}">'
        f"{kick}"
        f'<div class="fx-card-title">{_esc(title)}</div>'
        f"{body_bit}</div>"
    )


def scan_legend_html() -> str:
    return (
        '<div class="fx-legend">'
        '<span class="fx-chip buy">BUY</span>'
        '<span class="fx-chip sell">SELL</span>'
        '<span class="fx-chip hold">HOLD</span>'
        '<span class="fx-chip stale">STALE</span>'
        '<span class="fx-legend-note">STALE outranks the flash — Fetch first.</span>'
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
    pad = "7px 8px" if compact else "10px 12px"
    opacity = "0.78" if weak else "1"
    return (
        f'<div class="{" ".join(classes)}" style="background:{bg};color:{fg};font-weight:800;'
        f"font-size:{size};text-align:center;padding:{pad};border-radius:4px;"
        f"letter-spacing:0.08em;box-shadow:0 0 0 1px rgba(0,0,0,0.35);opacity:{opacity};"
        f'width:100%;min-height:2.45rem;display:flex;align-items:center;justify-content:center">'
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
        mark = "⚠ STALE" if v == VALIDITY_STALE else v
        return (
            f'<div class="fx-valid fx-valid-loud fx-valid-{v.lower()}" title="{_esc(v)}" '
            f'style="background:{bg};color:{fg};font-weight:800;font-size:{FONT_VALID_LOUD};'
            f"letter-spacing:0.06em;text-align:center;padding:6px 8px;border-radius:4px;"
            f"border:2px solid {fg};width:100%;min-height:2.45rem;"
            f'display:flex;align-items:center;justify-content:center">'
            f"{_esc(mark)}</div>"
        )
    if compact:
        return (
            f'<div class="fx-valid fx-valid-quiet fx-valid-{v.lower()}" title="{_esc(v)}" '
            f'style="text-align:center;line-height:1.1;width:100%;min-height:2.45rem;'
            f'display:flex;flex-direction:column;align-items:center;justify-content:center;'
            f'background:{NEUTRAL_BG};border-radius:4px;border:1px solid {BORDER}">'
            f'<span style="color:{tone};font-size:1.15rem;line-height:1">●</span>'
            f'<div style="font-size:{FONT_VALID_COMPACT};font-weight:800;letter-spacing:0.06em;color:{tone}">'
            f"{_esc(v)}</div></div>"
        )
    fg = WARN_FG if v in {VALIDITY_OK, VALIDITY_STALE} else "#fff"
    return (
        f'<div class="fx-valid" style="background:{tone};color:{fg};font-weight:800;'
        f"font-size:15px;letter-spacing:0.08em;text-align:center;padding:5px 10px;"
        f'border-radius:4px;display:inline-block">{_esc(v)}</div>'
    )


def signal_stack_html(
    sig: object,
    conf: object = "",
    *,
    weak: bool = False,
    mtf: object = "",
) -> str:
    """Scan-board signal chip + conf / MTF under it (one cell, not three columns)."""
    badge = signal_badge_html(sig, weak=weak, compact=True)
    bits = [str(conf).strip()] if conf not in {None, "", "—"} else []
    mtf_s = str(mtf or "").strip()
    if mtf_s and mtf_s.lower() not in {"n/a", "na", "—", "-"}:
        bits.append(mtf_s)
    meta = " · ".join(bits)
    extra = f'<div class="fx-sig-meta">{_esc(meta)}</div>' if meta else ""
    return f'<div class="fx-sig-stack">{badge}{extra}</div>'


def last_cell_html(last: object, session: object = "") -> str:
    sess = str(session or "").strip() or "—"
    return (
        f'<div class="fx-last-cell">'
        f'<div class="fx-last-px">{_esc(str(last or "n/a"))}</div>'
        f'<div class="fx-last-sess">{_esc(sess)}</div>'
        f"</div>"
    )


def pair_tf_html(tf: object) -> str:
    return f'<div class="fx-pair-tf">{_esc(str(tf or ""))}</div>'


def drawer_meta_html(
    *,
    last_bar: object = "n/a",
    last_fetch: object = "n/a",
    last_signal: object = "n/a",
) -> str:
    return (
        f'<div class="fx-drawer-meta">'
        f"<span>Last bar <b>{_esc(str(last_bar or 'n/a'))}</b></span>"
        f"<span>Fetch <b>{_esc(str(last_fetch or 'n/a'))}</b></span>"
        f"<span>Signal <b>{_esc(str(last_signal or 'n/a'))}</b></span>"
        f"</div>"
    )


def ohlc_header_html(text: str, *, up: bool = True) -> str:
    tone = "up" if up else "down"
    return f'<div class="fx-ohlc-bar {tone}">{_esc(text)}</div>'


def _advice_card_html(view: object) -> str:
    kicker = _esc(str(getattr(view, "kicker", "") or ""))
    side = str(getattr(view, "side", "") or "HOLD").upper()
    tone = {"BUY": "buy", "SELL": "sell"}.get(side, "hold")
    line = str(getattr(view, "line", "") or "")
    if not line:
        action = _esc(str(getattr(view, "action", "") or ""))
        tp = _esc(str(getattr(view, "take_profit", "n/a") or "n/a"))
        sl = _esc(str(getattr(view, "stop_loss", "n/a") or "n/a"))
        timeline = _esc(str(getattr(view, "timeline", "") or ""))
        now_at = _esc(str(getattr(view, "now_at", "") or ""))
        duration = timeline.replace("duration ", "") if timeline else ""
        if now_at:
            line = (
                f"{kicker}: {action}: now at {now_at}, stop loss {sl}, "
                f"target {tp}, duration {duration}."
            )
        else:
            line = f"{kicker}: {action}: stop loss {sl}, target {tp}, {timeline}."
    missing = str(getattr(view, "missing_reason", "") or "")
    available = bool(getattr(view, "available", True))
    body = _esc(line)
    extra = ""
    if not available and missing and "need Fetch" in missing:
        extra = f'<div class="fx-advice-note">{_esc(missing)}</div>'
    return (
        f'<article class="fx-advice-card {tone}">'
        f'<div class="fx-advice-kicker">{kicker}</div>'
        f'<p class="fx-advice-line">{body}</p>'
        f"{extra}"
        f"</article>"
    )


def _invalidation_html(lines: list[str] | tuple[str, ...] | None) -> str:
    if not lines:
        return ""
    items = "".join(f"<li>{_esc(str(x))}</li>" for x in lines)
    return (
        f'<div class="fx-invalid">'
        f'<div class="fx-invalid-kicker">If scenario changes</div>'
        f'<ul class="fx-invalid-list">{items}</ul>'
        f"</div>"
    )


def signal_brief_html(
    *,
    headline: str,
    primary: object | None = None,
    alternate: object | None = None,
    horizons: list | tuple | None = None,
    why: str = "",
    invalidation: list[str] | tuple[str, ...] | None = None,
    disclaimer: str = "",
) -> str:
    cards = ""
    views = list(horizons or [])
    if not views:
        if primary is not None:
            views.append(primary)
        if alternate is not None:
            views.append(alternate)
    for view in views:
        cards += _advice_card_html(view)
    why_bit = f'<p class="fx-why">{_esc(why)}</p>' if why else ""
    disc = f'<div class="fx-brief-disc">{_esc(disclaimer)}</div>' if disclaimer else ""
    return (
        f'<div class="fx-brief">'
        f'<h2 class="fx-brief-title">{_esc(headline)}</h2>'
        f'<div class="fx-advice-grid">{cards}</div>'
        f"{why_bit}"
        f"{_invalidation_html(invalidation)}"
        f"{disc}"
        f"</div>"
    )


def tech_bullets_html(bullets: list[str] | tuple[str, ...]) -> str:
    if not bullets:
        return ""
    items = "".join(f"<li>{_esc(str(b))}</li>" for b in bullets)
    return (
        f'<div class="fx-tech">'
        f'<div class="fx-tech-kicker">Technical notes</div>'
        f'<ul class="fx-tech-list">{items}</ul>'
        f"</div>"
    )

TERMINAL_CSS = """
:root {
  --fx-row-h: 2.85rem;
  --fx-font-body: 17px;
  --fx-font-caption: 15px;
  --fx-font-label: 15px;
  --fx-font-chip: 13px;
  --fx-font-cell: 16px;
  --fx-font-expander: 17px;
  --fx-font-clock: 22px;
  --fx-font-title: 22px;
  --fx-radius: 8px;
  --fx-rail-w: 22rem;
}
html {
  font-size: 17px !important;
}
html, body, .stApp, [data-testid="stAppViewContainer"] {
  background: var(--fx-bg) !important;
  color: var(--fx-text) !important;
}
body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
  font-size: 17px !important;
  color: var(--fx-text) !important;
}
.stApp {
  font-feature-settings: "tnum" 1, "ss01" 1;
  font-variant-numeric: tabular-nums;
}
[data-testid="stHeader"],
header[data-testid="stHeader"],
.stApp > header,
.stAppHeader,
header.stAppHeader,
div[data-testid="stHeader"] {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  min-height: 0 !important;
  max-height: 0 !important;
  overflow: hidden !important;
  opacity: 0 !important;
  position: static !important;
  pointer-events: none !important;
  z-index: 0 !important;
}
[data-testid="stToolbar"],
[data-testid="stAppToolbar"],
.stAppToolbar,
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
.stDeployButton,
div[data-testid="stToolbarActions"],
[data-testid="stHeaderActionElements"] {
  display: none !important;
  height: 0 !important;
  min-height: 0 !important;
}
footer { visibility: hidden; height: 0; }
#MainMenu { visibility: hidden; }
.block-container,
[data-testid="stMainBlockContainer"] {
  padding-top: 1.85rem !important;
  padding-bottom: 1.4rem !important;
  padding-left: 0.9rem !important;
  padding-right: 0.9rem !important;
  max-width: 100% !important;
}
section.main, [data-testid="stMain"] {
  margin-top: 0 !important;
  padding-top: 0 !important;
}
[data-testid="stMarkdownContainer"],
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stText"],
.stMarkdown p {
  font-size: 17px !important;
  color: var(--fx-text) !important;
  line-height: 1.4 !important;
}
[data-testid="stMarkdownContainer"] p strong,
[data-testid="stMarkdownContainer"] strong {
  color: var(--fx-text-bright) !important;
  font-weight: 800 !important;
}
small, .stMarkdown small {
  font-size: 15px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stCaptionContainer"], .stCaption,
[data-testid="stCaptionContainer"] p, .stCaption p,
[data-testid="stCaptionContainer"] * {
  font-size: 15px !important;
  line-height: 1.4 !important;
  color: var(--fx-muted) !important;
}
h1, [data-testid="stHeading"] h1 {
  font-size: 1.45rem !important;
  letter-spacing: 0.03em;
  font-weight: 800 !important;
  color: var(--fx-text-bright) !important;
}
h2, [data-testid="stHeading"] h2 {
  font-size: 1.22rem !important;
  letter-spacing: 0.03em;
  font-weight: 800 !important;
  color: var(--fx-text-bright) !important;
}
h3, [data-testid="stHeading"] h3, h4, h5, h6 {
  font-size: 1.08rem !important;
  letter-spacing: 0.03em;
  font-weight: 800 !important;
  color: var(--fx-text) !important;
}
.stMarkdown p { margin-bottom: 0.22rem; }
hr { margin: 0.4rem 0 !important; border-color: var(--fx-border) !important; }
[data-testid="stExpander"] {
  border: 1px solid var(--fx-border) !important;
  border-radius: var(--fx-radius) !important;
  background: var(--fx-card) !important;
  margin-bottom: 0.4rem !important;
}
[data-testid="stExpander"] details { gap: 0.25rem; }
[data-testid="stExpander"] summary {
  padding: 0.5rem 0.7rem !important;
}
[data-testid="stExpander"] summary p {
  font-size: 17px !important;
  font-weight: 700 !important;
  letter-spacing: 0.02em;
  color: var(--fx-text) !important;
}
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] p,
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] li {
  font-size: 17px !important;
  color: var(--fx-text) !important;
}
[data-testid="stExpander"] [data-testid="stCaptionContainer"],
[data-testid="stExpander"] [data-testid="stCaptionContainer"] p,
[data-testid="stExpander"] [data-testid="stCaptionContainer"] *,
[data-testid="stExpander"] .stCaption,
[data-testid="stExpander"] .stCaption p {
  font-size: 15px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stMetricValue"] {
  font-size: 1.28rem !important;
  font-variant-numeric: tabular-nums;
  color: var(--fx-text-bright) !important;
}
[data-testid="stMetricLabel"] {
  font-size: 15px !important;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--fx-muted) !important;
  font-weight: 800 !important;
}
[data-testid="stMetricDelta"] { font-size: 15px !important; color: var(--fx-muted) !important; }
div[data-testid="stAlert"] {
  padding: 0.5rem 0.7rem !important;
  font-size: 15px !important;
}
[data-testid="stWidgetLabel"] p,
[data-testid="stWidgetLabel"] label,
[data-testid="stWidgetLabel"] {
  font-size: 15px !important;
  letter-spacing: 0.04em !important;
  text-transform: uppercase !important;
  color: var(--fx-muted) !important;
  font-weight: 800 !important;
}
[data-testid="stSidebar"] {
  background: var(--fx-surface) !important;
  color: var(--fx-text) !important;
  border-right: 1px solid var(--fx-border-strong) !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
  color: var(--fx-text) !important;
  font-size: 17px !important;
}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
[data-testid="stSidebar"] .stCaption,
[data-testid="stSidebar"] .stCaption p {
  font-size: 15px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 15px !important;
  color: var(--fx-muted) !important;
}
[data-testid="stTooltipContent"],
[role="tooltip"],
div[data-baseweb="tooltip"] {
  font-size: 15px !important;
  line-height: 1.4 !important;
  color: var(--fx-text) !important;
  background: var(--fx-elev) !important;
}
div[data-testid="stHorizontalBlock"] {
  align-items: center !important;
  gap: 0.32rem !important;
}
div[data-testid="stHorizontalBlock"]:has([data-testid="stWidgetLabel"]):has([data-testid="stButton"]) {
  align-items: flex-end !important;
}
[data-testid="column"] {
  padding-left: 0.2rem !important;
  padding-right: 0.2rem !important;
}
[data-testid="stButton"] button {
  min-height: 2.2rem !important;
  padding: 0.28rem 0.65rem !important;
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
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea {
  border: 1px solid var(--fx-border-strong) !important;
  background: var(--fx-surface) !important;
  color: var(--fx-text) !important;
  font-size: 17px !important;
}
[class*="st-key-board_open"] button,
[class*="st-key-paper_buy"] button,
[class*="st-key-paper_sell"] button,
[class*="st-key-paper_close"] button,
[class*="st-key-board_fetch"] button,
[class*="st-key-board_rm"] button {
  min-height: var(--fx-row-h) !important;
  height: var(--fx-row-h) !important;
}
[class*="st-key-board_open"] button {
  background: var(--fx-elev) !important;
  border: 1px solid var(--fx-border-strong) !important;
  color: var(--fx-text-bright) !important;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
  letter-spacing: 0.06em !important;
  font-size: 16px !important;
}
[class*="st-key-board_open"] button[kind="primary"] {
  border-color: var(--fx-buy) !important;
  color: var(--fx-buy) !important;
  box-shadow: inset 3px 0 0 var(--fx-buy);
  background: var(--fx-buy-bg) !important;
}
[class*="st-key-board_rm"] button {
  background: transparent !important;
  border: 1px solid var(--fx-border) !important;
  color: var(--fx-muted) !important;
  font-size: 18px !important;
}
[class*="st-key-paper_buy"] button {
  background: var(--fx-buy) !important;
  color: var(--fx-buy-fg) !important;
  border: 0 !important;
}
[class*="st-key-paper_sell"] button {
  background: var(--fx-sell) !important;
  color: var(--fx-sell-fg) !important;
  border: 0 !important;
}
[class*="st-key-paper_close"] button {
  background: var(--fx-hold-bg) !important;
  color: var(--fx-text) !important;
  border: 0 !important;
}
[class*="st-key-board_fetch"] button {
  background: var(--fx-warn) !important;
  color: var(--fx-warn-fg) !important;
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
  font-size: 16px !important;
}
[data-testid="stDataFrame"] [role="gridcell"],
[data-testid="stDataFrame"] [role="columnheader"],
[data-testid="stTable"] td,
[data-testid="stTable"] th {
  font-size: 16px !important;
}
[data-testid="stTabs"] {
  margin-top: 0.2rem;
  background: var(--fx-elev);
  border: 1px solid var(--fx-border-strong);
  border-radius: var(--fx-radius);
  padding: 0.35rem 0.45rem 0.55rem;
}
[data-testid="stTabs"] [data-baseweb="tab-list"],
[data-testid="stTabs"] [role="tablist"] {
  gap: 6px !important;
  border-bottom: 1px solid var(--fx-border) !important;
  margin-bottom: 0.4rem !important;
}
[data-testid="stTabs"] button,
[data-testid="stTabs"] [data-baseweb="tab"],
[data-testid="stTab"] {
  font-size: 16px !important;
  font-weight: 800 !important;
  letter-spacing: 0.04em !important;
  padding: 0.55rem 1.05rem !important;
  min-height: 2.65rem !important;
  cursor: pointer !important;
  color: var(--fx-muted) !important;
  background: var(--fx-surface) !important;
  border: 1px solid var(--fx-border) !important;
  border-bottom: 2px solid var(--fx-border) !important;
  border-radius: 6px 6px 0 0 !important;
}
[data-testid="stTabs"] button:hover,
[data-testid="stTabs"] [data-baseweb="tab"]:hover {
  color: var(--fx-text) !important;
  background: var(--fx-elev) !important;
}
[data-testid="stTabs"] button[aria-selected="true"],
[data-testid="stTabs"] [aria-selected="true"],
[data-testid="stTab"][aria-selected="true"] {
  color: var(--fx-text-bright) !important;
  background: var(--fx-card) !important;
  border-color: var(--fx-border-strong) !important;
  border-bottom: 3px solid var(--fx-buy) !important;
}
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stExpander"] details {
  border-color: var(--fx-border-strong) !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] > div {
  background: var(--fx-card);
  border: 1px solid var(--fx-border-strong) !important;
  border-radius: var(--fx-radius) !important;
  padding: 0.55rem 0.75rem !important;
}
[data-testid="stMetric"] {
  background: var(--fx-elev);
  border: 1px solid var(--fx-border) !important;
  border-radius: 6px;
  padding: 0.4rem 0.55rem;
}
.fx-masthead {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0;
  padding: 10px 14px 8px;
  background: var(--fx-elev);
  border: 1px solid var(--fx-border-strong);
  border-radius: var(--fx-radius);
  margin-bottom: 8px;
}
.fx-masthead-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px 14px;
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
  font-size: 15px;
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
  background: var(--fx-buy-bg);
  color: var(--fx-buy);
  border: 1px solid var(--fx-border);
}
.fx-chip.muted {
  background: var(--fx-surface);
  color: var(--fx-muted);
  border: 1px solid var(--fx-border);
}
.fx-chip.buy { background: var(--fx-buy-bg); color: var(--fx-buy); }
.fx-chip.sell { background: var(--fx-sell-bg); color: var(--fx-sell); }
.fx-chip.hold { background: var(--fx-hold-bg); color: var(--fx-hold); }
.fx-chip.stale { background: var(--fx-warn-bg); color: var(--fx-warn); }
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
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--fx-muted);
  text-transform: uppercase;
}
.fx-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 8px;
  padding: 8px 0 0;
  margin-top: 8px;
  width: 100%;
  border-top: 1px solid var(--fx-border);
}
.fx-legend-note {
  font-size: 15px;
  color: var(--fx-muted);
  font-weight: 600;
  letter-spacing: 0.01em;
}
.fx-scan {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px 14px;
  flex-wrap: wrap;
  padding: 8px 10px;
  margin: 0 0 8px;
  background: var(--fx-elev);
  border: 1px solid var(--fx-border-strong);
  border-radius: var(--fx-radius);
}
.fx-scan-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
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
  gap: 6px;
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
.fx-count.buy.on { background: var(--fx-buy-bg); color: var(--fx-buy); border-color: var(--fx-buy); }
.fx-count.buy.on b { color: var(--fx-buy); }
.fx-count.sell.on { background: var(--fx-sell-bg); color: var(--fx-sell); border-color: var(--fx-sell); }
.fx-count.sell.on b { color: var(--fx-sell); }
.fx-count.hold.on { background: var(--fx-hold-bg); color: var(--fx-hold); border-color: var(--fx-border-strong); }
.fx-count.hold.on b { color: var(--fx-hold); }
.fx-count.stale.on { background: var(--fx-warn-bg); color: var(--fx-warn); border-color: var(--fx-warn); }
.fx-count.stale.on b { color: var(--fx-warn); }
.fx-count.missing.on { background: var(--fx-neutral-bg); color: var(--fx-muted); border-color: var(--fx-border-strong); }
.fx-count.missing.on b { color: var(--fx-muted); }
.fx-board-head {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--fx-muted);
  text-transform: uppercase;
  padding: 2px 0 8px;
  border-bottom: 1px solid var(--fx-border);
}
.fx-sig { line-height: 1.15; }
.fx-sig-quiet, .fx-sig-muted { font-weight: 700 !important; }
.fx-sig-stack { width: 100%; }
.fx-sig-meta {
  font-size: 13px;
  font-weight: 700;
  color: var(--fx-muted);
  text-align: center;
  margin-top: 3px;
  letter-spacing: 0.02em;
}
.fx-pair-tf {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--fx-muted);
  text-align: center;
  margin-top: 3px;
  text-transform: uppercase;
}
.fx-last-cell { text-align: right; width: 100%; }
.fx-last-px {
  font-size: 16px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: var(--fx-text-bright);
  line-height: 1.15;
}
.fx-last-sess {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: var(--fx-muted);
  text-transform: uppercase;
  margin-top: 2px;
}
.fx-valid-loud { line-height: 1.15; }
.fx-empty {
  border: 1px dashed var(--fx-border-strong);
  background: var(--fx-elev);
  border-radius: var(--fx-radius);
  padding: 14px 16px;
  margin: 6px 0 10px;
}
.fx-empty-kicker {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--fx-warn);
  margin-bottom: 4px;
}
.fx-empty-title {
  font-size: 17px;
  font-weight: 800;
  letter-spacing: 0.03em;
  color: var(--fx-text-bright);
}
.fx-empty-body {
  font-size: 15px;
  color: var(--fx-muted);
  margin-top: 4px;
  line-height: 1.4;
}
.fx-empty-action {
  font-size: 15px;
  font-weight: 800;
  color: var(--fx-warn);
  margin-top: 8px;
  letter-spacing: 0.02em;
}
.fx-empty-inline {
  font-size: 15px;
  color: var(--fx-muted);
  padding: 4px 0 8px;
  font-weight: 600;
}
.fx-drawer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 2px 2px 8px;
  border-bottom: 1px solid var(--fx-border);
  margin-bottom: 6px;
}
.fx-drawer-pair {
  font-weight: 800;
  letter-spacing: 0.08em;
  font-size: 20px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--fx-text-bright);
}
.fx-drawer-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  font-size: 15px;
  color: var(--fx-muted);
  padding: 6px 0 2px;
  font-variant-numeric: tabular-nums;
}
.fx-drawer-meta b { color: var(--fx-text); font-weight: 800; }
.fx-status {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  font-size: 15px;
  font-weight: 650;
  color: var(--fx-muted);
  padding: 2px 0 4px;
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
.fx-awareness-status { padding: 4px 0 2px; }
.fx-awareness-table {
  font-size: 16px;
  color: var(--fx-text);
}
.fx-section-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  padding: 4px 2px 6px;
  margin: 2px 0 2px;
}
.fx-section-kicker {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fx-buy);
}
.fx-section-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: var(--fx-text-bright);
}
.fx-section-note {
  font-size: 15px;
  color: var(--fx-muted);
  font-weight: 600;
}
.fx-card {
  background: var(--fx-elev);
  border: 1px solid var(--fx-border-strong);
  border-radius: var(--fx-radius);
  padding: 10px 12px;
  margin: 6px 0 10px;
}
.fx-card-kicker {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--fx-muted);
  margin-bottom: 4px;
}
.fx-card-title {
  font-size: 17px;
  font-weight: 800;
  color: var(--fx-text-bright);
  letter-spacing: 0.02em;
}
.fx-card-body {
  font-size: 15px;
  color: var(--fx-muted);
  margin-top: 4px;
  line-height: 1.4;
}
.fx-card-warn { border-color: var(--fx-sell); }
.fx-card-caution { border-color: var(--fx-warn); }
.fx-card-info { border-color: var(--fx-border-strong); }
.fx-chrome-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  padding: 4px 2px 2px;
  min-height: 2.15rem;
}
.fx-chrome-title {
  font-size: 15px;
  font-weight: 800;
  letter-spacing: 0.01em;
  color: var(--fx-text);
  line-height: 1.35;
}
.fx-chrome-head.closed .fx-chrome-title {
  color: var(--fx-muted);
  font-weight: 700;
}
.fx-chrome-note {
  font-size: 14px;
  color: var(--fx-muted);
  font-weight: 600;
}
[class*="st-key-chrome_toggle"] button,
[class*="st-key-board_reload"] button {
  min-height: 2.15rem !important;
  min-width: 2.15rem !important;
  height: 2.15rem !important;
  width: 2.15rem !important;
  padding: 0 !important;
  font-size: 18px !important;
  font-weight: 800 !important;
  line-height: 1 !important;
  background: var(--fx-elev) !important;
  border: 1px solid var(--fx-border-strong) !important;
  color: var(--fx-text-bright) !important;
  border-radius: 4px !important;
}
.fx-rail {
  background: var(--fx-surface);
  border: 1px solid var(--fx-border);
  border-radius: var(--fx-radius);
  padding: 8px 10px 10px;
}
.fx-toolbar {
  background: var(--fx-card);
  border: 1px solid var(--fx-border);
  border-radius: var(--fx-radius);
  padding: 6px 10px 8px;
  margin-bottom: 8px;
}
.fx-nav {
  display: flex;
  align-items: stretch;
  gap: 8px;
  position: relative !important;
  z-index: 1;
  padding: 4px 0 14px;
  margin: 0 0 18px;
  border-bottom: 1px solid var(--fx-border-strong);
}
.fx-nav-gap {
  height: 1.15rem;
  margin-bottom: 0.85rem;
}
[class*="st-key-desk_nav"] button {
  min-height: 2.75rem !important;
  height: 2.75rem !important;
  font-size: 16px !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
  background: var(--fx-surface) !important;
  border: 1px solid var(--fx-border) !important;
  color: var(--fx-muted) !important;
  box-shadow: none !important;
}
[class*="st-key-desk_nav"] button:hover {
  color: var(--fx-text) !important;
  border-color: var(--fx-border-strong) !important;
}
[class*="st-key-desk_nav"] button[kind="primary"] {
  background: var(--fx-elev) !important;
  color: var(--fx-text-bright) !important;
  border-color: var(--fx-buy) !important;
  box-shadow: inset 0 -3px 0 var(--fx-buy) !important;
}
.fx-mode-kicker {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fx-buy);
}
.js-plotly-plot .plotly .gtitle {
  font-size: 16px !important;
}
.fx-brief {
  padding: 6px 2px 8px;
  margin: 0 0 8px;
  max-width: 58rem;
}
.fx-brief-title {
  font-size: 24px;
  font-weight: 800;
  line-height: 1.28;
  letter-spacing: 0.01em;
  color: var(--fx-text-bright);
  margin: 4px 0 16px;
}
.fx-advice-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
  margin: 0 0 18px;
}
.fx-advice-card {
  background: var(--fx-elev);
  border: 1px solid var(--fx-border-strong);
  border-radius: 10px;
  padding: 16px 18px 16px;
}
.fx-advice-card.buy { border-color: var(--fx-buy); }
.fx-advice-card.sell { border-color: var(--fx-sell); }
.fx-advice-kicker {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fx-muted);
  margin-bottom: 8px;
}
.fx-advice-line {
  font-size: 17px;
  line-height: 1.5;
  color: var(--fx-text);
  margin: 0;
  font-weight: 600;
}
.fx-invalid {
  margin: 4px 0 16px;
  padding: 12px 14px 10px;
  border: 1px solid var(--fx-border);
  border-radius: 10px;
  background: var(--fx-card);
  max-width: 48rem;
}
.fx-invalid-kicker {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fx-warn);
  margin-bottom: 6px;
}
.fx-invalid-list {
  margin: 0;
  padding: 0 0 0 1.15rem;
  font-size: 16px;
  line-height: 1.5;
  color: var(--fx-text);
}
.fx-advice-list {
  margin: 0;
  padding: 0 0 0 1.1rem;
  font-size: 16px;
  line-height: 1.55;
  color: var(--fx-text);
}
.fx-advice-list b { color: var(--fx-text-bright); font-variant-numeric: tabular-nums; }
.fx-advice-note {
  font-size: 14px;
  color: var(--fx-muted);
  margin-top: 10px;
  line-height: 1.4;
}
.fx-why {
  font-size: 17px;
  line-height: 1.55;
  color: var(--fx-text);
  margin: 0 0 10px;
  max-width: 48rem;
}
.fx-brief-disc {
  font-size: 14px;
  color: var(--fx-muted);
  margin: 0 0 18px;
  line-height: 1.4;
}
.fx-tech {
  padding: 8px 2px 4px;
  max-width: 48rem;
}
.fx-tech-kicker {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fx-buy);
  margin-bottom: 6px;
}
.fx-tech-list {
  margin: 0;
  padding: 0 0 0 1.15rem;
  font-size: 16px;
  line-height: 1.5;
  color: var(--fx-text);
}
.fx-ohlc-bar {
  font-size: 15px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--fx-text-bright);
  letter-spacing: 0.02em;
  padding: 4px 0 8px;
}
.fx-ohlc-bar.down { color: var(--fx-sell); }
.fx-ohlc-bar.up { color: var(--fx-buy); }
[class*="st-key-chart_tf_btn"] button {
  min-height: 2.15rem !important;
  height: 2.15rem !important;
  font-size: 14px !important;
  letter-spacing: 0.06em !important;
}
.fx-theme-kicker {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--fx-muted);
  padding: 2px 0 4px;
}
[class*="st-key-desk_theme"] label,
[class*="st-key-desk_theme"] p,
[class*="st-key-desk_theme"] [data-testid="stWidgetLabel"] p {
  font-size: 13px !important;
  font-weight: 800 !important;
  letter-spacing: 0.06em !important;
  color: var(--fx-muted) !important;
}
@media (max-width: 1320px) {
  .block-container,
  [data-testid="stMainBlockContainer"] {
    padding-top: 1.85rem !important;
    padding-left: 0.65rem !important;
    padding-right: 0.65rem !important;
  }
  [data-testid="column"] {
    padding-left: 0.12rem !important;
    padding-right: 0.12rem !important;
  }
  .fx-title, .fx-clock { font-size: 20px; }
  .fx-brief-title { font-size: 22px; }
  .fx-advice-grid { grid-template-columns: 1fr 1fr; gap: 12px; }
}
"""

def _root_css() -> str:
    """Bind the active palette into :root so CSS tracks Light / Dark."""
    return f"""
:root {{
  --fx-bg: {BG};
  --fx-surface: {SURFACE};
  --fx-elev: {ELEVATED};
  --fx-card: {CARD};
  --fx-border: {BORDER};
  --fx-border-strong: {BORDER_STRONG};
  --fx-text: {TEXT};
  --fx-text-bright: {TEXT_BRIGHT};
  --fx-muted: {MUTED};
  --fx-dim: {DIM};
  --fx-buy: {BUY};
  --fx-buy-fg: {BUY_FG};
  --fx-buy-bg: {BUY_BG};
  --fx-sell: {SELL};
  --fx-sell-fg: {SELL_FG};
  --fx-sell-bg: {SELL_BG};
  --fx-hold: {HOLD};
  --fx-hold-fg: {HOLD_FG};
  --fx-hold-bg: {HOLD_BG};
  --fx-neutral: {NEUTRAL};
  --fx-neutral-bg: {NEUTRAL_BG};
  --fx-warn: {WARN};
  --fx-warn-fg: {WARN_FG};
  --fx-warn-bg: {WARN_BG};
  --fx-error: {ERROR};
  --fx-info: {INFO};
  --fx-event: {EVENT};
}}
"""


def plotly_template() -> str:
    """Plotly layout template for the active desk palette."""
    return "plotly_dark" if ACTIVE_THEME == "dark" else "plotly_white"


def terminal_css() -> str:
    return _root_css() + TERMINAL_CSS


def inject_terminal_css() -> None:
    """Apply desk CSS for the active Light (default) / Dark palette.

    Safe no-op when Streamlit is not importing the app. Presentation only —
    does not change BrokerPort, paper fills, or Asia/Dhaka clocks.
    """
    import streamlit as st

    try:
        raw = st.session_state.get("desk_theme")
    except Exception:
        raw = DEFAULT_THEME
    name = apply_palette(raw)
    st.markdown(
        f'<style data-fx-theme="{name}">{terminal_css()}</style>',
        unsafe_allow_html=True,
    )
