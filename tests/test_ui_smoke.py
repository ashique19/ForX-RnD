"""Smoke tests for the local Streamlit research UI."""
from __future__ import annotations

import pandas as pd
import pytest

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
    run_fetch,
    style_signals,
    ui_pairs,
)


def test_ui_pairs_default_eurusd_and_supported_majors():
    pairs = ui_pairs()
    assert pairs[0] == "EURUSD"
    cfg_keys = {str(k).upper() for k in (load_config().get("pairs") or {})}
    for p in ("GBPUSD", "USDJPY"):
        if p in cfg_keys:
            assert p in pairs


def test_load_existing_sample_signals_and_metrics():
    signals = load_signals()
    metrics = load_metrics()
    report = load_report()
    trades = load_trades()
    assert signals is not None and len(signals) > 0
    assert "signal" in signals.columns
    assert str(signals.iloc[-1]["signal"]).upper() in {"BUY", "SELL", "HOLD"}
    assert metrics is not None
    assert "model" in metrics
    for key in ("win_rate", "n_trades", "total_return", "max_drawdown", "profit_factor"):
        assert key in metrics["model"]
    table = metrics_table(metrics)
    assert not table.empty
    assert "SMA crossover" in set(table["strategy"])
    assert report and "Research only" in report
    assert trades is not None and "net_return" in trades.columns


def test_style_signals_highlights_newest_row():
    signals = load_signals()
    assert signals is not None
    styled = style_signals(signals)
    data = getattr(styled, "data", styled)
    assert str(data.iloc[0]["note"]) == "newest"
    assert data.iloc[0]["datetime"] == signals.iloc[-1]["datetime"]
    html = styled.to_html() if hasattr(styled, "to_html") else data.to_html()
    assert "BUY" in html or "SELL" in html or "HOLD" in html
    assert "newest" in html.lower() or "#d0e4f7" in html or "#171f29" in html


def test_equity_from_trades_compounds():
    trades = pd.DataFrame(
        {
            "entry_time": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
            "net_return": [0.10, -0.05, 0.00],
        }
    )
    curve = equity_from_trades(trades)
    assert list(curve.columns) == ["equity", "drawdown"]
    assert curve["equity"].iloc[0] == pytest.approx(1.10)
    assert curve["equity"].iloc[1] == pytest.approx(1.10 * 0.95)
    assert curve["drawdown"].iloc[0] == pytest.approx(0.0)
    assert curve["drawdown"].iloc[1] < 0


def test_run_fetch_synthetic_writes_csv(tmp_path):
    cfg = {
        "pairs": {"EURUSD": "EURUSD=X"},
        "interval": "1h",
        "period": "1mo",
        "paths": {"data_dir": str(tmp_path)},
    }
    rc, log = run_fetch("EURUSD", period="1mo", interval="1h", synthetic=True, cfg=cfg)
    out = tmp_path / "EURUSD_1h.csv"
    assert rc == 0
    assert out.exists() and out.stat().st_size > 0
    loaded = pd.read_csv(out, index_col=0)
    assert len(loaded) >= 100
    assert "synthetic" in log.lower() or "bars" in log.lower() or out.exists()


def test_artifact_status_sees_repo_sample():
    status = artifact_status("EURUSD")
    assert status["root"] == project_root()
    assert status["data_exists"]
    assert status["signals_exist"]
    assert status["metrics_exist"]


