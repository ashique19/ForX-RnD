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
    "Past backtest metrics do not predict future results."
)


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
    else:
        st.sidebar.error(f"{label} failed (exit {rc}). See Logs.")


def render() -> None:
    _init_state()
    cfg = load_config()
    pairs = ui_pairs(cfg)
    default_period = str(cfg.get("period") or "2y")
    default_interval = str(cfg.get("interval") or "1h")

    st.title("Forex Research Lab")
    st.caption("Local BUY / SELL / HOLD research dashboard. Manual review only.")
    st.warning(DISCLAIMER)

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
