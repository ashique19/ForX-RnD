"""Forex Research Lab — local Streamlit dashboard.

Research only. No live broker APIs, no auto-trading. Paper fills are local.
Run from the project root:  streamlit run streamlit_app.py
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import base64
import html

import pandas as pd
import streamlit as st

from forex_lab.broker import BrokerError, BrokerPort, make_broker, position_for_pair
from forex_lab.score import filter_journal
from forex_lab.calendar import CalendarBundle, countdown_label
from forex_lab import calendar as calendar_lab
from forex_lab.clock import clock_note, fmt_display, relabel, timezone_tag, zoneinfo_for
from forex_lab.config_loader import load_config
from forex_lab.paths import project_root
from forex_lab.advise import Suggestion, suggest_actions
from forex_lab.mtf import MTF_AGREE, MTF_CONFLICT, MtfStatus
from forex_lab.ui.board import (
    BOARD_TABLE_COLS,
    NEED_FETCH_TRAIN,
    PAPER_GATE_CAPTION,
    PAPER_STALE_CAPTION,
    attach_next_event,
    board_table,
    build_board_row,
    conf_label,
    paper_submit_allowed,
    paper_submit_block_reason,
    paper_submit_risk_defaults,
    research_target,
    spark_ascii,
    style_board,
    validity_token,
)
from forex_lab.session import SessionState, classify_session
from forex_lab.ui.quote import QuoteView
from forex_lab.fred import FredStatus, fred_feed_status
from forex_lab.ui.alerts import (
    alerts_cfg,
    beep_wav,
    dismiss_alert,
    format_alert_time,
    kind_tone,
    load_state as load_alert_state,
    process_watch,
    save_state as save_alert_state,
    visible_alerts,
)
from forex_lab.ui.health import (
    awareness_status_html,
    awareness_table_html,
    build_health_rows,
    health_strip,
    health_unhealthy,
    model_status_map,
    status_token,
)
from forex_lab.ui.pipeline import (
    artifact_status,
    equity_from_trades,
    load_metrics,
    load_report,
    load_signals,
    load_trades,
    metrics_table,
    run_backtest,
    run_fetch,
    run_retrain,
    run_signals,
    run_train,
    style_signals,
    ui_pairs,
)
from forex_lab.ui.watchlist import (
    KNOWN_INTERVALS,
    WatchlistError,
    add_pair,
    load_watchlist,
    remove_pair,
    save_watchlist,
    watchlist_path,
)
from forex_lab.ui.theme import (
    BUY,
    BUY_BG,
    HOLD_BG,
    MUTED,
    SELL,
    SURFACE,
    TEXT,
    TEXT_BRIGHT,
    WARN,
    empty_inline_html,
    empty_state_html,
    inject_terminal_css,
    scan_counts,
    scan_legend_html,
    scan_strip_html,
    section_head_html,
    card_html,
    session_fill,
    signal_badge_html,
    validity_badge_html,
)
from forex_lab.ui.chart import candle_bar_count, candlestick_figure, chart_blocked_reason
from forex_lab.ui.workspace import (
    Workspace,
    WorkspaceError,
    apply_workspace,
    capture_current,
    list_workspaces,
    overlay_config,
    reset_workspace,
    resolve_active_workspace,
    save_active,
    save_workspace,
)
from forex_lab.data import load_cached_ohlcv
from forex_lab.digest import (
    build_digest,
    digest_cfg,
    format_digest_text,
    persist_digest,
)
from forex_lab.explain import SignalExplanation, explain_latest_signal
from forex_lab.retrain import load_champion
from forex_lab.freshness import (
    VALIDITY_CLOSED,
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
    FetchGate,
    YF_MIN_INTERVAL_S,
    assess_ohlcv,
    board_cfg,
    is_rate_limited_reason,
    now_utc,
    should_fetch_ohlcv,
)
from forex_lab.news import NewsBundle, fetch_watchlist_news

st.set_page_config(
    page_title="FX signal screen",
    page_icon="FX",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "FX signal screen — research only. Paper BrokerPort. No live orders.",
    },
)

DISCLAIMER = (
    "**Decision-support only — not financial advice, not auto-trading.** "
    "No live broker APIs. **Paper Buy/Sell/Close** records a local practice fill only "
    "— not a vendor order, not auto-submit, not linked to a real account. "
    "yfinance quotes are **not** executable broker prices. "
    "News can be late, incomplete, or wrong; the bias note is a keyword heuristic on fetched headlines, not a call. "
    "The event calendar is an unofficial Forex Factory weekly dump (cached; fail-soft if offline) — "
    "not an official Fed/BLS/ECB schedule, not a trade instruction. "
    "Advisory cards (no new opens / hold / close / tighten SL) never auto-submit via BrokerPort. "
    "MTF badges use causal higher-TF SMA slope on the same CSV — not a live trend service. "
    "Last on the board is yfinance **last/mid-ish**, not executable bid/ask. "
    "Spread is the **config pip estimate** (cost context), not your broker’s live spread. "
    "Session is a UTC-window clock badge (Asia/London/NY); times on the desk are **Asia/Dhaka**. "
    "Paper uPnL is a local mark vs that last/mid-ish cache — **not** live broker PnL. "
    "Paper RIGHT/WRONG is a local lookback vs cached bars (TP/SL or horizon), not a live edge. "
    "Past backtests do not predict future results. Auto-refresh is **not** broker realtime. "
    "STALE or MISSING data never flashes BUY/SELL as a live call — refresh (Fetch) first. "
    "**Paper BUY/SELL is disabled when Data● is STALE or MISSING** (caption on the board). "
    "The alerts strip flags BUY/SELL/HOLD flips and STALE/MISSING vs the last snapshot — "
    "it never auto-submits via BrokerPort. Optional alert sound is **off by default**. "
    "Risk SL/TP is a research suggestion only — no lot size auto-submit, no live order ticket. "
    "Click a board row for chart / SHAP / news / risk / rationale. "
    "**Workspace** presets (scalp / swing / save-as) switch watchlist pairs, TF, "
    "realtime interval, and min-confidence display only — they do **not** wipe the "
    "paper journal or change BrokerPort. "
    "The **daily digest** is yesterday/today in Asia/Dhaka (freshness, flips, paper "
    "RIGHT/WRONG, calendar, Awareness FAIL/STALE) — not a live edge. "
    "The **champion/challenger retrain gate** promotes a walk-forward challenger only "
    "if PF / total return / max DD improve (else null). Not a live edge."
)

BOARD_HELP = """
This is the **trader screen**: one dense scan board. Click a pair for the detail drawer.

**Columns** — Pair | TF | Signal | Conf | Data● | MTF | Session | Last/mid | Spread | Next event | Spark | actions.

**Pair / TF** — watchlist symbol and lab interval. Click the pair to open chart / SHAP / news / risk / rationale.

**Signal** — BUY green / SELL red / HOLD grey (quieter). Research label, **not** an order. STALE/MISSING flash **—** and the Data● pill outranks the flash.

**Conf** — P(predicted class). Scan aid only.

**Data●** — `OK` / `CLOSED` / `STALE` / `MISSING` / `ERROR`. In a liquid session, a last bar older than ~2× the timeframe is **STALE**. Weekends / Friday after ~21:00 UTC show **CLOSED**. **Paper BUY/SELL is disabled when STALE or MISSING** — refresh (Fetch) first. Optional **gates** (MTF agree / min confidence / event window) can also HOLD the flash and disable paper opens when `gates.enabled` is true (default **off**).

**Last/mid** — cached yfinance close as **last/mid-ish**. Not broker bid/ask.

**Spread** — config `spread_pips` as cost context, not a live broker spread.

**Session** — ASIA / LONDON / NY from UTC windows (`board.sessions`). Overlap LONDON+NY. Desk clocks are **Asia/Dhaka**.

**MTF** — causal higher-TF SMA slope: agree / conflict / n/a. Not a live trend filter.

**Next event** — next high-impact print that hits this pair (countdown). **⚠** when the pre-event window is live — keep that warning visible. Not a trade instruction. Full calendar is an expander.

**Spark** — unicode spark of cached closes. Empty when STALE/MISSING (no invented prices). Sparkline chart lives in the drawer.

**Actions** — Paper BUY / SELL / CLOSE via `BrokerPort` (`broker.backend: paper` only). Disabled on STALE/MISSING with a clear caption. Optional gates (default off) can also disable BUY/SELL. CLOSE still works on an open paper position. SL/TP default from the ATR risk box when available. Practice desk — not a live order.

**Detail drawer** — chart, SHAP/drivers, news context, risk box, rationale, advisory cards (no new opens / hold / close / tighten SL). Advice **never auto-submitted**.

**Event calendar** — unofficial Forex Factory weekly JSON (`nfs.faireconomy.media`). Cached; fail-soft. **Not a trade instruction.**

**Alerts** — compact top strip on BUY/SELL/HOLD flips or STALE/MISSING. Optional event-within-60m. Sound off by default. Times **Asia/Dhaka**. Never places orders.

**Awareness** — every source this desk fetches or observes (watchlist OHLCV, news RSS, model files, calendar, optional FRED). Columns: **Source | Observing | Cadence | Last OK | Status**. STALE/FAIL/MISSING never look OK. Opens itself when anything is unhealthy. Times **Asia/Dhaka**.

**Workspace** — scalp / swing (or a saved custom) switch **pairs, TF, refresh, min conf**. Apply / Save current / Reset. Does **not** rewrite `config/default.yaml`, the paper journal, or BrokerPort. Clocks stay **Asia/Dhaka**.

**Realtime** (default 60s) rebuilds from **local** cache. yfinance at most one pair/tick. Not broker quotes.

**Theme** — dark dense terminal (`.streamlit/config.toml` + CSS). BUY green / SELL red / HOLD grey. STALE amber outranks the flash. Clocks **Asia/Dhaka**. BrokerPort unchanged.

**Last bar / last fetch / last signal** — in the detail drawer. The board shows **board last refreshed**. Desk clocks are **Asia/Dhaka**.

