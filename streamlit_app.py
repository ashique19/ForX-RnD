"""Forex Research Lab — local Streamlit dashboard.

Research only. No broker APIs, no live orders, no auto-trading.
Run from the project root:  streamlit run streamlit_app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from forex_lab.config_loader import load_config
from forex_lab.paths import project_root
from forex_lab.ui.board import (
    NEED_FETCH_TRAIN,
    board_table,
    build_board_row,
    research_target,
    style_board,
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
from forex_lab.news import NewsBundle, fetch_watchlist_news

st.set_page_config(
    page_title="FX signal screen",
    page_icon="FX",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DISCLAIMER = (
    "**Decision-support only — not financial advice, not auto-trading.** "
    "No broker APIs and **no order buttons**. You decide. "
    "yfinance quotes are **not** executable broker prices. "
    "News can be late, incomplete, or wrong; the bias note is a keyword heuristic on fetched headlines, not a call. "
    "Past backtests do not predict future results. Auto-refresh is **not** broker realtime."
)

BOARD_HELP = """
This is the **trader screen**: one flash card per watchlist pair.

**Pair / Timeframe** — watchlist symbol and lab interval (`config/default.yaml`).
A per-pair override can be set when adding.

**Buy/Sell** — latest model class after the same filters as `python -m forex_lab signals`
(`BUY` / `SELL` / `HOLD`). Color badge is a research label, **not** an order.

**Target** — for `triple_barrier`, last close ± ATR × tp/sl. Next-open fill is unknown
on the latest bar, so close is a **proxy**. If barriers cannot be computed: `n/a`.

**Signal details** — confidence, probabilities, top feature drivers, rule overlay, short rationale.

**News context** — Google News RSS headlines for that pair + a keyword bias
(bullish / bearish / mixed / unclear). Labeled **news context, not a trade instruction**.
Never invented articles. If the feed is down, the math board still works.

Realtime / Manual update refresh math (and news if the cache TTL expired).
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


def _signal_badge(sig: str) -> None:
    s = str(sig).upper() if sig and str(sig).strip() not in {"—", "-", "n/a"} else "—"
    colors = {"BUY": "#15803d", "SELL": "#b91c1c", "HOLD": "#57534e", "—": "#64748b"}
    bg = colors.get(s, "#64748b")
    st.markdown(
        f'<div style="background:{bg};color:#fff;font-weight:800;font-size:1.55rem;'
        f"text-align:center;padding:14px 10px;border-radius:10px;letter-spacing:0.12em;"
        f'box-shadow:0 0 0 1px rgba(0,0,0,0.08)">{s}</div>',
        unsafe_allow_html=True,
    )


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


def _render_news_lane(bundle: NewsBundle | None) -> None:
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
            st.markdown(f"- [{title}]({h.link})  \n  {h.published} {('· ' + h.source) if h.source else ''}")
        else:
            st.markdown(f"- {title}")
    if bundle.fetched_at:
        st.caption(f"Source: Google News RSS · {bundle.fetched_at}")


def _watch_cache_key(pair: str, interval: str) -> str:
    return f"{pair.upper()}|{interval}"


