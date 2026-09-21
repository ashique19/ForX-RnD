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

st.set_page_config(
    page_title="Forex Research Lab",
    page_icon="FX",
    layout="wide",
    initial_sidebar_state="expanded",
)

DISCLAIMER = (
    "**Research only — not financial advice.** "
    "This dashboard does **not** place live orders and has **no broker APIs**. "
    "yfinance quotes are **not** broker executable prices (spreads, liquidity, and session gaps differ). "
    "Past backtest metrics do not predict future results. "
    "Watch-board auto-refresh is a research timer, **not** broker realtime."
)

BOARD_HELP = """
**Pair** — watchlist symbol (e.g. EURUSD).

**Timeframe** — bar interval used for that row. Default is the lab interval from
`config/default.yaml` (fetch settings). A per-pair override can be set when adding.

**Buy/Sell** — latest model class after the same filters as `python -m forex_lab signals`
(`BUY` / `SELL` / `HOLD`). This is a research label, not an order.

**Target** — best-effort from the lab labeling scheme. For `triple_barrier`, last close
± ATR × tp/sl (next-open fill is unknown on the latest bar, so close is a **proxy**).
If barriers cannot be computed: `n/a` plus scheme / horizon in the row details.

**Signal details** — confidence, dir_edge, p_buy / p_sell / p_hold, model name, signal
datetime. If data or the model is missing the cell is **need Fetch/Train** — the board
will not invent prices.

Realtime ticks and **Manual update** try yfinance only when a model already exists, then
regenerate signals in memory (they do **not** overwrite `signals/latest_signals.csv`).
The sidebar Fetch / Train / Backtest / Generate signals tools remain the pipeline.
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
    wl = load_watchlist(cfg=cfg, create=True)
    lab_iv = wl.lab_interval(cfg)
    available = ui_pairs(cfg)

    st.subheader("Watch board")
    st.caption(
        f"One row per selected pair. Lab timeframe **{lab_iv}** "
        f"(from config / fetch settings). File: `{watchlist_path()}`."
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
        table = board_table(rows)
        if table.empty:
            st.info("Watchlist is empty. Add a pair below.")
        else:
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
        for row in rows:
            title = f"{row.pair} · {row.timeframe} · {row.buy_sell}"
            with st.expander(title):
                st.write(
                    {
                        "status": row.status,
                        "buy_sell": row.buy_sell,
                        "target": row.target,
                        "target_note": row.target_note,
                        "confidence": row.confidence,
                        "dir_edge": row.dir_edge,
                        "p_buy": row.p_buy,
                        "p_sell": row.p_sell,
                        "p_hold": row.p_hold,
                        "model": row.model,
                        "datetime": row.datetime,
                        "close": row.close,
                        "raw_signal": row.raw_signal,
                        "data_source": row.data_source,
                        "n_bars": row.n_bars,
                        "error": row.error,
                    }
                )
                if row.status != "ready":
                    st.warning(
                        f"{row.pair}: {row.signal_details}. "
                        "Use the sidebar **Fetch** then **Train** (then this board’s update). "
                        "The board does not invent prices."
                    )

    _board_fragment()

    st.markdown("**Watchlist**")
    add_c, del_c = st.columns(2)
    watched = wl.pair_symbols()
    with add_c:
        addable = [p for p in available if p not in watched]
        pick = st.selectbox(
            "Add pair",
            options=addable or ["(all config pairs are listed)"],
            disabled=not addable,
        )
        typed = st.text_input("Or type a pair", value="", placeholder="EURUSD")
        tf_choice = st.selectbox(
            "Pair timeframe",
            options=["lab default (" + lab_iv + ")"] + list(KNOWN_INTERVALS),
        )
        if st.button("Add to watchlist"):
            symbol = (typed or "").strip() or (pick if addable else "")
            try:
                iv = None if tf_choice.startswith("lab default") else tf_choice
                add_pair(wl, symbol, interval=iv)
                save_watchlist(wl)
                st.session_state.pop("watch_rows", None)
                st.rerun()
            except WatchlistError as exc:
                st.error(str(exc))
    with del_c:
        rm = st.selectbox(
            "Remove pair",
            options=watched or ["(watchlist empty)"],
            disabled=not watched,
        )
        st.caption("Persisted to disk so the list survives reruns.")
        if st.button("Remove from watchlist", disabled=not watched) and watched:
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

    st.title("Forex Research Lab")
    st.caption("Local BUY / SELL / HOLD research dashboard. Manual review only.")
    st.warning(DISCLAIMER)

    render_watch_board(cfg)

    with st.sidebar:
        st.header("Pipeline")
        pair = st.selectbox("Pair", options=pairs, index=0, help="From config/default.yaml")
        status = artifact_status(pair, cfg)
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
