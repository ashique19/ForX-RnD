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
    board_table,
    build_board_row,
    research_risk,
    research_target,
    style_board,
)
from forex_lab.session import SessionState, classify_session
from forex_lab.ui.quote import QuoteView
from forex_lab.fred import fred_feed_status
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
from forex_lab.ui.health import build_health_rows, health_strip, health_unhealthy
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
from forex_lab.data import load_cached_ohlcv
from forex_lab.explain import SignalExplanation, explain_latest_signal
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
    "The alerts strip flags BUY/SELL/HOLD flips and STALE/MISSING vs the last snapshot — "
    "it never auto-submits via BrokerPort. Optional alert sound is **off by default**. "
    "Risk SL/TP is a research suggestion only — no lot size auto-submit, no live order ticket."
)

BOARD_HELP = """
This is the **trader screen**: one flash card per watchlist pair.

**Pair / Timeframe** — watchlist symbol and lab interval (`config/default.yaml`).
A per-pair override can be set when adding.

**Validity** — `OK` / `CLOSED` / `STALE` / `MISSING` / `ERROR`.
During a liquid FX session, a last bar older than ~2× the timeframe is **STALE**.
Weekends / Friday after ~21:00 UTC show **CLOSED** (last bar + “market likely closed”),
not a false STALE panic. STALE/MISSING flash **—** with a reason — not a live BUY/SELL.

**Last** — cached yfinance close shown as **last/mid-ish**. Yahoo FX is not a bid/ask
book; this is not your broker’s executable quote. Mid is labeled only when Bid/Ask
columns exist (they do not on the default yfinance path).

**Spread** — `spread_pips` from `config/default.yaml` as **cost context** (same pip
assumption as backtest). Optional last-bar High−Low is a **range proxy**, labeled
as such — not a live spread.

**Session** — Asia / London / NY from the **clock** (UTC windows under `board.sessions`,
overlap shown as LONDON+NY). Weekend / Friday after ~21:00 UTC → CLOSED. Not a
broker session calendar.

**Buy/Sell** — latest model class after the same filters as `python -m forex_lab signals`.
Color badge is a research label, **not** an order. Only flashed when validity is OK or CLOSED.
Optional `board.mtf_confirm.conflict_flash: hold|weaken` can flash HOLD or a weaker badge when higher-TF SMA slope conflicts (default **off** — badge only).

**MTF** — causal higher-TF SMA slope (default 4h of the same pair CSV): **agree / conflict / n/a**. Not a live trend filter.

**Event calendar** — upcoming High-impact FX releases (NFP, FOMC, CPI, rate decisions, …) from the free unofficial Forex Factory weekly JSON (`nfs.faireconomy.media`). Cached locally; fail-soft if offline. Countdown + affected currencies/pairs. **Not a trade instruction.**

**Advice** — cards next to Paper Buy/Sell: no new opens / hold / close / tighten SL from event windows + open paper position + model/MTF. **Never auto-submitted.** Tighten SL uses the ATR risk box at `advice.tighten_sl_atr`. Click **Apply paper SL** or Paper CLOSE yourself.

**Target / Risk** — ATR SL and TP from the same `barrier.tp_atr` / `sl_atr` as labels and backtest,
plus R:R and config spread. Entry is **last close as proxy** when `entry_timing=next_open`.
Research suggestion only: **no lot size, no auto-submit, no live broker order**. HOLD or STALE/MISSING → n/a.

**Sparkline** — last 24–48 cached closes of the row timeframe. Missing/STALE: empty chart + validity badge (no invented prices).

**Paper desk** — Buy / Sell / Close talk only to `BrokerPort` (`broker.backend: paper`).
Fills at the last cached close like a practice book: open position, uPnL, SL/TP hits.
Lookback scores PENDING → RIGHT/WRONG when later bars hit TP/SL or the horizon
(signed move at timeout). The journal shows hit rate by session / confidence /
STALE-vs-OK, filters wrongs, and short “how to improve” notes. **Not** a live
edge and **not** a broker order. A future `mt5` / `oanda` backend would implement
the same four methods.

**Alerts** — compact top strip when a watchlist pair **flips** BUY/SELL/HOLD vs the
previous refresh, or validity becomes **STALE / MISSING**. Last-seen signals persist
in `data/alert_state.json` (local; not a broker). Unchanged polls stay quiet; the same
transition is rate-limited. Optional high-impact **event within 60m** uses the calendar.
Sound is **off by default** (checkbox + `board.alerts.sound`). Times are **Asia/Dhaka**.
Dismissible; never places orders.

**Awareness** — always-visible **Feeds:** line plus expander listing OHLCV + news
with last update, cadence, and OK/STALE/FAIL. Opens itself when a feed is STALE/FAIL/MISSING.

**Last update** — last candle time, last successful CSV write (fetch), last signal time.
The board also shows a global **board last refreshed** timestamp. Desk clocks are **Asia/Dhaka** (`ui.timezone`) with an `Asia/Dhaka` tag. Session windows stay UTC.

**Signal details** — confidence, probabilities, top feature drivers, rule overlay, short rationale.

**News context** — Google News RSS headlines for that pair + a keyword bias
(bullish / bearish / mixed / unclear). Labeled **news context, not a trade instruction**.
Never invented articles. If the feed is down, the math board still works.

**Realtime** (default 60s) rebuilds signals from **local** cache every tick. yfinance is
only called when a bar is due / data is approaching stale, **one pair per tick**, with
cooldown after errors or 429-like failures. That is why the interval is 60–120s — Yahoo
is unofficial and has no SLA. Realtime off: **Manual update** only.

Fetch / Train / Backtest live in the sidebar **Lab** expander.
Rows without cached data or a trained model show **need Fetch/Train** — the board will not invent prices.
"""


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
        st.write("No explanation for this row.")
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