def _sync_watch_rows(wl, cfg, *, refresh: bool) -> list:
    lab_iv = wl.lab_interval(cfg)
    cache: dict = st.session_state.setdefault("watch_rows", {})
    wanted: list[str] = []
    rows = []
    for item in wl.pairs:
        interval = item.resolved_interval(lab_iv)
        key = _watch_cache_key(item.pair, interval)
        wanted.append(key)
        if refresh or key not in cache:
            cache[key] = build_board_row(
                item.pair,
                cfg,
                interval=interval,
                refresh_data=refresh,
                regenerate=refresh,
            )
        rows.append(cache[key])
    for stale in [k for k in cache if k not in wanted]:
        del cache[stale]
    return rows


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

    c_real, c_secs, c_note = st.columns([1.1, 1.1, 2.4])
    realtime = c_real.checkbox(
        "Realtime",
        value=False,
        help="Auto-rebuild the board on a timer. Not broker quotes and not streaming.",
    )
    seconds = int(
        c_secs.number_input(
            "Refresh (s)",
            min_value=15,
            max_value=3600,
            value=int(wl.refresh_seconds),
            step=15,
            help="Interval used when Realtime is checked.",
        )
    )
    if seconds != int(wl.refresh_seconds):
        wl.refresh_seconds = seconds
        save_watchlist(wl)
    if realtime:
        c_note.caption(f"Auto-refresh every {seconds}s. Research timer only — not executable prices.")
    else:
        c_note.caption("Realtime off: the board stays put until **Manual update**.")

    run_every = seconds if realtime else None

    @st.fragment(run_every=run_every)
    def _board_fragment() -> None:
        refresh = bool(realtime)
        b1, b2, b3 = st.columns([1.2, 1.4, 2.4])
        if not realtime:
            if b1.button("Manual update", type="primary", help="Refresh all watchlist pairs once"):
                refresh = True
        else:
            b1.caption("Realtime on")
        if b2.button(
            "Update selected",
            help="yfinance + regenerate signals for watchlist pairs that already have a model. "
            "Pairs without data/model stay as need Fetch/Train.",
        ):
            refresh = True
        with st.spinner("Updating watch board…" if refresh else "Loading watch board…"):
            rows = _sync_watch_rows(wl, cfg, refresh=refresh)
        news_map: dict = {}
        try:
            news_map = fetch_watchlist_news(
                [r.pair for r in rows],
                cfg,
                force=bool(refresh and not realtime),
            )
        except Exception:
            news_map = {}

        if not rows:
            st.info("Watchlist is empty. Add a pair below.")
        else:
            for row in rows:
                news = news_map.get(row.pair)
                with st.container(border=True):
                    left, mid, right = st.columns([1.15, 1.55, 1.55])
                    with left:
                        st.markdown(f"### {row.pair}")
                        st.caption(f"Timeframe **{row.timeframe}**")
                        _signal_badge(row.buy_sell)
                        st.caption(f"Target  {row.target}")
                        if row.status != "ready":
                            st.warning(row.signal_details)
                    with mid:
                        st.markdown("**Math signal details**")
                        st.caption(row.signal_details)
                        if row.datetime:
                            st.caption(f"{row.model or ''} · {row.datetime}")
                        if row.drivers:
                            bits = ", ".join(
                                f"{d.feature} {d.contribution:+.3f}" for d in row.drivers[:3]
                            )
                            st.caption(f"Drivers: {bits}")
                    with right:
                        _render_news_lane(news)
                    with st.expander("Drivers, rules, rationale, headlines"):
                        st.markdown(
                            f"- **Buy/Sell:** {row.buy_sell}  \n"
                            f"- **Target:** {row.target}  \n"
                            f"- **Confidence / edge:** conf={row.confidence} · dir_edge={row.dir_edge}  \n"
                            f"- **p_buy / p_sell / p_hold:** {row.p_buy} / {row.p_sell} / {row.p_hold}"
                        )
                        if row.target_note:
                            st.caption(row.target_note)
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
                            _render_news_lane(news)
                        if row.error:
                            st.caption(f"Status detail: {row.error}")
                        if row.status != "ready":
                            st.info(
                                "Open **Lab** in the sidebar: Fetch then Train. "
                                "The board does not invent prices."
                            )

            table = board_table(rows)
            with st.expander("Table view (Pair | Timeframe | Buy/Sell | Target | Signal details)"):
                try:
                    st.dataframe(style_board(table), use_container_width=True, hide_index=True)
                except Exception:
                    st.dataframe(table, use_container_width=True, hide_index=True)

        ready = sum(1 for r in rows if r.status == "ready")
        blocked = len(rows) - ready
        if rows:
            b3.caption(
                f"{ready} ready · {blocked} {NEED_FETCH_TRAIN}" if blocked else f"{ready} ready"
            )

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
                st.caption(f"{last.get('pair', '')}  {when}  close={close}  conf={conf}")
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