Fetch / Train / Backtest live in the sidebar **Lab** expander. Rows without data/model show **need Fetch/Train**.
"""

DENSE_WEIGHTS = [1.08, 0.42, 0.82, 0.48, 0.58, 0.62, 0.72, 0.88, 0.48, 1.18, 0.82, 1.72]
DENSE_HEADERS = [
    "Pair",
    "TF",
    "Signal",
    "Conf",
    "Data●",
    "MTF",
    "Session",
    "Last/mid",
    "Spread",
    "Next event",
    "Spark",
    "actions",
]


def _init_state() -> None:
    if "logs" not in st.session_state:
        st.session_state.logs = ""
    if "last_action" not in st.session_state:
        st.session_state.last_action = None


def _append_log(title: str, rc: int, log: str) -> None:
    block = f"=== {title}  exit={rc} ===\n{log.strip()}\n"
    prev = st.session_state.get("logs") or ""
    st.session_state.logs = (prev + "\n" + block).strip()
    st.session_state.last_action = title


def _fmt_pct(x: object, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    try:
        return f"{100 * float(x):.{digits}f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_num(x: object, digits: int = 4) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if v == float("inf"):
        return "inf"
    return f"{v:.{digits}f}"


def _run_step(label: str, fn) -> None:
    with st.spinner(f"{label}…"):
        rc, log = fn()
    _append_log(label, rc, log)
    if rc == 0:
        st.sidebar.success(f"{label} finished (exit 0)")
        st.session_state.pop("watch_rows", None)
    else:
        st.sidebar.error(f"{label} failed (exit {rc}). See Logs.")


def _render_explanation(expl: SignalExplanation | None, *, heading: str = "Why this signal?") -> None:
    st.markdown(f"**{heading}**")
    st.caption(
        "Local explanation of the fitted model and config rules on this bar. "
        "Not a broker quote, not an order, and not evidence of a profitable edge."
    )
    if expl is None:
        _empty_inline("No explanation for this row.")
        return
    if expl.error and expl.method == "unavailable":
        st.info(expl.rationale)
        st.caption(f"Detail: {expl.error}")
        return
    st.write(expl.rationale)
    if expl.drivers:
        drv = pd.DataFrame(
            [
                {
                    "feature": d.feature,
                    "contribution": round(d.contribution, 5),
                    "value": None if d.value is None else round(d.value, 5),
                    "meaning": d.hint,
                }
                for d in expl.drivers
            ]
        )
        st.markdown(f"Top drivers (`{expl.method}`)")
        st.dataframe(drv, use_container_width=True, hide_index=True)
    if expl.rules:
        rule_df = pd.DataFrame(
            [
                {
                    "rule": r.name,
                    "enabled": r.enabled,
                    "passed": "—" if r.passed is None else ("yes" if r.passed else "no"),
                    "detail": r.detail,
                }
                for r in expl.rules
            ]
        )
        st.markdown("Human rule overlay (applied after model probabilities)")
        st.dataframe(rule_df, use_container_width=True, hide_index=True)


def _empty_state(title: str, body: str, *, kicker: str = "EMPTY") -> None:
    st.markdown(empty_state_html(title, body, kicker=kicker), unsafe_allow_html=True)


def _empty_inline(text: str) -> None:
    st.markdown(empty_inline_html(text), unsafe_allow_html=True)


def _validity_badge(validity: str, *, compact: bool = False) -> None:
    st.markdown(validity_badge_html(validity, compact=compact), unsafe_allow_html=True)


def _signal_badge(sig: str, *, weak: bool = False, compact: bool = False) -> None:
    st.markdown(signal_badge_html(sig, weak=weak, compact=compact), unsafe_allow_html=True)


def _session_badge(session: SessionState | None, *, show_note: bool = False) -> None:
    if session is None:
        name = "n/a"
        note = "session n/a"
    else:
        name = session.badge()
        note = session.note
    bg = session_fill(name)
    st.markdown(
        f'<div style="background:{bg};color:#fff;font-weight:700;font-size:13px;'
        f"letter-spacing:0.08em;text-align:center;padding:4px 8px;border-radius:3px;"
        f'display:inline-block">{name}</div>',
        unsafe_allow_html=True,
    )
    if show_note and note:
        st.caption(note)


def _render_quote_strip(row) -> None:
    """Last / spread / session — scan in seconds. Not a broker ticker."""
    q: QuoteView | None = getattr(row, "quote", None)
    last_txt = q.last_label() if q is not None else "n/a"
    kind = (q.kind if q is not None else "last/mid-ish") or "last/mid-ish"
    spr = q.spread_label() if q is not None else "n/a"
    st.markdown(
        f'<div style="font-variant-numeric:tabular-nums;line-height:1.15">'
        f'<div style="font-size:1.2rem;font-weight:800;letter-spacing:0.02em;color:{TEXT_BRIGHT}">{last_txt}</div>'
        f'<div style="font-size:14px;color:{MUTED};font-weight:600">{kind}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    s1, s2 = st.columns(2)
    with s1:
        st.markdown(
            f'<div style="background:{SURFACE};color:{TEXT};font-weight:700;font-size:13px;'
            f"letter-spacing:0.04em;text-align:center;padding:3px 6px;border-radius:3px;"
            f'border:1px solid #243040">'
            f"SPR {spr} cfg</div>",
            unsafe_allow_html=True,
        )
    with s2:
        _session_badge(getattr(row, "session", None), show_note=False)
    if q is not None:
        st.caption(q.note)
        if q.range_pips is not None:
            st.caption(f"bar range {q.range_pips:.1f}p · {q.range_note}")


def _mtf_badge(mtf: MtfStatus | None, *, compact: bool = False) -> None:
    if mtf is None:
        status = "n/a"
        note = "n/a" if compact else "MTF n/a"
    else:
        status = str(mtf.status or "n/a")
        note = status if compact else mtf.as_label()
    colors = {MTF_AGREE: BUY, MTF_CONFLICT: WARN, "n/a": HOLD_BG}
    fg = {MTF_AGREE: BUY_BG, MTF_CONFLICT: "#1a1204", "n/a": TEXT}
    bg = colors.get(status, HOLD_BG)
    color = fg.get(status, TEXT)
    st.markdown(
        f'<div style="background:{bg};color:{color};font-weight:800;font-size:13px;'
        f"letter-spacing:0.04em;text-align:center;padding:3px 5px;border-radius:3px;"
        f'display:block">{note}</div>',
        unsafe_allow_html=True,
    )
    if not compact and mtf is not None and mtf.note:
        st.caption(mtf.note)


def _news_bias_badge(bias: str) -> None:
    b = str(bias or "unclear").lower()
    colors = {
        "bullish": BUY,
        "bearish": SELL,
        "mixed": WARN,
        "unclear": HOLD_BG,
    }
    fg = {
        "bullish": BUY_BG,
        "bearish": "#fff",
        "mixed": "#1a1204",
        "unclear": TEXT,
    }
    st.markdown(
        f'<div style="background:{colors.get(b, HOLD_BG)};color:{fg.get(b, TEXT)};font-weight:800;'
        f'padding:4px 8px;border-radius:3px;display:inline-block;letter-spacing:0.08em;'
        f'font-size:13px">{b.upper()}</div>',
        unsafe_allow_html=True,
    )


def _render_sparkline(row, *, height: int = 90) -> None:
    st.caption("Sparkline (cached close)")
    if not getattr(row, "sparkline", None):
        st.caption(getattr(row, "sparkline_note", None) or "n/a")
        return
    chart = pd.DataFrame({"close": list(row.sparkline)})
    st.line_chart(chart, height=height, use_container_width=True)
    st.caption(row.sparkline_note)


def _render_price_chart(row, cfg) -> None:
    """Detail Chart tab: interactive Plotly candlestick from cached OHLCV.

    Scan-board sparkline stays a mini close spark. STALE/MISSING/ERROR never
    invent candles. Not a broker chart.
    """
    blocked = chart_blocked_reason(getattr(row, "validity", None))
    if blocked:
        _empty_state(
            "No candlestick — data is not OK",
            blocked + " The board sparkline stays empty too. Research only — not a broker chart.",
            kicker="CHART",
        )
        return
    _price, _ts, ohlcv = _cached_quote(row, cfg)
    title = f"{row.pair}  {row.timeframe}"
    fig, note = candlestick_figure(
        ohlcv,
        n=candle_bar_count(cfg),
        title=title,
        timeframe=row.timeframe,
        cfg=cfg,
        validity=row.validity,
    )
    if fig is None:
        _empty_state(
            "No candlestick for this pair",
            f"{note}. Fetch OHLCV first. Research only — not a broker chart.",
            kicker="CHART",
        )
        return
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
    )
    st.caption(note)


def _render_alert_strip(state, fresh: list, cfg, *, sound_on: bool) -> None:
    """Dense dismissible banner. Hidden when there is nothing to show."""
    acfg = alerts_cfg(cfg)
    shown = visible_alerts(state, max_visible=int(acfg.get("max_visible") or 6))
    if shown:
        head, clear = st.columns([7.2, 1.1])
        with head:
            st.markdown("**Alerts**")
            st.caption(
                "Flips and STALE vs last snapshot · dismissible · not orders. "
                f"{'Sound on' if sound_on else 'Sound off'}."
            )
        with clear:
            if st.button("Clear", key="alert_clear_all", help="Dismiss every visible alert"):
                for a in list(state.alerts):
                    dismiss_alert(state, a.id)
                save_alert_state(state, cfg)
                st.rerun()
        clicked = None
        for alert in shown:
            msg, xbtn = st.columns([8.6, 0.55])
            with msg:
                bg = kind_tone(alert.kind)
                when = format_alert_time(alert, cfg)
                st.markdown(
                    f'<div style="display:flex;gap:10px;align-items:center;background:{bg};'
                    f"color:#fff;font-size:15px;font-weight:700;padding:7px 10px;"
                    f'border-radius:6px;letter-spacing:0.02em;line-height:1.2">'
                    f"<span>{html.escape(alert.message)}</span>"
                    f'<span style="margin-left:auto;font-weight:600;opacity:.9;white-space:nowrap">'
                    f"{html.escape(when)}</span></div>",
                    unsafe_allow_html=True,
                )
            with xbtn:
                if st.button("×", key=f"alert_dismiss_{alert.id}", help="Dismiss this alert"):
                    clicked = alert.id
        hidden_n = max(0, len([a for a in state.alerts if a.id not in {s.id for s in shown}]) )
        if hidden_n:
            st.caption(f"{hidden_n} older alert(s) not shown — Clear to drop them.")
        if clicked:
            dismiss_alert(state, clicked)
            save_alert_state(state, cfg)
            st.rerun()
    if sound_on and fresh:
        b64 = base64.b64encode(beep_wav()).decode("ascii")
        st.markdown(
            f'<audio autoplay src="data:audio/wav;base64,{b64}"></audio>',
            unsafe_allow_html=True,
        )


def _render_calendar_panel(bundle: CalendarBundle | None, pairs: list[str], cfg=None) -> None:
    st.markdown("**Event calendar**")
    st.caption(
        "High-impact FX releases (NFP, FOMC, CPI, rate decisions, …). "
        "Free unofficial Forex Factory weekly JSON via nfs.faireconomy.media — no API key. "
        "Cached locally; fail-soft if offline. **Not a trade instruction.** "
        f"{clock_note(cfg)}"
    )
    if bundle is None:
        _empty_state(
            "Calendar not loaded this tick",
            "Math board is unchanged. Retry Manual update if the feed was skipped.",
            kicker="CALENDAR",
        )
        return
    if bundle.error and not bundle.events:
        st.warning(f"{bundle.error} — math board is unchanged.")
        return
    if bundle.stale_cache:
        st.warning(bundle.error or "Showing stale calendar cache (live fetch failed).")
    if not bundle.events:
        _empty_state(
            "No high-impact events in the look-ahead window",
            "NFP / FOMC / CPI-style prints will appear here when the unofficial feed has them.",
            kicker="CALENDAR",
        )
        return
    wanted = {str(p).upper() for p in pairs}
    dhaka_now = datetime.now(zoneinfo_for(cfg))
    st.caption(f"Now {fmt_display(dhaka_now, cfg, seconds=True)} — countdowns vs this clock.")
    for e in bundle.events:
        when = e.when_dt()
        cd = countdown_label(when, now=dhaka_now)
        hit = [p for p in wanted if e.currency in {p[:3], p[3:6]} and len(p) >= 6]
        pairs_txt = ", ".join(hit) if hit else "(no watchlist pair)"
        mark = " · highlight" if e.highlight else ""
        st.markdown(
            f"- **{cd}** · {e.currency} · {e.impact}{mark} · {e.title}  \n"
            f"  {fmt_display(when or e.when, cfg)} · pairs {pairs_txt}"
            + (f" · forecast {e.forecast} prev {e.previous}" if e.forecast or e.previous else "")
        )
    src = relabel(bundle.fetched_at, cfg) if bundle.fetched_at else "cache"
    stale = " · stale cache" if bundle.stale_cache else ""
    st.caption(f"Source: {bundle.source} · {src}{stale}")


def _render_suggestions(
    row,
    cfg,
    broker: BrokerPort | None,
    *,
    calendar: CalendarBundle | None,
    news: NewsBundle | None = None,  # noqa: ARG001
    price: float | None,
    ohlcv,
    position,
) -> None:
    events = list(calendar.events) if calendar is not None else []
    cards = suggest_actions(
        pair=row.pair,
        signal=row.raw_signal or row.buy_sell,
        validity=row.validity,
        position=position,
        events=events,
        cfg=cfg,
        ohlcv=ohlcv,
        risk=getattr(row, "risk", None),
        mtf=getattr(row, "mtf", None),
        last_price=price,
    )
    if not cards:
        return
    for i, card in enumerate(cards):
        _render_advice_card(row, cfg, broker, card, price=price, index=i)


def _render_advice_card(
    row,
    cfg,
    broker: BrokerPort | None,
    card: Suggestion,
    *,
    price: float | None,
    index: int,
) -> None:
    st.markdown(
        card_html(
            card.title,
            card.detail,
            kicker=str(card.severity or "info"),
            tone=str(card.severity or "info"),
        ),
        unsafe_allow_html=True,
    )
    if card.countdown or card.event_title:
        when_txt = relabel(getattr(card, "event_when", None), cfg)
        when_bit = f" · {when_txt}" if when_txt != "n/a" else ""
        st.caption(
            f"{card.currencies or ''} {card.event_title or ''} · {card.countdown or ''}"
            f"{when_bit} · window {card.window}"
        )
    st.caption(card.disclaimer)
    if card.auto_submit:
        card.auto_submit = False
    if card.action == "tighten_sl" and card.suggested_sl is not None and broker is not None:
        apply_fn = getattr(broker, "modify_sl", None)
        key = f"paper_sl_{row.pair}_{row.timeframe}_{index}"
        if callable(apply_fn) and st.button(
            f"Apply paper SL {_fmt_num(card.suggested_sl, 5)}",
            key=key,
            use_container_width=True,
            help="Writes a tighter SL on the local paper position. Not a broker modify. Not auto-submit.",
        ):
            pos = position_for_pair(broker, row.pair)
            if not pos:
                st.error("No open paper position to tighten.")
            else:
                try:
                    apply_fn(
                        pos["id"],
                        card.suggested_sl,
                        price=price,
                        note=card.sl_note or "advisory tighten (user click)",
                    )
                    st.session_state["paper_flash"] = (
                        f"Paper SL updated to {_fmt_num(card.suggested_sl, 5)} — local journal only."
                    )
                    st.rerun()
                except BrokerError as exc:
                    st.error(str(exc))


def _render_risk(row) -> None:
    with st.container(border=True):
        st.markdown("**Risk**")
        st.caption("Research suggestion only — not an order. No lot size, no auto-submit, no broker ticket.")
        risk = getattr(row, "risk", None)
        if risk is None or not risk.available:
            reason = getattr(risk, "reason", None) if risk is not None else "n/a"
            st.info(f"n/a — {reason}")
            return
        st.caption(f"Entry  {_fmt_num(risk.entry, 5)}  ·  {risk.entry_ref}")
        st.caption(f"SL  {_fmt_num(risk.sl, 5)}   TP  {_fmt_num(risk.tp, 5)}")
        rr = "n/a" if risk.rr is None else f"{risk.rr:.2f}"
        st.caption(f"R:R  {rr}   (tp_atr={risk.tp_atr} / sl_atr={risk.sl_atr})")
        if risk.spread_pips is not None:
            st.caption(
                f"Spread assumption  {risk.spread_pips:g} pips from config — not your broker’s spread."
            )


def _paper_broker(cfg) -> BrokerPort:
    """UI entry point: always BrokerPort. Never a vendor SDK."""
    b = st.session_state.get("broker")
    if not isinstance(b, BrokerPort):
        b = make_broker(cfg)
        st.session_state.broker = b
    else:
        if hasattr(b, "cfg"):
            b.cfg = cfg  # type: ignore[attr-defined]
        reload = getattr(b, "reload", None)
        if callable(reload):
            reload()
    return b


def _cached_quote(row, cfg):
    ohlcv = load_cached_ohlcv(row.pair, cfg, row.timeframe)
    if ohlcv is not None and not ohlcv.empty and "Close" in ohlcv.columns:
        return float(ohlcv["Close"].iloc[-1]), str(ohlcv.index[-1]), ohlcv
    if row.close:
        try:
            return float(row.close), str(row.last_bar_at or ""), ohlcv
        except (TypeError, ValueError):
            pass
    return None, "", ohlcv


def _driver_snapshot(row) -> str:
    bits = []
    for d in list(row.drivers or [])[:3]:
        try:
            bits.append(f"{d.feature} {d.contribution:+.3f}")
        except Exception:
            bits.append(str(getattr(d, "feature", d)))
    return ", ".join(bits)


def _submit_paper_fill(
    row,
    cfg,
    broker: BrokerPort,
    side: str,
    news: NewsBundle | None,
    calendar: CalendarBundle | None = None,
) -> None:
    """Local practice fill via BrokerPort. STALE/MISSING/gated never submit."""
    events = list(calendar.events) if calendar is not None else []
    clock = now_utc()
    if not paper_submit_allowed(row.validity, row, cfg, events=events, now=clock):
        st.error(
            paper_submit_block_reason(row.validity, row, cfg, events=events, now=clock)
            or PAPER_STALE_CAPTION
        )
        return
    price, entry_bar, ohlcv = _cached_quote(row, cfg)
    if price is None:
        st.error("No cached price for a paper fill.")
        return
    size = float((cfg.get("broker") or {}).get("default_size") or 1.0)
    sl, tp = paper_submit_risk_defaults(ohlcv, cfg, side, row.validity)
    news_bias = ""
    news_note = ""
    if news is not None:
        news_bias = str(news.bias or "")
        if news.headlines:
            news_note = str(news.headlines[0].title or "")[:160]
        elif news.error:
            news_note = f"news fail: {news.error}"[:160]
    try:
        broker.submit(
            side,
            row.pair,
            size=size,
            sl=sl,
            tp=tp,
            price=price,
            timeframe=row.timeframe,
            validity=row.validity,
            model_signal=row.raw_signal or row.buy_sell,
            confidence=row.confidence,
            dir_edge=row.dir_edge,
            p_buy=row.p_buy,
            p_sell=row.p_sell,
            p_hold=row.p_hold,
            rationale=row.rationale or "",
            drivers=_driver_snapshot(row),
            entry_bar_time=entry_bar,
            entry_ref="last close (paper fill; lab path is next-open)",
            horizon=int(cfg.get("horizon") or 8),
            note=f"validity={row.validity}",
            news_bias=news_bias,
            news_note=news_note,
        )
        st.session_state["paper_flash"] = f"Paper {side} recorded @ {_fmt_num(price, 5)} — local journal only."
        st.rerun()
    except BrokerError as exc:
        st.error(str(exc))


def _render_paper_actions(
    row,
    cfg,
    broker: BrokerPort | None,
    news: NewsBundle | None = None,
    calendar: CalendarBundle | None = None,
    *,
    compact: bool = False,
) -> None:
    if broker is None:
        return
    if not compact:
        flash = st.session_state.pop("paper_flash", None)
        warn = st.session_state.pop("paper_flash_warn", None)
        if flash:
            st.success(flash)
        if warn:
            st.warning(warn)
    price, _entry_bar, ohlcv = _cached_quote(row, cfg)
    open_pos = position_for_pair(broker, row.pair)
    events = list(calendar.events) if calendar is not None else []
    clock = now_utc()
    blocked = not paper_submit_allowed(row.validity, row, cfg, events=events, now=clock)
    block_reason = paper_submit_block_reason(row.validity, row, cfg, events=events, now=clock)
    if not compact:
        st.markdown("**Practice desk**")
        backend = str((cfg.get("broker") or {}).get("backend") or "paper")
        st.caption(
            f"backend=`{backend}` — practice in parallel with live markets. "
            "Same submit/close a live venue would use; fills are local until a real backend exists. "
            "Not a broker order. No auto-submit. Advisory cards never place fills. "
            + PAPER_STALE_CAPTION
            + " "
            + PAPER_GATE_CAPTION
        )
        _render_suggestions(
            row,
            cfg,
            broker,
            calendar=calendar,
            news=news,
            price=price,
            ohlcv=ohlcv,
            position=open_pos,
        )
    if open_pos:
        if not compact:
            bars = open_pos.get("bars_held")
            horizon = open_pos.get("horizon")
            held = ""
            if bars is not None and horizon:
                held = f"  ·  lookback {int(bars)}/{int(horizon)} bars"
            st.caption(
                f"Open {open_pos['side']} {float(open_pos['size']):g} @ "
                f"{_fmt_num(open_pos['entry_price'], 5)}  ·  "
                f"uPnL {_fmt_num(open_pos.get('unrealized'), 5)}  ·  "
                f"{open_pos.get('outcome') or 'PENDING'}{held}"
            )
            st.caption("paper mark vs last/mid-ish cache — not live broker PnL")
        close_key = f"paper_close_{row.pair}_{row.timeframe}"
        if st.button(
            "CLOSE" if compact else "Paper CLOSE",
            key=close_key,
            use_container_width=True,
            help="Close the local paper position. Not a broker order.",
        ):
            try:
                if price is None:
                    st.error("No cached price to close against.")
                else:
                    broker.close(open_pos["id"], price=price, reason="manual")
                    st.session_state["paper_flash"] = "Paper position closed (local journal only)."
                    st.rerun()
            except BrokerError as exc:
                st.error(str(exc))
        return
    if price is None:
        if not compact:
            st.caption("n/a — no cached price for a paper fill.")
        return
    help_txt = (
        block_reason
        if blocked
        else "Paper fill at last/mid — local journal only, not a live order. SL/TP from the ATR risk box when available."
    )
    c1, c2 = st.columns(2)
    buy = c1.button(
        "BUY" if compact else "Paper BUY",
        key=f"paper_buy_{row.pair}_{row.timeframe}",
        use_container_width=True,
        disabled=blocked,
        help=help_txt,
    )
    sell = c2.button(
        "SELL" if compact else "Paper SELL",
        key=f"paper_sell_{row.pair}_{row.timeframe}",
        use_container_width=True,
        disabled=blocked,
        help=help_txt,
    )
    if compact and blocked:
        st.caption(block_reason)
    side = "BUY" if buy else ("SELL" if sell else None)
    if side is None:
        return
    _submit_paper_fill(row, cfg, broker, side, news, calendar)


def _dense_cell(text: str, *, warn: bool = False, numeric: bool = False, strong: bool = False) -> None:
    color = WARN if warn else (TEXT_BRIGHT if strong else TEXT)
    weight = "800" if warn or strong else "600"
    size = "16px" if strong else "15px"
    variant = "font-variant-numeric:tabular-nums;" if numeric else ""
    st.markdown(
        f'<div style="font-size:{size};font-weight:{weight};color:{color};'
        f'line-height:1.15;{variant}">{html.escape(str(text))}</div>',
        unsafe_allow_html=True,
    )


def _render_dense_header() -> None:
    cols = st.columns(DENSE_WEIGHTS)
    for col, lab in zip(cols, DENSE_HEADERS):
        col.markdown(
            f'<div class="fx-board-head">{html.escape(lab)}</div>',
            unsafe_allow_html=True,
        )


def _render_dense_row(
    row,
    cfg,
    broker: BrokerPort | None,
    news: NewsBundle | None,
    calendar: CalendarBundle | None,
    *,
    selected: bool,
) -> None:
    warn = bool(getattr(row, "next_event_warn", False))
    blocked = validity_token(getattr(row, "validity", "")) in {
        VALIDITY_STALE,
        VALIDITY_MISSING,
        VALIDITY_ERROR,
    }
    with st.container(border=bool(selected or warn or blocked)):
        c = st.columns(DENSE_WEIGHTS)
        pair_kwargs = {
            "key": f"board_open_{row.pair}_{row.timeframe}",
            "use_container_width": True,
            "help": "Open detail drawer (chart / SHAP / news / risk / rationale)",
        }
        if selected:
            pair_kwargs["type"] = "primary"
        if c[0].button(row.pair, **pair_kwargs):
            cur = st.session_state.get("board_detail_pair")
            st.session_state["board_detail_pair"] = None if cur == row.pair else row.pair
            st.rerun()
        with c[1]:
            _dense_cell(row.timeframe)
        with c[2]:
            _signal_badge(row.buy_sell, weak=bool(getattr(row, "flash_weak", False)), compact=True)
        with c[3]:
            _dense_cell(conf_label(row.confidence), numeric=True, strong=True)
        with c[4]:
            _validity_badge(row.validity, compact=True)
        with c[5]:
            _mtf_badge(getattr(row, "mtf", None), compact=True)
        with c[6]:
            _session_badge(getattr(row, "session", None), show_note=False)
        last = row.quote.last_label() if row.quote is not None else "n/a"
        with c[7]:
            _dense_cell(last, numeric=True, strong=True)
        spread = row.quote.spread_label() if row.quote is not None else "n/a"
        with c[8]:
            _dense_cell(spread, numeric=True)
        with c[9]:
            _dense_cell(row.next_event or "—", warn=warn)
        with c[10]:
            spark = spark_ascii(row.sparkline)
            st.markdown(
                f'<div style="font-size:15px;letter-spacing:-0.06em;line-height:1.25;'
                f'color:{MUTED};font-variant-numeric:tabular-nums">{html.escape(spark)}</div>',
                unsafe_allow_html=True,
            )
        with c[11]:
            _render_paper_actions(row, cfg, broker, news=news, calendar=calendar, compact=True)


def _render_detail_drawer(
    row,
    cfg,
    broker: BrokerPort | None,
    news: NewsBundle | None,
    calendar: CalendarBundle | None,
) -> None:
    """Row-click drawer: chart / SHAP / news / risk / rationale. Board stays clean."""
    with st.container(border=True):
        head, xbtn = st.columns([7.4, 1.0])
        with head:
            st.markdown(
                f'<div class="fx-drawer-head">'
                f'<span class="fx-drawer-pair">{html.escape(row.pair)}</span>'
                f'<span class="fx-tz">{html.escape(row.timeframe)}</span>'
                f"{signal_badge_html(row.buy_sell, compact=True)}"
                f"{validity_badge_html(row.validity, compact=True)}"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.caption(
                "Chart / SHAP / news / risk / rationale. "
                "News context, not a trade instruction. Advisory cards never auto-submit. "
                f"{clock_note(cfg)}"
            )
        with xbtn:
            if st.button("Close", key=f"board_close_drawer_{row.pair}_{row.timeframe}"):
                st.session_state["board_detail_pair"] = None
                st.rerun()
        if getattr(row, "next_event_warn", False):
            st.warning(f"Pre-event warning: {row.next_event}")
        if row.validity == VALIDITY_STALE:
            st.warning(row.signal_details or row.validity_reason or "data stale — refresh required")
        elif row.validity in {VALIDITY_MISSING, VALIDITY_ERROR} or row.status not in {"ready", "stale"}:
            reason = str(row.validity_reason or "").strip()
            detail = str(row.signal_details or "").strip()
            if "failed:" in reason:
                shown = reason if not detail or detail in reason else f"{reason} ({detail})"
            else:
                shown = detail or reason or NEED_FETCH_TRAIN
            st.warning(shown)
        tabs = st.tabs(["Chart", "SHAP", "News", "Risk", "Rationale"])
        with tabs[0]:
            _render_price_chart(row, cfg)
            st.caption(f"Last bar  {row.last_bar_at or 'n/a'}")
            st.caption(f"Last fetch  {row.last_fetch_at or 'n/a'}")
            st.caption(f"Last signal  {row.last_signal_at or row.datetime or 'n/a'}")
            if row.quote is not None:
                st.caption(row.quote.note)
                if row.quote.range_note:
                    st.caption(
                        f"bar range "
                        f"{'n/a' if row.quote.range_pips is None else f'{row.quote.range_pips:.1f}p'}"
                        f" · {row.quote.range_note}"
                    )
            if row.session is not None and row.session.note:
                st.caption(row.session.note)
        with tabs[1]:
            expl = None
            if row.rationale or row.drivers or row.rules:
                expl = SignalExplanation(
                    pair=row.pair,
                    signal=row.buy_sell,
                    raw_signal=row.raw_signal or row.buy_sell,
                    method=row.explain_method or "n/a",
                    drivers=list(row.drivers),
                    rules=list(row.rules),
                    rationale=row.rationale or "",
                    target=row.target,
                )
            _render_explanation(expl, heading="Why this math signal?")
        with tabs[2]:
            _render_news_lane(news, cfg)
        with tabs[3]:
            _render_risk(row)
            if broker is not None:
                price, _, ohlcv = _cached_quote(row, cfg)
                open_pos = position_for_pair(broker, row.pair)
                _render_suggestions(
                    row,
                    cfg,
                    broker,
                    calendar=calendar,
                    news=news,
                    price=price,
                    ohlcv=ohlcv,
                    position=open_pos,
                )
                if open_pos:
                    st.caption(
                        f"Open {open_pos['side']} @ {_fmt_num(open_pos['entry_price'], 5)} · "
                        f"uPnL {_fmt_num(open_pos.get('unrealized'), 5)} · "
                        f"{open_pos.get('outcome') or 'PENDING'} — paper mark vs last/mid-ish, not live broker PnL"
                    )
            cal_events = list(calendar.events) if calendar is not None else []
            if not paper_submit_allowed(row.validity, row, cfg, events=cal_events, now=now_utc()):
                st.caption(
                    paper_submit_block_reason(
                        row.validity, row, cfg, events=cal_events, now=now_utc()
                    )
                    or PAPER_STALE_CAPTION
                )
        with tabs[4]:
            st.caption(
                f"conf={_fmt_num(row.confidence, 4)} · dir_edge={_fmt_num(row.dir_edge, 4)} · "
                f"p_buy={_fmt_num(row.p_buy, 3)} / p_sell={_fmt_num(row.p_sell, 3)} / "
                f"p_hold={_fmt_num(row.p_hold, 3)}"
            )
            if row.datetime:
                st.caption(f"{row.model or ''} · {row.datetime}")
            if row.target_note:
                st.caption(row.target_note)
            if row.rationale:
                st.write(row.rationale)
            if row.raw_signal and row.buy_sell in {"—", "HOLD"}:
                st.caption(f"Last model class (not live / MTF overlay): {row.raw_signal}")
            if row.validity_reason:
                st.caption(f"Validity: {row.validity} — {row.validity_reason}")
            if row.error:
                st.caption(f"Status detail: {row.error}")
            if row.status not in {"ready", "stale"}:
                st.info("Open **Lab** in the sidebar: Fetch then Train. The board does not invent prices.")


def _pct_label(value: object) -> str:
    if value is None:
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if pd.isna(v):
        return "n/a"
    return f"{100 * v:.0f}%"


def _rate_frame(block: list) -> pd.DataFrame:
    df = pd.DataFrame(block)
    for col in ("hit_rate", "error_rate"):
        if col in df.columns:
            df[col] = [_pct_label(x) for x in df[col]]
    return df


def _render_daily_digest(
    cfg,
    *,
    health,
    calendar,
    broker: BrokerPort | None,
    alert_state,
    board_rows,
) -> None:
    """Yesterday/today snapshot. Fail-soft. Paper BrokerPort is only read, never submitted."""
    if digest_cfg(cfg).get("enabled") is False:
        return
    closed = []
    open_rows = []
    if broker is not None:
        closed = list(getattr(broker, "list_closed", lambda: [])())
        try:
            open_rows = list(broker.list_positions())
        except Exception:
            open_rows = []
    alerts = list(getattr(alert_state, "alerts", None) or [])
    try:
        payload = build_digest(
            cfg,
            health_rows=health,
            board_rows=board_rows,
            calendar=calendar,
            alerts=alerts,
            closed_paper=closed,
            open_paper=open_rows,
        )
    except Exception as exc:  # noqa: BLE001 — digest must never break the board
        with st.expander("Daily digest — unavailable", expanded=False):
            st.caption("Fail-soft: digest builder error. Paper BrokerPort unchanged.")
            st.warning(str(exc))
        return
    try:
        persist_digest(payload, cfg)
    except Exception:
        pass
    issues = int((payload.get("awareness") or {}).get("n_unhealthy") or 0)
    paper = payload.get("paper") or {}
    n_flips = len(payload.get("flips") or [])
    expand = bool(paper.get("wrong") or n_flips)
    label = "Daily digest"
    bits = []
    if issues:
        bits.append(f"{issues} FAIL/STALE/MISSING")
    if n_flips:
        bits.append(f"{n_flips} flips")
    bits.append(f"{paper.get('right', 0)}R/{paper.get('wrong', 0)}W paper")
    if bits:
        label += " — " + " · ".join(bits)
    with st.expander(label, expanded=expand):
        st.caption(
            "Yesterday + today in "
            f"**{timezone_tag(cfg)}**. Freshness, signal flips, paper RIGHT/WRONG, "
            "calendar ahead, Awareness FAIL/STALE. "
            "Paper lookback only — **not a live edge**. BrokerPort unchanged."
        )
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("RIGHT", str(paper.get("right", 0)))
        k2.metric("WRONG", str(paper.get("wrong", 0)))
        k3.metric("Flips", str(n_flips))
        k4.metric("FAIL/STALE", str(issues))
        windows = payload.get("windows") or []
        win_bits = ", ".join(f"{w.get('label')} {w.get('local_date')}" for w in windows)
        st.caption(
            f"Generated {payload.get('generated_at') or ''} · {win_bits or 'n/a'} · "
            "paper lookback, not a live edge"
        )
        fresh = list(payload.get("freshness") or [])
        if fresh:
            st.markdown("**Data freshness**")
            st.dataframe(pd.DataFrame(fresh), use_container_width=True, hide_index=True)
        else:
            _empty_inline("No watchlist freshness rows.")
        flips = list(payload.get("flips") or [])
        if flips:
            st.markdown("**Signal flips (BUY/SELL/HOLD)**")
            st.dataframe(pd.DataFrame(flips), use_container_width=True, hide_index=True)
        else:
            _empty_inline("No BUY/SELL/HOLD flips in the window.")
        by_w = list(paper.get("by_window") or [])
        if by_w:
            st.markdown("**Paper RIGHT/WRONG by day**")
            show = pd.DataFrame(by_w)
            if "hit_rate" in show.columns:
                show["hit_rate"] = [_pct_label(x) for x in show["hit_rate"]]
            drop = [c for c in ("note",) if c in show.columns]
            st.dataframe(show.drop(columns=drop, errors="ignore"), use_container_width=True, hide_index=True)
        ahead = list(payload.get("calendar_ahead") or [])
        if payload.get("calendar_error") and not ahead:
            _empty_inline(f"Calendar unavailable: {payload.get('calendar_error')}")
        elif ahead:
            st.markdown("**Calendar ahead**")
            st.dataframe(pd.DataFrame(ahead), use_container_width=True, hide_index=True)
        else:
            _empty_inline("No calendar events in the lookahead window.")
        aw_issues = list((payload.get("awareness") or {}).get("issues") or [])
        if aw_issues:
            st.markdown("**Awareness FAIL/STALE/MISSING**")
            st.dataframe(pd.DataFrame(aw_issues), use_container_width=True, hide_index=True)
        else:
            _empty_inline("No FAIL/STALE/MISSING sources.")
        errs = list(payload.get("errors") or [])
        if errs:
            st.caption("Fail-soft: " + " · ".join(str(e) for e in errs))
        with st.expander("CLI text", expanded=False):
            st.code(format_digest_text(payload), language=None)


def _render_paper_journal(broker: BrokerPort, cfg=None) -> None:
    backend = "paper"
    st.caption(
        f"Practice book via BrokerPort (`broker.backend: {backend}`). "
        "Lookback: RIGHT = TP before SL (or a positive signed move at the horizon); "
        "WRONG = SL first (or a non-positive move at the horizon); "
        "PENDING until enough cached bars pass; FLAT = manual close. "
        "Hit rate is paper-only — not a live edge. "
        "A live mt5/oanda backend would use the same submit/close/list_positions/list_fills "
        "methods — this repo does not store API keys."
    )
    positions = broker.list_positions()
    closed = list(getattr(broker, "list_closed", lambda: [])())
    fills = broker.list_fills()
    unreal = sum(float(p.get("unrealized") or 0) for p in positions)
    realized = sum(float(c.get("realized") or 0) for c in closed)

    agg_fn = getattr(broker, "aggregates", None)
    agg = agg_fn() if callable(agg_fn) else None
    st.markdown("**Paper book stats** (lookback vs cached bars — not live broker PnL)")
    s1, s2, s3, s4, s5 = st.columns(5)
    if agg:
        s1.metric("Hit rate", _pct_label(agg.get("hit_rate")))
        s2.metric("Right", str(agg["right"]))
        s3.metric("Wrong", str(agg["wrong"]))
        s4.metric("Pending", str(agg["n_pending"]))
        s5.metric("Scored", str(agg.get("n_scored") if agg.get("n_scored") is not None else agg["right"] + agg["wrong"]))
        st.caption(
            f"Timeout exits {agg['timeout']} · manual/flat {agg['flat']} · closed {agg['n_closed']} · "
            f"error rate {_pct_label(agg.get('error_rate'))}. "
            "Horizon timeouts are scored RIGHT/WRONG from the signed move."
        )
        for title, key in (
            ("By session", "by_session"),
            ("By confidence bucket", "by_confidence"),
            ("By validity at entry (STALE vs OK)", "by_validity"),
            ("By pair", "by_pair"),
        ):
            block = agg.get(key) or []
            if block:
                st.markdown(f"**{title}**")
                st.dataframe(_rate_frame(block), use_container_width=True, hide_index=True)
        st.markdown("**How to improve** (from this journal — not a live edge)")
        for note in agg.get("notes") or []:
            st.caption("• " + note)
    else:
        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Open positions", str(len(positions)))
        b2.metric("Unrealized (paper)", _fmt_num(unreal, 5))
        b3.metric("Realized (paper)", _fmt_num(realized, 5))
        b4.metric("Fills", str(len(fills)))

    p1, p2 = st.columns(2)
    p1.metric("Unrealized (paper)", _fmt_num(unreal, 5))
    p2.metric("Realized (paper)", _fmt_num(realized, 5))
    st.caption("Paper marks use last/mid-ish cache — not live broker PnL. Times are Asia/Dhaka.")

    if positions:
        st.markdown("**Open book**")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "id": p.get("id"),
                        "pair": p.get("pair"),
                        "side": p.get("side"),
                        "size": p.get("size"),
                        "entry": p.get("entry_price"),
                        "when": relabel(p.get("entry_time"), cfg, seconds=True),
                        "sl": p.get("sl"),
                        "tp": p.get("tp"),
                        "uPnL": p.get("unrealized"),
                        "validity": p.get("validity_at_entry"),
                        "model": p.get("model_signal"),
                        "conf": p.get("confidence"),
                        "session": p.get("session"),
                        "bars": p.get("bars_held"),
                        "horizon": p.get("horizon"),
                        "outcome": p.get("outcome") or "PENDING",
                    }
                    for p in positions
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )

    journal_fn = getattr(broker, "journal", None)
    all_rows = list(journal_fn()) if callable(journal_fn) else list(reversed(closed)) + list(reversed(positions))
    st.markdown("**Mistake review**")
    sessions = ["All"] + sorted({str(r.get("session") or "n/a") for r in all_rows})
    validities = ["All"] + sorted({str(r.get("validity_at_entry") or "n/a") for r in all_rows})
    confs = ["All"] + sorted({str(r.get("conf_bucket") or "n/a") for r in all_rows})
    f1, f2, f3, f4 = st.columns(4)
    wrong_only = f1.checkbox("Wrong trades only", key="paper_wrong_only")
    session_pick = f2.selectbox("Session", sessions, key="paper_session_filter")
    val_pick = f3.selectbox("Validity at entry", validities, key="paper_validity_filter")
    conf_pick = f4.selectbox("Confidence bucket", confs, key="paper_conf_filter")
    rows = filter_journal(
        all_rows,
        wrong_only=wrong_only,
        session=session_pick,
        validity=val_pick,
        conf=conf_pick,
    )
    if not all_rows:
        _empty_state(
            "No paper trades yet",
            "Use BUY / SELL on an OK row. Local practice fill only — BrokerPort unchanged.",
            kicker="PAPER",
        )
    elif not rows:
        _empty_state(
            "No paper trades match these filters",
            "Clear Wrong-only / session / STALE / conf filters to see the full journal.",
            kicker="PAPER",
        )
    else:
        show = pd.DataFrame(
            [
                {
                    "when": relabel(r.get("entry_time"), cfg, seconds=True),
                    "exit_when": relabel(r.get("exit_time"), cfg, seconds=True) if r.get("exit_time") else "",
                    "pair": r.get("pair"),
                    "side": r.get("side"),
                    "status": r.get("status"),
                    "outcome": r.get("outcome"),
                    "entry": r.get("entry_price"),
                    "exit": r.get("exit_price"),
                    "sl": r.get("sl"),
                    "tp": r.get("tp"),
                    "pnl": r.get("realized") if r.get("status") == "closed" else r.get("unrealized"),
                    "validity": r.get("validity_at_entry"),
                    "model": r.get("model_signal"),
                    "conf": r.get("confidence"),
                    "conf_bucket": r.get("conf_bucket"),
                    "session": r.get("session"),
                    "news": r.get("news_bias"),
                    "news_note": r.get("news_note"),
                    "reason": r.get("exit_reason"),
                    "drivers": r.get("drivers"),
                }
                for r in rows
            ]
        )
        st.dataframe(show, use_container_width=True, hide_index=True)
        review = [r for r in rows if str(r.get("outcome")) == "WRONG"] if not wrong_only else rows
        if wrong_only or review:
            st.markdown("**Entry context (wrongs)**")
            for r in review[:12]:
                when = relabel(r.get("entry_time"), cfg, seconds=True)
                st.markdown(
                    f"- **{r.get('pair')} {r.get('side')}**  {when}  "
                    f"signal={r.get('model_signal') or 'n/a'}  "
                    f"conf={r.get('confidence')} ({r.get('conf_bucket') or 'n/a'})  "
                    f"session={r.get('session') or 'n/a'}  "
                    f"news={r.get('news_bias') or 'n/a'}  "
                    f"validity={r.get('validity_at_entry') or 'n/a'}  "
                    f"{(r.get('rationale') or r.get('news_note') or r.get('drivers') or '')[:180]}"
                )
    if fills:
        st.markdown("**Fills** (`BrokerPort.list_fills`)")
        fill_rows = []
        for f in fills:
            d = dict(f)
            if "time" in d:
                d["time"] = relabel(d.get("time"), cfg, seconds=True)
            fill_rows.append(d)
        st.dataframe(pd.DataFrame(fill_rows), use_container_width=True, hide_index=True)


def _render_news_lane(bundle: NewsBundle | None, cfg=None) -> None:
    st.caption("News context, not a trade instruction")
    if bundle is None:
        _empty_inline("No news yet.")
        return
    _news_bias_badge(bundle.bias)
    if bundle.error and not bundle.headlines:
        st.caption(bundle.error + " — math board is unchanged.")
        return
    for bullet in (bundle.bullets or [])[:2]:
        st.caption(bullet)
    for h in (bundle.headlines or [])[:8]:
        title = h.title if len(h.title) < 110 else h.title[:107] + "…"
        if h.link:
            st.markdown(f"- [{title}]({h.link})  \n  {relabel(h.published, cfg)} {('· ' + h.source) if h.source else ''}")
        else:
            st.markdown(f"- {title}")
    if bundle.fetched_at:
        st.caption(f"Source: Google News RSS · {relabel(bundle.fetched_at, cfg)}")


def _watch_cache_key(pair: str, interval: str) -> str:
    return f"{pair.upper()}|{interval}"


def _yf_gate() -> FetchGate:
    gate = st.session_state.get("yf_gate")
    if not isinstance(gate, FetchGate):
        gate = FetchGate()
        st.session_state.yf_gate = gate
    return gate


def _sync_watch_rows(wl, cfg, *, manual: bool, realtime: bool) -> tuple[list, str | None]:
    """Rebuild board rows. yfinance only when due; signals from local cache."""
    import time

    lab_iv = wl.lab_interval(cfg)
    cache: dict = st.session_state.setdefault("watch_rows", {})
    gate = _yf_gate()
    bcfg = board_cfg(cfg)
    min_iv = int(bcfg.get("yf_min_interval_s") or YF_MIN_INTERVAL_S)
    now_ts = time.time()
    clock = now_utc()
    rate_msg = None
    if (manual or realtime) and not gate.allowed(now_ts):
        rate_msg = f"rate limited — backing off until {gate.backoff_until_label()}"

    n = len(wl.pairs)
    stagger_target = gate.next_stagger(n) if realtime and not manual and n else None

    wanted: list[str] = []
    rows = []
    for i, item in enumerate(wl.pairs):
        interval = item.resolved_interval(lab_iv)
        key = _watch_cache_key(item.pair, interval)
        wanted.append(key)
        ohlcv = load_cached_ohlcv(item.pair, cfg, interval)
        fresh = assess_ohlcv(ohlcv, interval, cfg, now=clock)
        last_ok = gate.last_yf_ok.get(str(item.pair).upper())
        do_fetch = False
        if (manual or realtime) and gate.allowed(now_ts):
            due = should_fetch_ohlcv(
                fresh,
                force=bool(manual),
                last_yf_ok_ts=last_ok,
                now_ts=now_ts,
                interval=interval,
                min_interval_s=min_iv,
                realtime=bool(realtime and not manual),
            )
            if due and (manual or i == stagger_target):
                do_fetch = True
        regenerate = bool(manual or realtime or key not in cache)
        row = build_board_row(
            item.pair,
            cfg,
            interval=interval,
            refresh_data=do_fetch,
            regenerate=regenerate,
            now=clock,
            incremental=True,
        )
        if do_fetch:
            src = str(row.data_source or "")
            if src == "yfinance":
                gate.mark_ok(item.pair, now_ts)
            else:
                gate.mark_fail(src, now_ts)
                if is_rate_limited_reason(src):
                    rate_msg = f"rate limited — backing off until {gate.backoff_until_label()}"
        cache[key] = row
        rows.append(row)
    for stale in [k for k in cache if k not in wanted]:
        del cache[stale]
    st.session_state["board_last_refreshed"] = fmt_display(clock, cfg, seconds=True)
    return rows, rate_msg


def _after_workspace_change(ws: Workspace) -> None:
    """Queue widget defaults for the next run — do not mutate live widget keys."""
    pending: dict = {"pick": ws.name}
    if ws.refresh_seconds is not None:
        pending["refresh"] = max(60, int(ws.refresh_seconds))
    if ws.min_confidence is not None:
        pending["min_conf"] = float(ws.min_confidence)
    st.session_state["_ws_pending"] = pending
    st.session_state.pop("watch_rows", None)
    st.rerun()


def _apply_workspace_pending() -> None:
    pending = st.session_state.pop("_ws_pending", None)
    if not pending:
        return
    if pending.get("pick"):
        st.session_state["ws_pick"] = pending["pick"]
    if pending.get("refresh") is not None:
        st.session_state["board_refresh_s"] = int(pending["refresh"])
    if pending.get("min_conf") is not None:
        st.session_state["ws_min_conf"] = float(pending["min_conf"])
        st.session_state["_ws_min_conf_saved"] = float(pending["min_conf"])


def _render_workspace_bar(cfg, wl) -> None:
    """Select / save / reset presets. Paper journal is not touched."""
    _apply_workspace_pending()
    presets = list_workspaces()
    names = [p.name for p in presets]
    labels = {p.name: p.display_label() for p in presets}
    active = resolve_active_workspace()
    current_name = active.name if active is not None else None
    options = names or ["(none)"]
    if "ws_pick" not in st.session_state or st.session_state.get("ws_pick") not in options:
        st.session_state["ws_pick"] = current_name if current_name in options else options[0]
    default_conf = float((cfg.get("signals") or {}).get("min_confidence") or 0.40)
    if active is not None and active.min_confidence is not None:
        default_conf = float(active.min_confidence)
    if "ws_min_conf" not in st.session_state:
        st.session_state["ws_min_conf"] = default_conf
    if "ws_save_as" not in st.session_state:
        st.session_state["ws_save_as"] = "custom"

    c1, c2, c3, c4, c5, c6 = st.columns([1.35, 0.7, 0.7, 1.05, 0.85, 1.15])
    with c1:
        pick = st.selectbox(
            "Workspace",
            options=options,
            format_func=lambda n: labels.get(n, n),
            disabled=not names,
            key="ws_pick",
            help="Scalp / swing (or a saved custom) switch watchlist pairs, TF, "
            "realtime interval, and min-confidence display. Does not wipe the "
            "paper journal or change BrokerPort.",
        )
    apply_clicked = c2.button("Apply", disabled=not names, use_container_width=True)
    reset_clicked = c3.button("Reset", disabled=not names, use_container_width=True)
    with c4:
        save_as = st.text_input(
            "Save as",
            key="ws_save_as",
            help="Slug for a custom preset under data/workspaces/. Not a broker store.",
        )
    save_clicked = c5.button("Save current", use_container_width=True)
    with c6:
        conf = float(
            st.number_input(
                "Min conf",
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                format="%.2f",
                key="ws_min_conf",
                help="Board display floor overlay (signals.min_confidence in memory). "
                "Does not rewrite config/default.yaml.",
            )
        )

    if current_name and "_ws_min_conf_saved" in st.session_state:
        prev = st.session_state.get("_ws_min_conf_saved")
        if prev is not None and abs(float(prev) - conf) > 1e-9:
            try:
                save_active(current_name, min_confidence=conf)
                st.session_state["_ws_min_conf_saved"] = conf
            except WorkspaceError as exc:
                st.error(str(exc))
    elif current_name:
        st.session_state["_ws_min_conf_saved"] = conf

    bits = ["Workspace presets switch **pairs / TF / refresh / min conf** only."]
    if current_name:
        iv = (active.interval if active is not None else None) or wl.lab_interval(cfg)
        bits.append(
            f"Active **{labels.get(current_name, current_name)}** · TF {iv} · "
            f"min conf {conf:.2f}."
        )
    bits.append("Paper journal and BrokerPort stay put. Clocks **Asia/Dhaka**.")
    st.caption(" ".join(bits))

    if apply_clicked and names:
        try:
            ws = next((p for p in presets if p.name == pick), None) or presets[0]
            if current_name == pick and abs(conf - float(ws.min_confidence or conf)) > 1e-9:
                ws.min_confidence = conf
            apply_workspace(ws, cfg=cfg)
            _after_workspace_change(ws)
        except WorkspaceError as exc:
            st.error(str(exc))
    if reset_clicked and names:
        try:
            target = pick if pick in names else (current_name or names[0])
            wl_reset = reset_workspace(str(target), cfg=cfg)
            restored = next((p for p in list_workspaces() if p.name == target), None)
            if restored is None:
                restored = Workspace(
                    name=str(target),
                    pairs=wl_reset.pair_symbols(),
                    interval=wl_reset.interval,
                    refresh_seconds=wl_reset.refresh_seconds,
                    min_confidence=conf,
                )
            _after_workspace_change(restored)
        except WorkspaceError as exc:
            st.error(str(exc))
    if save_clicked:
        try:
            ws = capture_current(
                wl,
                name=str(save_as or "custom"),
                cfg=cfg,
                min_confidence=conf,
            )
            save_workspace(ws)
            apply_workspace(ws, cfg=cfg)
            _after_workspace_change(ws)
        except (WorkspaceError, WatchlistError) as exc:
            st.error(str(exc))


def _render_masthead(cfg) -> None:
    """Compact terminal header. Clocks stay Asia/Dhaka; paper BrokerPort unchanged."""
    now = datetime.now(zoneinfo_for(cfg))
    clock = html.escape(fmt_display(now, cfg, seconds=True))
    tz = html.escape(timezone_tag(cfg))
    st.markdown(
        f'<div class="fx-masthead">'
        f'<div class="fx-masthead-top">'
        f'<div class="fx-masthead-left">'
        f'<span class="fx-brand">FX</span>'
        f'<span class="fx-title">SIGNAL SCREEN</span>'
        f'<span class="fx-chip">PAPER</span>'
        f'<span class="fx-chip muted">RESEARCH</span>'
        f"</div>"
        f'<div class="fx-masthead-right">'
        f'<span class="fx-clock">{clock}</span>'
        f'<span class="fx-tz">{tz}</span>'
        f"</div>"
        f"</div>"
        f"{scan_legend_html()}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_watch_board(cfg) -> None:
    """Top-of-page multi-pair research board + persisted watchlist controls."""
    if st.session_state.pop("watch_clear_typed", False):
        st.session_state["watch_typed_pair"] = ""
    wl = load_watchlist(cfg=cfg, create=True)
    lab_iv = wl.lab_interval(cfg)
    available = ui_pairs(cfg)

    st.markdown(
        section_head_html("Nav", "Workspace", note=f"lab TF {lab_iv} · click a pair for the drawer"),
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        st.caption(
            "BUY green / SELL red / HOLD grey. "
            f"Watchlist: `{watchlist_path()}`. Presets do not wipe the paper journal."
        )
        _render_workspace_bar(cfg, wl)
        with st.expander("Column help (research only)"):
            st.markdown(BOARD_HELP)

        c_real, c_secs, c_sound, c_note = st.columns([1.0, 1.0, 1.15, 2.1])
        realtime = c_real.checkbox(
            "Realtime",
            value=False,
            help="Rebuild signals from local cache on a timer. yfinance only when a bar is due. "
            "Not broker quotes and not streaming.",
        )
        if "alert_sound" not in st.session_state:
            st.session_state["alert_sound"] = bool(load_alert_state(cfg).sound)
        sound_on = c_sound.checkbox(
            "Alert sound",
            key="alert_sound",
            help="Off by default. Short beep when a watchlist pair flips BUY/SELL/HOLD or goes "
            "STALE/MISSING. Never places orders.",
        )
        bcfg = board_cfg(cfg)
        default_rt = int(bcfg.get("realtime_seconds") or max(60, int(wl.refresh_seconds)))
        if "board_refresh_s" not in st.session_state:
            st.session_state["board_refresh_s"] = max(60, int(wl.refresh_seconds) or default_rt)
        seconds = int(
            c_secs.number_input(
                "Refresh (s)",
                min_value=60,
                max_value=3600,
                step=30,
                key="board_refresh_s",
                help="Realtime poll interval. Default 60s so yfinance is not hammered "
                "(unofficial API, no SLA; 1h bars do not need faster OHLCV). "
                "Workspace presets can switch this.",
            )
        )
        if seconds != int(wl.refresh_seconds):
            wl.refresh_seconds = seconds
            save_watchlist(wl)
        if realtime:
            c_note.caption(
                f"Auto-refresh every {seconds}s: signals from cache; OHLCV at most one pair/tick, "
                "only if due. Research timer — not executable prices."
            )
        else:
            c_note.caption("Realtime off: the board stays put until **Manual update**.")

    run_every = seconds if realtime else None

    @st.fragment(run_every=run_every)
    def _board_fragment() -> None:
        manual = False
        b1, b2, b3 = st.columns([1.2, 1.4, 2.4])
        if not realtime:
            if b1.button("Manual update", type="primary", help="Refresh all watchlist pairs once"):
                manual = True
        else:
            b1.caption("Realtime on")
        if b2.button(
            "Update selected",
            help="Force yfinance (short window, merged into cache) + regenerate signals "
            "for pairs that already have a model. Rate-limited with backoff.",
        ):
            manual = True
        with st.spinner("Updating watch board…" if (manual or realtime) else "Loading watch board…"):
            rows, rate_msg = _sync_watch_rows(wl, cfg, manual=manual, realtime=realtime)
        if rate_msg:
            st.warning(rate_msg)

        calendar: CalendarBundle | None = None
        try:
            calendar = calendar_lab.fetch_calendar(cfg, force=bool(manual))
        except Exception as exc:  # noqa: BLE001 — never break the math board
            calendar = CalendarBundle(error=str(exc), notes=["Calendar unavailable."])

        alert_state, alert_fresh = process_watch(
            rows,
            calendar=calendar,
            cfg=cfg,
            now=now_utc(),
            sound=bool(sound_on),
        )
        refreshed = st.session_state.get("board_last_refreshed")
        board_sess = classify_session(cfg=cfg)
        refresh_label = relabel(refreshed, cfg, seconds=True) if refreshed else "—"
        refresh_bit = (
            f"Board last refreshed at {relabel(refreshed, cfg, seconds=True)} "
            f"({timezone_tag(cfg)} clock). "
            if refreshed
            else ""
        )

        news_map: dict = {}
        try:
            news_map = fetch_watchlist_news(
                [r.pair for r in rows],
                cfg,
                force=bool(manual),
            )
        except Exception:
            news_map = {}

        try:
            broker = _paper_broker(cfg)
        except BrokerError as exc:
            st.error(str(exc))
            broker = None
        if broker is not None:
            refresh = getattr(broker, "refresh_from_ohlcv", None)
            if callable(refresh):
                for row in rows:
                    refresh(
                        row.pair,
                        load_cached_ohlcv(row.pair, cfg, row.timeframe),
                        cfg,
                    )

        news_ttl = int((cfg.get("news") or {}).get("cache_ttl_s") or 300)
        cal_ttl = int((cfg.get("calendar") or {}).get("cache_ttl_s") or 1800)
        try:
            fred_status = fred_feed_status(cfg)
        except Exception:  # noqa: BLE001 — health must never break the board
            fred_status = FredStatus(enabled=False, error="status check failed")
        if fred_status is None:
            fred_status = FredStatus(enabled=False)
        try:
            models = model_status_map((r.pair for r in rows), cfg)
        except Exception:  # noqa: BLE001
            models = None
        health = build_health_rows(
            rows,
            news_map=news_map,
            calendar=calendar,
            calendar_ttl_s=cal_ttl,
            fred=fred_status,
            gate=_yf_gate(),
            realtime=realtime,
            refresh_s=seconds,
            news_ttl_s=news_ttl,
            yf_min_interval_s=int(bcfg.get("yf_min_interval_s") or YF_MIN_INTERVAL_S),
            models=models,
        )
        unhealthy = health_unhealthy(health)
        clock = now_utc()
        cal_events = list(calendar.events) if calendar is not None else []

        st.markdown(
            section_head_html("Scan", "Board", note="pair row → detail drawer"),
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            st.markdown(
                scan_strip_html(
                    session=board_sess.badge(),
                    refreshed=refresh_label,
                    tz=timezone_tag(cfg),
                    counts=scan_counts(rows),
                ),
                unsafe_allow_html=True,
            )
            st.caption(
                f"{refresh_bit}"
                f"Session **{board_sess.badge()}** · {board_sess.note}. "
                "Last is yfinance last/mid-ish — not broker bid/ask. "
                "Spread is the config pip estimate (cost context). "
                f"{clock_note(cfg)}"
            )
            if not rows:
                _empty_state(
                    "Watchlist is empty. Add a pair below.",
                    "The board will not invent prices. Fetch / Train live in the Lab sidebar.",
                    kicker="BOARD",
                )
            else:
                for row in rows:
                    attach_next_event(row, cal_events, now=clock, cfg=cfg)
                flash = st.session_state.pop("paper_flash", None)
                if flash:
                    st.success(flash)
                st.caption(
                    "Click a pair for chart / SHAP / news / risk / rationale. "
                    + PAPER_STALE_CAPTION
                    + " "
                    + PAPER_GATE_CAPTION
                )
                _render_dense_header()
                selected = st.session_state.get("board_detail_pair")
                if selected and selected not in {r.pair for r in rows}:
                    selected = None
                    st.session_state["board_detail_pair"] = None
                for row in rows:
                    news = news_map.get(row.pair)
                    _render_dense_row(
                        row,
                        cfg,
                        broker,
                        news,
                        calendar,
                        selected=bool(selected == row.pair),
                    )

        selected = st.session_state.get("board_detail_pair") if rows else None
        if selected and selected not in {r.pair for r in rows}:
            selected = None
        if selected:
            st.markdown(
                section_head_html("Detail", str(selected), note="Chart / SHAP / News / Risk / Rationale"),
                unsafe_allow_html=True,
            )
            open_row = next((r for r in rows if r.pair == selected), None)
            if open_row is not None:
                _render_detail_drawer(
                    open_row,
                    cfg,
                    broker,
                    news_map.get(open_row.pair),
                    calendar,
                )

        st.markdown(
            section_head_html("Health", "Alerts, awareness, digest"),
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            _render_alert_strip(alert_state, alert_fresh, cfg, sound_on=bool(sound_on))
            st.markdown(awareness_status_html(health), unsafe_allow_html=True)
            if unhealthy:
                st.warning(
                    "Obsolete or failing inputs: "
                    + " · ".join(
                        f"{r.get('Source') or r.get('Feed')} {r.get('Status')}" for r in unhealthy
                    )
                )
            exp_label = "Awareness"
            if unhealthy:
                exp_label += " — " + ", ".join(
                    f"{r.get('Source') or r.get('Feed')} {status_token(r)}"
                    for r in unhealthy[:4]
                )
            with st.expander(exp_label, expanded=False):
                st.caption(
                    "Every source this desk fetches or observes. "
                    "STALE / FAIL / MISSING never display as OK. "
                    f"Last OK is {timezone_tag(cfg)}. Paper BrokerPort unchanged."
                )
                st.caption(health_strip(health))
                if health:
                    st.markdown(awareness_table_html(health), unsafe_allow_html=True)
                else:
                    _empty_state(
                        "Watchlist is empty — no sources to report",
                        "Add a pair below. Awareness lists every feed this desk observes.",
                        kicker="AWARENESS",
                    )
            try:
                _render_daily_digest(
                    cfg,
                    health=health,
                    calendar=calendar,
                    broker=broker,
                    alert_state=alert_state,
                    board_rows=rows,
                )
            except Exception:  # noqa: BLE001
                pass

        st.markdown(section_head_html("More", "Calendar, paper, table"), unsafe_allow_html=True)
        with st.container(border=True):
            with st.expander("Event calendar", expanded=False):
                _render_calendar_panel(calendar, [r.pair for r in rows], cfg)
            if broker is not None:
                closed_n = list(getattr(broker, "list_closed", lambda: [])())
                has_book = bool(broker.list_positions() or closed_n)
                with st.expander("Paper portfolio (practice desk — not a broker)", expanded=has_book):
                    _render_paper_journal(broker, cfg)
            if rows:
                table = board_table(rows, events=cal_events, now=clock, cfg=cfg)
                cols_help = " | ".join(BOARD_TABLE_COLS)
                with st.expander(f"Table view ({cols_help})"):
                    try:
                        st.dataframe(style_board(table), use_container_width=True, hide_index=True)
                    except Exception:
                        st.dataframe(table, use_container_width=True, hide_index=True)

        ok_n = sum(1 for r in rows if r.validity == VALIDITY_OK)
        closed_n = sum(1 for r in rows if r.validity == VALIDITY_CLOSED)
        stale_n = sum(1 for r in rows if r.validity == VALIDITY_STALE)
        missing_n = sum(1 for r in rows if r.validity in {VALIDITY_MISSING, VALIDITY_ERROR})
        if rows:
            bits = [f"{ok_n} OK"]
            if closed_n:
                bits.append(f"{closed_n} CLOSED")
            if stale_n:
                bits.append(f"{stale_n} STALE")
            if missing_n:
                bits.append(f"{missing_n} MISSING/ERROR")
            b3.caption(" · ".join(bits))

    _board_fragment()

    st.markdown(
        section_head_html("Watch", "Watchlist", note="add / remove pairs"),
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        watched = wl.pair_symbols()
        addable = [p for p in available if p not in watched]
        a1, a2, a3, a4 = st.columns([1.3, 1.2, 1.4, 1.1])
        with a1:
            pick = st.selectbox(
                "Add pair",
                options=addable or ["(all config pairs are listed)"],
                disabled=not addable,
                key="watch_add_pick",
            )
        with a2:
            typed = st.text_input("Or type a pair", placeholder="EURUSD", key="watch_typed_pair")
        with a3:
            tf_choice = st.selectbox(
                "Pair timeframe",
                options=["lab default (" + lab_iv + ")"] + list(KNOWN_INTERVALS),
                key="watch_add_tf",
            )
        with a4:
            add_clicked = st.button("Add to watchlist", use_container_width=True)
        if add_clicked:
            symbol = (typed or "").strip() or (pick if addable else "")
            try:
                iv = None if str(tf_choice).startswith("lab default") else str(tf_choice)
                add_pair(wl, symbol, interval=iv)
                save_watchlist(wl)
                st.session_state["watch_clear_typed"] = True
                st.session_state.pop("watch_rows", None)
                st.rerun()
            except WatchlistError as exc:
                st.error(str(exc))

        d1, d2, d3 = st.columns([1.6, 1.2, 2.2])
        with d1:
            rm_index = max(len(watched) - 1, 0) if watched else 0
            rm = st.selectbox(
                "Remove pair",
                options=watched or ["(watchlist empty)"],
                index=rm_index,
                disabled=not watched,
                key="watch_remove_" + "-".join(watched) if watched else "watch_remove_empty",
            )
        with d2:
            remove_clicked = st.button(
                "Remove from watchlist",
                disabled=not watched,
                use_container_width=True,
            )
        with d3:
            st.caption("Persisted to disk so the list survives reruns.")
        if remove_clicked and watched:
            remove_pair(wl, str(rm))
            save_watchlist(wl)
            st.session_state.pop("watch_rows", None)
            st.rerun()

    st.divider()


def render() -> None:
    _init_state()
    inject_terminal_css()
    cfg = load_config()
    active_ws = resolve_active_workspace()
    if "ws_min_conf" in st.session_state:
        if active_ws is None:
            active_ws = Workspace(
                name="session",
                min_confidence=float(st.session_state["ws_min_conf"]),
            )
        else:
            active_ws.min_confidence = float(st.session_state["ws_min_conf"])
    cfg = overlay_config(cfg, active_ws)
    pairs = ui_pairs(cfg)
    default_period = str(cfg.get("period") or "2y")
    default_interval = str(cfg.get("interval") or "1h")

    _render_masthead(cfg)
    st.caption(
        "Math analysis + news context so you can decide. Manual only — no auto-trading. "
        "Decision-support only — not financial advice. Paper fills are local. "
        f"{clock_note(cfg)}"
    )
    with st.expander("Disclaimer (research only — not a live edge)", expanded=False):
        st.warning(DISCLAIMER)

    render_watch_board(cfg)

    with st.sidebar:
        st.header("Lab")
        st.caption("Secondary. Open when you need data or a model — not to read signals.")
        st.caption(
            "Decision desk auto-refresh updates watchlist prices only. "
            "Train, backtest, and generate signals stay on the buttons below."
        )
        pair = st.selectbox("Pair", options=pairs, index=0, help="From config/default.yaml")
        status = artifact_status(pair, cfg)
        h1_status = artifact_status(pair, cfg, interval="1h")
        d1_status = artifact_status(pair, cfg, interval="1d")

        def _cache_line(label: str, art: dict) -> str:
            if art.get("data_exists"):
                bars = art.get("n_bars")
                return f"{label}: yes" + (f" ({bars} bars)" if bars is not None else "")
            return f"{label}: missing"

        with st.expander("Fetch / Train / Backtest", expanded=False):
            st.caption(f"Project: `{project_root()}`")
            st.markdown(
                f"- {_cache_line('H1', h1_status)}\n"
                f"- {_cache_line('D1', d1_status)}\n"
                f"- Model: {'yes' if status['model_exists'] else 'missing'}\n"
                f"- Signals CSV: {'yes' if status['signals_exist'] else 'missing'}\n"
                f"- Metrics JSON: {'yes' if status['metrics_exist'] else 'missing'}"
            )
            if not d1_status.get("data_exists"):
                st.caption(
                    "Daily — failed: no OHLCV cache. "
                    "Fetch downloads 1d for this pair, or aggregates it from H1 when the provider has no daily bars."
                )
            st.subheader("Fetch")
            st.caption(
                "Saves H1 and D1 for this pair. Real 1d is downloaded first. "
                "If the provider has no daily bars, D1 is aggregated from the H1 cache. "
                "Synthetic mode writes only the interval below and does not build the other one."
            )
            with st.form("fetch_form"):
                period = st.text_input("Period", value=default_period, help="yfinance period, e.g. 2y")
                interval = st.selectbox(
                    "Interval",
                    options=["1h", "1d", "4h", "15m"],
                    index=["1h", "1d", "4h", "15m"].index(default_interval)
                    if default_interval in ("1h", "1d", "4h", "15m")
                    else 0,
                )
                synthetic = st.checkbox(
                    "Synthetic OHLCV (offline demo)",
                    value=False,
                    help="Do not mix synthetic numbers with yfinance numbers in the same report.",
                )
                fetch_go = st.form_submit_button("Fetch")
            if synthetic and status["data_exists"]:
                st.caption("Synthetic fetch overwrites the existing pair CSV.")
            if fetch_go:
                _run_step(
                    f"Fetch {pair}",
                    lambda: run_fetch(
                        pair, period=period.strip() or None, interval=interval, synthetic=synthetic, cfg=cfg
                    ),
                )
            st.subheader("Train / evaluate")
            st.caption("Backtest is walk-forward and can take several minutes.")
            c1, c2 = st.columns(2)
            if c1.button("Train", use_container_width=True):
                _run_step(f"Train {pair}", lambda: run_train(pair, cfg=cfg))
            if c2.button("Backtest", use_container_width=True):
                _run_step(f"Backtest {pair}", lambda: run_backtest(pair, cfg=cfg))
            if st.button("Generate signals", use_container_width=True):
                _run_step(f"Signals {pair}", lambda: run_signals(pair, cfg=cfg))
            st.subheader("Champion / challenger")
            st.caption(
                "Walk-forward gate. Promotes only if PF, total return, and max DD all improve "
                "(or the non-regression bar). Else **null** — champion stays. "
                "First run seeds the slot (not a promotion). Not a live edge. Can take several minutes."
            )
            try:
                champ = load_champion(pair, cfg)
            except Exception:
                champ = None
            if champ:
                m = champ.get("metrics") or {}
                r1, r2 = st.columns(2)
                r1.metric("PF", _fmt_num(m.get("profit_factor")))
                r2.metric("Return", _fmt_num(m.get("total_return")))
                r3, r4 = st.columns(2)
                r3.metric("Max DD", _fmt_num(m.get("max_drawdown")))
                r4.metric("Trades", str(m.get("n_trades") if m.get("n_trades") is not None else "n/a"))
                st.caption(
                    f"{champ.get('verdict') or 'champion'} · "
                    f"{champ.get('promoted_at_display') or champ.get('promoted_at') or ''} · "
                    "research sample only — not a live edge"
                )
            else:
                st.caption(
                    "No champion yet — **Retrain gate** seeds the slot "
                    "(not a promotion). **Retrain dry-run** compares only and "
                    "does not write champion JSON."
                )
            g1, g2 = st.columns(2)
            if g1.button("Retrain gate", use_container_width=True):
                _run_step(f"Retrain {pair}", lambda: run_retrain(pair, cfg=cfg))
            if g2.button("Retrain dry-run", use_container_width=True):
                _run_step(
                    f"Retrain dry-run {pair}",
                    lambda: run_retrain(pair, dry_run=True, cfg=cfg),
                )
            if st.button("Reload files", use_container_width=True):
                st.rerun()

    signals = load_signals(cfg)
    metrics = load_metrics(cfg)
    trades = load_trades(cfg)
    report = load_report(cfg)

    with st.expander("Research lab — CSV, walk-forward metrics, equity, logs", expanded=False):
        st.caption(
            "Secondary. The flash board above is the trader screen. "
            "These tabs are the same walk-forward artifacts as the CLI."
        )
        disk_pair = None
        if metrics and metrics.get("pair"):
            disk_pair = str(metrics["pair"]).upper()
        elif signals is not None and not signals.empty and "pair" in signals.columns:
            disk_pair = str(signals["pair"].iloc[-1]).upper()
        if disk_pair and disk_pair != pair:
            st.info(
                f"On-disk signals/metrics are for **{disk_pair}**; selected pair is **{pair}**. "
                "Run the pipeline steps for the selected pair to refresh."
            )

        latest_col, *metric_cols = st.columns([1.4, 1, 1, 1, 1, 1])
        with latest_col:
            if signals is not None and not signals.empty:
                last = signals.iloc[-1]
                sig = str(last.get("signal", "n/a")).upper()
                st.metric("Latest signal", sig)
                when = last.get("datetime", "")
                close = last.get("close", "")
                conf = last.get("confidence", "")
                st.caption(
                    f"{last.get('pair', '')}  {relabel(when, cfg)}  close={close}  conf={conf}"
                )
            else:
                st.metric("Latest signal", "—")
                st.caption("No `signals/latest_signals.csv` yet.")

        why = st.container()
        with why:
            if signals is not None and not signals.empty:
                last = signals.iloc[-1]
                why_pair = str(last.get("pair") or pair).upper()
                why_iv = str(cfg.get("interval") or "1h")
                ohlcv = load_cached_ohlcv(why_pair, cfg, why_iv)
                if ohlcv is not None and not ohlcv.empty:
                    tgt, _note = research_target(ohlcv, cfg, str(last.get("signal") or ""))
                    expl = explain_latest_signal(why_pair, ohlcv, cfg, last, target=tgt)
                    st.markdown("**Why this on-disk signal?** (same explainability as the board)")
                    _render_explanation(expl)
                else:
                    st.info(f"No cached OHLCV for {why_pair} — run Fetch.")

        model_m = (metrics or {}).get("model") or {}
        labels = [
            ("Win rate", _fmt_pct(model_m.get("win_rate"))),
            ("Trades", str(model_m.get("n_trades") if model_m.get("n_trades") is not None else "—")),
            ("Total return", _fmt_pct(model_m.get("total_return"))),
            ("Max drawdown", _fmt_pct(model_m.get("max_drawdown"))),
            ("Profit factor", _fmt_num(model_m.get("profit_factor"), 3)),
        ]
        for col, (lab, val) in zip(metric_cols, labels):
            col.metric(lab, val)

        tabs = st.tabs(["Signals", "Metrics", "Equity", "Report", "Logs"])

        with tabs[0]:
            st.subheader("latest_signals.csv")
            st.caption("Newest bar is the first row (highlighted). BUY/SELL/HOLD colors are research labels, not orders.")
            if signals is None or signals.empty:
                st.write("No signals file yet. Run **Generate signals** after fetch + train.")
            else:
                try:
                    st.dataframe(style_signals(signals), use_container_width=True, hide_index=True)
                except Exception:
                    st.dataframe(signals.iloc[::-1], use_container_width=True, hide_index=True)

        with tabs[1]:
            st.subheader("Walk-forward success metrics")
            if not metrics:
                st.write("No `reports/latest_metrics.json` yet. Run **Backtest**.")
            else:
                wr_lo, wr_hi = model_m.get("win_rate_lo"), model_m.get("win_rate_hi")
                st.write(
                    f"Pair **{metrics.get('pair', 'n/a')}** · model `{metrics.get('model_type')}` · "
                    f"scheme `{metrics.get('label_scheme')}` · folds {metrics.get('folds')} · "
                    f"win-rate 95% CI {_fmt_pct(wr_lo)} – {_fmt_pct(wr_hi)}"
                )
                extra = st.columns(3)
                extra[0].metric("Win rate BUY", _fmt_pct(model_m.get("win_rate_buy")))
                extra[1].metric("Win rate SELL", _fmt_pct(model_m.get("win_rate_sell")))
                extra[2].metric("Avg return / trade", _fmt_num(model_m.get("avg_return_per_trade"), 6))
                table = metrics_table(metrics)
                if not table.empty:
                    show = table.copy()
                    for c in ("win_rate", "total_return", "max_drawdown"):
                        if c in show.columns:
                            show[c] = show[c].map(_fmt_pct)
                    if "profit_factor" in show.columns:
                        show["profit_factor"] = show["profit_factor"].map(lambda x: _fmt_num(x, 4))
                    if "avg_return_per_trade" in show.columns:
                        show["avg_return_per_trade"] = show["avg_return_per_trade"].map(
                            lambda x: _fmt_num(x, 6)
                        )
                    st.markdown("**Vs baselines** (same windows, barriers, and costs)")
                    st.dataframe(show, use_container_width=True, hide_index=True)
                stab = metrics.get("fold_stability") or {}
                if stab:
                    st.markdown(
                        f"Fold stability: win rate {_fmt_pct(stab.get('win_rate_mean'))} ± "
                        f"{_fmt_pct(stab.get('win_rate_std'))}; "
                        f"profit factor {_fmt_num(stab.get('profit_factor_mean'), 3)} ± "
                        f"{_fmt_num(stab.get('profit_factor_std'), 3)} "
                        f"(n={stab.get('folds_with_trades')} folds with trades)."
                    )
                takeaway = (
                    "A slightly higher win rate with worse profit factor or deeper drawdown "
                    "than the baseline is not a clear win. Profit factor below 1 means negative expectancy after costs."
                )
                st.caption(takeaway)

        with tabs[2]:
            st.subheader("Equity and drawdown")
            st.caption("From `reports/latest_trades.csv` (compounded net returns of sequential closed trades).")
            if trades is None:
                st.write("No trades CSV yet. Run **Backtest**.")
            else:
                curve = equity_from_trades(trades)
                if curve.empty:
                    st.write("Trades file has no `net_return` column.")
                else:
                    eq_col, dd_col = st.columns(2)
                    with eq_col:
                        st.markdown("Equity (start = 1.0)")
                        st.line_chart(curve[["equity"]], use_container_width=True)
                    with dd_col:
                        st.markdown("Drawdown")
                        st.line_chart(curve[["drawdown"]], use_container_width=True)
                    st.caption(f"{len(trades)} closed trades in the file.")

        with tabs[3]:
            st.subheader("latest_report.md")
            if not report:
                st.write("No report yet. Run **Backtest**.")
            else:
                st.markdown(report)

        with tabs[4]:
            st.subheader("Pipeline logs")
            st.caption("Captured stdout from forex_lab commands (same functions as `python -m forex_lab`).")
            if st.session_state.last_action:
                st.write(f"Last action: **{st.session_state.last_action}**")
            log_text = st.session_state.get("logs") or "(no pipeline steps run in this session)"
            st.code(log_text, language="text")

    st.divider()
    st.caption(
        "CLI is unchanged: `python -m forex_lab fetch|train|backtest|signals`. "
        f"Artifacts live under `{Path(project_root())}` "
        "(data/, models/, signals/, reports/)."
    )


render()