def _validity_badge(validity: str) -> None:
    v = str(validity or "MISSING").upper()
    colors = {
        VALIDITY_OK: "#15803d",
        VALIDITY_CLOSED: "#475569",
        VALIDITY_STALE: "#b45309",
        VALIDITY_MISSING: "#64748b",
        VALIDITY_ERROR: "#b91c1c",
    }
    st.markdown(
        f'<div style="background:{colors.get(v, "#64748b")};color:#fff;font-weight:700;'
        f"font-size:0.8rem;letter-spacing:0.08em;text-align:center;padding:5px 8px;"
        f'border-radius:6px;display:inline-block">{v}</div>',
        unsafe_allow_html=True,
    )


def _signal_badge(sig: str, *, weak: bool = False) -> None:
    s = str(sig).upper() if sig and str(sig).strip() not in {"—", "-", "n/a"} else "—"
    colors = {"BUY": "#15803d", "SELL": "#b91c1c", "HOLD": "#57534e", "—": "#64748b"}
    bg = colors.get(s, "#64748b")
    label = s if not weak or s in {"—", "HOLD"} else f"{s} (weak)"
    size = "1.15rem" if weak and s in {"BUY", "SELL"} else "1.55rem"
    st.markdown(
        f'<div style="background:{bg};color:#fff;font-weight:800;font-size:{size};'
        f"text-align:center;padding:14px 10px;border-radius:10px;letter-spacing:0.12em;"
        f'box-shadow:0 0 0 1px rgba(0,0,0,0.08);opacity:{0.72 if weak else 1}">{label}</div>',
        unsafe_allow_html=True,
    )


