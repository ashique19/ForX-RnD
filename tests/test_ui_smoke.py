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
    html = styled.to_html()
    assert "BUY" in html or "SELL" in html or "HOLD" in html
    assert "#d0e4f7" in html


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


def test_streamlit_app_renders_sample_artifacts():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = project_root() / "streamlit_app.py"
    assert app.exists()
    at = AppTest.from_file(str(app), default_timeout=60)
    at.run()
    assert not at.exception, f"Streamlit render failed: {at.exception}"
    # Disclaimer + sample metrics should be on the page.
    blobs = []
    for attr in ("markdown", "warning", "caption", "text", "title"):
        block = getattr(at, attr, None)
        if block is None:
            continue
        for el in block:
            val = getattr(el, "value", None) or getattr(el, "body", None)
            if val:
                blobs.append(str(val))
    joined = "\n".join(blobs)
    assert "Research only" in joined or "research only" in joined.lower()
    assert "yfinance" in joined.lower() or "broker" in joined.lower()