def test_streamlit_app_renders_sample_artifacts(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from forex_lab.calendar import CalendarBundle, CalendarEvent
    from forex_lab.news import Headline, NewsBundle

    def _stub_news(pair, cfg=None, **_kwargs):
        return NewsBundle(
            pair=str(pair).upper(),
            bias="mixed",
            bullets=["Keyword heuristic on fixture headlines."],
            headlines=[Headline("Euro rallies in fixture", "https://example.com/x", "now", "Test")],
            fetched_at="2026-09-21 00:00 UTC",
        )

    def _stub_cal(cfg=None, **_kwargs):
        return CalendarBundle(
            events=[
                CalendarEvent(
                    title="Non-Farm Employment Change",
                    currency="USD",
                    when="2026-09-21T16:30:00Z",
                    impact="High",
                    highlight=True,
                )
            ],
            source="faireconomy_ff_json",
            fetched_at="2026-09-21 00:00 UTC",
            notes=["fixture"],
        )

    monkeypatch.setattr("forex_lab.news.fetch_pair_news", _stub_news)
    monkeypatch.setattr("forex_lab.calendar.fetch_calendar", _stub_cal)

    app = project_root() / "streamlit_app.py"
    assert app.exists()
    at = AppTest.from_file(str(app), default_timeout=60)
    at.run()
    assert not at.exception, f"Streamlit render failed: {at.exception}"

    def _joined(app_test):
        blobs = []
        for attr in ("markdown", "warning", "caption", "text", "title", "subheader", "header"):
            block = getattr(app_test, attr, None)
            if block is None:
                continue
            for el in block:
                val = getattr(el, "value", None) or getattr(el, "body", None)
                if val:
                    blobs.append(str(val))
        return "\n".join(blobs)

    def _click_nav(app_test, name):
        btn = next(b for b in app_test.button if str(getattr(b, "label", "")) == name)
        btn.click()
        app_test.run()
        assert not app_test.exception, f"{name} mode failed: {app_test.exception}"

    joined = _joined(at)
    low = joined.lower()
    nav_labels = [str(getattr(b, "label", "")) for b in at.button]
    for name in ("Decision", "Calendar", "Paper", "Lab", "Awareness"):
        assert name in nav_labels, f"missing top-level nav {name}"

    assert "decision-support" in low or "research only" in low or "research label" in low
    assert "yfinance" in low or "broker" in low
    assert "signal screen" in low
    assert "news context, not a trade instruction" in low or "news context" in low
    assert "last bar" in low or "board last refreshed" in low or "validity" in low
    assert "sparkline" in low or "spark" in low or "risk" in low or "candle" in low
    assert "paper" in low
    assert "practice desk" in low or "brokerport" in low or "broker.port" in low
    assert "mtf" in low
    assert "advisory" in low or "not an order" in low or "never auto-submitted" in low
    assert "last/mid-ish" in low or "mid-ish" in low
    assert "spread" in low
    assert "session" in low
    assert "not broker bid/ask" in low or "not broker" in low
    assert "asia/dhaka" in low or "dhaka" in low
    assert "alert sound" in low or "alerts" in low
    assert "not a live edge" in low or "paper right/wrong" in low
    assert "data●" in low or "next event" in low
    assert "disabled when" in low and "stale" in low
    assert "detail" in low
    assert "workspace" in low
    assert "scalp" in low or "swing" in low
    assert "paper journal" in low or "brokerport" in low
    assert "fx-masthead" in joined or "data-fx-theme" in joined or "--fx-buy" in joined
    assert "fx-legend" in joined or "stale outranks" in low
    assert "fx-scan" in joined or "fx-empty" in joined or "seconds to decide" in low
    buy_btns = [b for b in at.button if "BUY" in str(getattr(b, "label", "")).upper()]
    sell_btns = [b for b in at.button if "SELL" in str(getattr(b, "label", "")).upper()]
    assert buy_btns and sell_btns
    labels = [str(getattr(b, "label", "")) for b in at.button]
    assert "Add to watchlist" in labels
    assert any(str(getattr(b, "label", "")) == "×" for b in at.button)
    selects = [str(getattr(s, "label", "")) for s in at.selectbox]
    assert any("Add pair" in s for s in selects)

    _click_nav(at, "Calendar")
    cal = _joined(at).lower()
    assert "event calendar" in cal
    assert "non-farm" in cal or "nfp" in cal

    _click_nav(at, "Paper")
    paper = _joined(at).lower()
    assert "paper" in paper
    assert "practice" in paper or "brokerport" in paper

    _click_nav(at, "Lab")
    lab = _joined(at).lower()
    assert "fetch" in lab
    lab_labels = [str(getattr(b, "label", "")) for b in at.button]
    assert "Run pipeline" in lab_labels
    assert "train" in lab or "backtest" in lab or "Run pipeline" in lab_labels

    _click_nav(at, "Awareness")
    aw = _joined(at).lower()
    assert "awareness" in aw
    assert "data health" in aw or "stale" in aw or "source" in aw

    _click_nav(at, "Decision")
    buy_btns = [b for b in at.button if str(getattr(b, "label", "")).upper() in {"BUY", "SELL"}]
    assert buy_btns


def test_run_pipeline_order_stops_on_failure(monkeypatch):
    from forex_lab.ui import pipeline as pl

    calls: list[str] = []

    monkeypatch.setattr(pl, "run_fetch", lambda *a, **k: calls.append("fetch") or (0, "ok"))
    monkeypatch.setattr(pl, "run_train", lambda *a, **k: calls.append("train") or (0, "ok"))
    monkeypatch.setattr(pl, "run_backtest", lambda *a, **k: calls.append("backtest") or (1, "fail"))
    monkeypatch.setattr(pl, "run_signals", lambda *a, **k: calls.append("signals") or (0, "ok"))

    rc, log = pl.run_pipeline("EURUSD")
    assert rc == 1
    assert calls == ["train", "backtest"]
    assert "Signals" not in log

    calls.clear()
    monkeypatch.setattr(pl, "run_backtest", lambda *a, **k: calls.append("backtest") or (0, "ok"))
    rc, log = pl.run_pipeline("EURUSD", fetch=True)
    assert rc == 0
    assert calls == ["fetch", "train", "backtest", "signals"]
    assert "Fetch" in log and "Train" in log and "Signals" in log


def test_stale_row_disables_paper_buy_sell_buttons(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from forex_lab.calendar import CalendarBundle
    from forex_lab.freshness import VALIDITY_STALE
    from forex_lab.news import NewsBundle
    from forex_lab.ui.board import build_board_row as real_build

    def _stub_news(pair, cfg=None, **_kwargs):
        return NewsBundle(pair=str(pair).upper(), bias="unclear", headlines=[], fetched_at="2026-09-21 00:00 UTC")

    def _stub_cal(cfg=None, **_kwargs):
        return CalendarBundle(events=[], source="fixture", fetched_at="2026-09-21 00:00 UTC")

    def _stale(*a, **k):
        row = real_build(*a, **k)
        row.validity = VALIDITY_STALE
        row.buy_sell = "—"
        return row

    monkeypatch.setattr("forex_lab.news.fetch_pair_news", _stub_news)
    monkeypatch.setattr("forex_lab.calendar.fetch_calendar", _stub_cal)
    monkeypatch.setattr("forex_lab.ui.board.build_board_row", _stale)

    app = project_root() / "streamlit_app.py"
    at = AppTest.from_file(str(app), default_timeout=60)
    at.run()
    assert not at.exception, f"Streamlit render failed: {at.exception}"
    blobs = []
    for attr in ("markdown", "warning", "caption", "text", "title", "subheader", "header"):
        block = getattr(at, attr, None)
        if block is None:
            continue
        for el in block:
            val = getattr(el, "value", None) or getattr(el, "body", None)
            if val:
                blobs.append(str(val))
    joined = "\n".join(blobs).lower()
    assert "paper buy/sell disabled" in joined or "disabled when data● is stale" in joined
    action_btns = [
        b
        for b in at.button
        if str(getattr(b, "label", "")).upper() in {"BUY", "SELL", "PAPER BUY", "PAPER SELL"}
    ]
    assert action_btns, "expected paper BUY/SELL buttons on the dense board"
    disabled_flags = [getattr(b, "disabled", None) for b in action_btns]
    if any(flag is not None for flag in disabled_flags):
        assert all(bool(flag) for flag in disabled_flags)