def _session_badge(session: SessionState | None, *, show_note: bool = False) -> None:
    if session is None:
        name = "n/a"
        note = "session n/a"
    else:
        name = session.badge()
        note = session.note
    colors = {
        "ASIA": "#3730a3",
        "LONDON": "#1d4ed8",
        "NY": "#0f766e",
        "ASIA+LONDON": "#1e3a8a",
        "LONDON+NY": "#b45309",
        "ASIA+NY": "#6d28d9",
        "ASIA+LONDON+NY": "#b45309",
        "CLOSED": "#475569",
        "OFF": "#57534e",
        "N/A": "#57534e",
    }
    bg = colors.get(name, "#334155")
    st.markdown(
        f'<div style="background:{bg};color:#fff;font-weight:700;font-size:0.75rem;'
        f"letter-spacing:0.08em;text-align:center;padding:4px 8px;border-radius:6px;"
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
        f'<div style="font-size:1.35rem;font-weight:800;letter-spacing:0.02em">{last_txt}</div>'
        f'<div style="font-size:0.72rem;color:#64748b;font-weight:600">{kind}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )
    s1, s2 = st.columns(2)
    with s1:
        st.markdown(
            f'<div style="background:#0f172a;color:#e2e8f0;font-weight:700;font-size:0.72rem;'
            f"letter-spacing:0.04em;text-align:center;padding:4px 6px;border-radius:6px\">"
            f"SPR {spr} cfg</div>",
            unsafe_allow_html=True,
        )
    with s2:
        _session_badge(getattr(row, "session", None), show_note=False)
    if q is not None:
        st.caption(q.note)
        if q.range_pips is not None:
            st.caption(f"bar range {q.range_pips:.1f}p · {q.range_note}")


def _mtf_badge(mtf: MtfStatus | None) -> None:
    if mtf is None:
        status = "n/a"
        note = "MTF n/a"
    else:
        status = str(mtf.status or "n/a")
        note = mtf.as_label()
    colors = {MTF_AGREE: "#166534", MTF_CONFLICT: "#9a3412", "n/a": "#57534e"}
    bg = colors.get(status, "#57534e")
    st.markdown(
        f'<div style="background:{bg};color:#fff;font-weight:700;font-size:0.75rem;'
        f"letter-spacing:0.06em;text-align:center;padding:4px 8px;border-radius:6px;"
        f'display:inline-block">{note}</div>',
        unsafe_allow_html=True,
    )
    if mtf is not None and mtf.note:
        st.caption(mtf.note)


def _news_bias_badge(bias: str) -> None:
    b = str(bias or "unclear").lower()
    colors = {
        "bullish": "#166534",
        "bearish": "#991b1b",
        "mixed": "#a16207",
        "unclear": "#57534e",
    }
    st.markdown(
        f'<div style="background:{colors.get(b, "#57534e")};color:#fff;font-weight:700;'
        f'padding:6px 10px;border-radius:8px;display:inline-block">{b.upper()}</div>',
        unsafe_allow_html=True,
    )


def _render_sparkline(row) -> None:
    st.caption("Sparkline (cached close)")
    if not getattr(row, "sparkline", None):
        st.caption(getattr(row, "sparkline_note", None) or "n/a")
        return
    chart = pd.DataFrame({"close": list(row.sparkline)})
    st.line_chart(chart, height=90, use_container_width=True)
    st.caption(row.sparkline_note)


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
            st.markdown("&nbsp;")
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
                    f"color:#fff;font-size:0.78rem;font-weight:700;padding:5px 8px;"
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
        st.info("Calendar not loaded this tick.")
        return
    if bundle.error and not bundle.events:
        st.warning(f"{bundle.error} — math board is unchanged.")
        return
    if bundle.stale_cache:
        st.warning(bundle.error or "Showing stale calendar cache (live fetch failed).")
    if not bundle.events:
        st.caption("No high-impact events in the look-ahead window.")
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
    colors = {"warn": "#9f1239", "caution": "#b45309", "info": "#334155"}
    bg = colors.get(card.severity, "#334155")
    st.markdown(
        f'<div style="background:{bg};color:#fff;font-weight:700;font-size:0.85rem;'
        f'padding:6px 10px;border-radius:8px;margin-bottom:4px">{card.title}</div>',
        unsafe_allow_html=True,
    )
    st.caption(card.detail)
    if card.countdown or card.event_title:
        when_txt = relabel(getattr(card, "event_when", None), cfg)
        when_bit = f" · {when_txt}" if when_txt != "n/a" else ""
        st.caption(
            f"{card.currencies or ''} {card.event_title or ''} · {card.countdown or ''}"
            f"{when_bit} · window {card.window}"
        )
    st.caption(card.disclaimer)
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


def _render_paper_actions(
    row,
    cfg,
    broker: BrokerPort,
    news: NewsBundle | None = None,
    calendar: CalendarBundle | None = None,
) -> None:
    st.markdown("**Practice desk**")
    backend = str((cfg.get("broker") or {}).get("backend") or "paper")
    st.caption(
        f"backend=`{backend}` — practice in parallel with live markets. "
        "Same submit/close a live venue would use; fills are local until a real backend exists. "
        "Not a broker order. No auto-submit. Advisory cards never place fills."
    )
    flash = st.session_state.pop("paper_flash", None)
    warn = st.session_state.pop("paper_flash_warn", None)
    if flash:
        st.success(flash)
    if warn:
        st.warning(warn)
    price, entry_bar, ohlcv = _cached_quote(row, cfg)
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
        if st.button("Paper CLOSE", key=f"paper_close_{row.pair}_{row.timeframe}", use_container_width=True):
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
        st.caption("n/a — no cached price for a paper fill.")
        return
    size = float((cfg.get("broker") or {}).get("default_size") or 1.0)
    c1, c2 = st.columns(2)
    buy = c1.button("Paper BUY", key=f"paper_buy_{row.pair}_{row.timeframe}", use_container_width=True)
    sell = c2.button("Paper SELL", key=f"paper_sell_{row.pair}_{row.timeframe}", use_container_width=True)
    side = "BUY" if buy else ("SELL" if sell else None)
    if side is None:
        return
    box = research_risk(ohlcv, cfg, side, validity=VALIDITY_OK)
    sl = box.sl if box.available else None
    tp = box.tp if box.available else None
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
        if row.validity == VALIDITY_STALE:
            st.session_state["paper_flash_warn"] = "Recorded on STALE data — scored later; not a live call."
        st.rerun()
    except BrokerError as exc:
        st.error(str(exc))


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
        st.info("No paper trades yet. Use Paper BUY / SELL on a card.")
    elif not rows:
        st.info("No paper trades match these filters.")
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
        st.info("No news yet.")
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


def render_watch_board(cfg) -> None:
    """Top-of-page multi-pair research board + persisted watchlist controls."""
    if st.session_state.pop("watch_clear_typed", False):
        st.session_state["watch_typed_pair"] = ""
    wl = load_watchlist(cfg=cfg, create=True)
    lab_iv = wl.lab_interval(cfg)
    available = ui_pairs(cfg)

    st.subheader("Signal screen")
    st.caption(
        f"Flash BUY / SELL / HOLD for your watchlist. Lab timeframe **{lab_iv}**. "
        f"Watchlist file: `{watchlist_path()}`."
    )
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
    seconds = int(
        c_secs.number_input(
            "Refresh (s)",
            min_value=60,
            max_value=3600,
            value=max(60, int(wl.refresh_seconds) or default_rt),
            step=30,
            help="Realtime poll interval. Default 60s so yfinance is not hammered "
            "(unofficial API, no SLA; 1h bars do not need faster OHLCV).",
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
        _render_alert_strip(alert_state, alert_fresh, cfg, sound_on=bool(sound_on))
        refreshed = st.session_state.get("board_last_refreshed")
        if refreshed:
            st.caption(
                f"Board last refreshed at {relabel(refreshed, cfg, seconds=True)} "
                f"({timezone_tag(cfg)} clock)."
            )
        board_sess = classify_session(cfg=cfg)
        st.caption(
            f"Session **{board_sess.badge()}** · {board_sess.note}. "
            "Last is yfinance last/mid-ish — not broker bid/ask. "
            "Spread is the config pip estimate (cost context). "
            f"{clock_note(cfg)}"
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
            fred_status = None
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
        )
        unhealthy = health_unhealthy(health)
        st.caption(health_strip(health))
        if unhealthy:
            st.warning(
                "Obsolete or failing inputs: "
                + " · ".join(f"{r.get('Feed')} {r.get('Status')}" for r in unhealthy)
            )
        exp_label = "Awareness / data health"
        if unhealthy:
            exp_label += " — " + ", ".join(
                f"{r.get('Feed')} {r.get('Status')}" for r in unhealthy[:4]
            )
        with st.expander(exp_label, expanded=bool(unhealthy)):
            st.caption(
                "v0 — active feeds this board can see (OHLCV per pair + news + event calendar"
                " + FRED when enabled). Full registry / daily digest / weekly retrain stay later."
            )
            if health:
                st.dataframe(pd.DataFrame(health), use_container_width=True, hide_index=True)
            else:
                st.info("Watchlist is empty — no feeds to report.")

        if not rows:
            st.info("Watchlist is empty. Add a pair below.")
        else:
            with st.container(border=True):
                _render_calendar_panel(calendar, [r.pair for r in rows], cfg)
            for row in rows:
                news = news_map.get(row.pair)
                with st.container(border=True):
                    left, mid, right = st.columns([1.15, 1.55, 1.55])
                    with left:
                        st.markdown(f"### {row.pair}")
                        st.caption(f"Timeframe **{row.timeframe}**")
                        _render_quote_strip(row)
                        _validity_badge(row.validity)
                        _signal_badge(row.buy_sell, weak=bool(getattr(row, "flash_weak", False)))
                        _mtf_badge(getattr(row, "mtf", None))
                        st.caption(f"Target  {row.target}")
                        if row.validity == VALIDITY_STALE:
                            st.warning(row.signal_details or row.validity_reason or "data stale — refresh required")
                        elif row.validity == VALIDITY_CLOSED:
                            st.caption(row.validity_reason)
                        elif row.validity in {VALIDITY_MISSING, VALIDITY_ERROR} or row.status not in {
                            "ready",
                            "stale",
                        }:
                            st.warning(row.signal_details)
                    with mid:
                        st.markdown("**Math signal details**")
                        st.caption(f"Last bar  {row.last_bar_at or 'n/a'}")
                        st.caption(f"Last fetch  {row.last_fetch_at or 'n/a'}")
                        st.caption(f"Last signal  {row.last_signal_at or row.datetime or 'n/a'}")
                        st.caption(
                            f"conf={_fmt_num(row.confidence, 4)} · dir_edge={_fmt_num(row.dir_edge, 4)}"
                        )
                        st.caption(
                            f"p_buy={_fmt_num(row.p_buy, 3)} / "
                            f"p_sell={_fmt_num(row.p_sell, 3)} / "
                            f"p_hold={_fmt_num(row.p_hold, 3)}"
                        )
                        if row.datetime:
                            st.caption(f"{row.model or ''} · {row.datetime}")
                        if row.drivers:
                            bits = ", ".join(
                                f"{d.feature} {d.contribution:+.3f}" for d in row.drivers[:3]
                            )
                            st.caption(f"Drivers: {bits}")
                        failed_rules = [
                            r.name for r in (row.rules or []) if getattr(r, "enabled", False) and r.passed is False
                        ]
                        passed_rules = [
                            r.name for r in (row.rules or []) if getattr(r, "enabled", False) and r.passed is True
                        ]
                        if failed_rules:
                            st.caption("Rules blocked: " + ", ".join(failed_rules))
                        elif passed_rules:
                            st.caption("Rules passed: " + ", ".join(passed_rules))
                        if row.raw_signal and row.buy_sell in {"—", "HOLD"} and (
                            row.validity == VALIDITY_STALE or bool(getattr(row, "flash_weak", False))
                            or (row.mtf is not None and row.mtf.status == "conflict")
                        ):
                            st.caption(f"Last model class (not live / MTF overlay): {row.raw_signal}")
                    with right:
                        _render_news_lane(news, cfg)
                    sp, rk = st.columns([1.55, 1.45])
                    with sp:
                        _render_sparkline(row)
                    with rk:
                        _render_risk(row)
                        if broker is not None:
                            _render_paper_actions(row, cfg, broker, news=news, calendar=calendar)
                    with st.expander("Drivers, rules, rationale, headlines"):
                        st.markdown(
                            f"- **Buy/Sell:** {row.buy_sell}  \n"
                            f"- **Last:** {(row.quote.as_table_last() if row.quote else 'n/a')}  \n"
                            f"- **Spread:** {(row.quote.as_table_spread() if row.quote else 'n/a')}  \n"
                            f"- **Session:** {(row.session.badge() if row.session else 'n/a')}  \n"
                            f"- **MTF:** {(row.mtf.as_label() if row.mtf else 'n/a')}  \n"
                            f"- **Target:** {row.target}  \n"
                            f"- **Confidence / edge:** conf={row.confidence} · dir_edge={row.dir_edge}  \n"
                            f"- **p_buy / p_sell / p_hold:** {row.p_buy} / {row.p_sell} / {row.p_hold}"
                        )
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
                        if row.target_note:
                            st.caption(row.target_note)
                        _render_risk(row)
                        _render_sparkline(row)
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
                        if news is not None and news.headlines:
                            st.markdown("**Headlines used for the news note**")
                            _render_news_lane(news, cfg)
                        if row.error:
                            st.caption(f"Status detail: {row.error}")
                        if row.validity_reason:
                            st.caption(f"Validity: {row.validity} — {row.validity_reason}")
                        if row.status not in {"ready", "stale"}:
                            st.info(
                                "Open **Lab** in the sidebar: Fetch then Train. "
                                "The board does not invent prices."
                            )

            if broker is not None:
                closed_n = list(getattr(broker, "list_closed", lambda: [])())
                has_book = bool(broker.list_positions() or closed_n)
                with st.expander("Paper portfolio (practice desk — not a broker)", expanded=has_book):
                    _render_paper_journal(broker, cfg)

            table = board_table(rows)
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

    st.markdown("**Watchlist**")
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
        st.markdown("&nbsp;")
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
        st.markdown("&nbsp;")
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
    cfg = load_config()
    pairs = ui_pairs(cfg)
    default_period = str(cfg.get("period") or "2y")
    default_interval = str(cfg.get("interval") or "1h")

    st.title("FX signal screen")
    st.caption("Math analysis + news context so you can decide. Manual only — no auto-trading.")
    st.warning(DISCLAIMER)

    render_watch_board(cfg)

    with st.sidebar:
        st.header("Lab")
        st.caption("Secondary. Open when you need data or a model — not to read signals.")
        pair = st.selectbox("Pair", options=pairs, index=0, help="From config/default.yaml")
        status = artifact_status(pair, cfg)
        with st.expander("Fetch / Train / Backtest", expanded=False):
            st.caption(f"Project: `{project_root()}`")
            st.markdown(
                f"- Data: {'yes' if status['data_exists'] else 'missing'}"
                + (f" ({status['n_bars']} bars)" if status["n_bars"] is not None else "")
                + f"\n- Model: {'yes' if status['model_exists'] else 'missing'}"
                + f"\n- Signals CSV: {'yes' if status['signals_exist'] else 'missing'}"
                + f"\n- Metrics JSON: {'yes' if status['metrics_exist'] else 'missing'}"
            )
            st.subheader("Fetch")
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
