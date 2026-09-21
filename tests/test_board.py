"""Watch-board row building (research UI; no broker calls)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv, try_yfinance_refresh
from forex_lab.ui.board import (
    NEED_FETCH_TRAIN,
    board_table,
    build_board_row,
    build_board_rows,
    research_target,
    row_from_signal,
)
from forex_lab.ui.watchlist import WatchItem, Watchlist


def _tmp_cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "pairs": {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X"},
        "interval": "1h",
        "period": "1mo",
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "entry_timing": "next_open",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
        "model": {"type": "xgboost"},
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "models_dir": str(tmp_path / "models"),
            "signals_dir": str(tmp_path / "signals"),
            "reports_dir": str(tmp_path / "reports"),
        },
    }
    cfg.update(extra)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "models").mkdir(exist_ok=True)
    return cfg


def test_research_target_triple_barrier_uses_last_close_proxy():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    cfg = load_config()
    compact, note = research_target(df, cfg, "BUY")
    assert compact.startswith("TP ")
    assert "SL " in compact
    assert "proxy" in note.lower()
    assert "next_open" in note or "next open" in note.lower()
    hold, _ = research_target(df, cfg, "HOLD")
    assert hold.startswith("upper ")


def test_research_target_other_scheme_is_na():
    df = generate_synthetic_ohlcv(bars=40, seed=1)
    compact, note = research_target(df, {"label_scheme": "forward_return", "horizon": 4}, "BUY")
    assert compact.startswith("n/a")
    assert "forward_return" in compact
    assert "horizon=4" in compact
    assert "barrier" in note.lower()


def test_row_from_signal_fills_table_fields():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    last = {
        "datetime": "2024-01-02 15:00:00",
        "signal": "SELL",
        "raw_signal": "SELL",
        "confidence": 0.51,
        "dir_edge": 0.22,
        "p_buy": 0.20,
        "p_sell": 0.42,
        "p_hold": 0.38,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, load_config())
    assert row.buy_sell == "SELL"
    assert row.timeframe == "1h"
    assert row.status == "ready"
    assert "TP " in row.target
    assert "conf=0.5100" in row.signal_details
    assert "xgboost" in row.signal_details
    table = board_table([row])
    assert list(table.columns) == ["Pair", "Timeframe", "Buy/Sell", "Target", "Signal details"]
    assert table.iloc[0]["Buy/Sell"] == "SELL"


def test_board_row_missing_artifacts_need_fetch_train(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    row = build_board_row("GBPUSD", cfg, refresh_data=False)
    assert row.buy_sell == "—"
    assert row.target == "n/a"
    assert NEED_FETCH_TRAIN in row.signal_details
    assert row.status == "need_fetch"


def test_refresh_skips_yfinance_when_model_missing(tmp_path, monkeypatch):
    called = []

    def _boom(*_a, **_k):
        called.append(True)
        return None, "should not run"

    monkeypatch.setattr("forex_lab.ui.board.try_yfinance_refresh", _boom)
    cfg = _tmp_cfg(tmp_path)
    generate_synthetic_ohlcv(pair="GBPUSD", bars=120, seed=1).to_csv(
        tmp_path / "data" / "GBPUSD_1h.csv"
    )
    row = build_board_row("GBPUSD", cfg, refresh_data=True, regenerate=True)
    assert called == []
    assert row.status == "need_train"
    assert NEED_FETCH_TRAIN in row.signal_details


def test_yfinance_refresh_does_not_write_synthetic(tmp_path, monkeypatch):
    cfg = _tmp_cfg(tmp_path)
    csv = tmp_path / "data" / "EURUSD_1h.csv"
    original = "Datetime,Open,High,Low,Close,Volume\n2024-01-01,1.1,1.2,1.0,1.15,1\n"
    csv.write_text(original, encoding="utf-8")

    fake = types.ModuleType("yfinance")

    def _download(*_a, **_k):
        raise RuntimeError("network down")

    fake.download = _download
    monkeypatch.setitem(sys.modules, "yfinance", fake)

    df, reason = try_yfinance_refresh("EURUSD", cfg, interval="1h")
    assert df is None
    assert "failed" in reason.lower() or "yfinance" in reason.lower()
    assert csv.read_text(encoding="utf-8") == original
    assert "synthetic" not in csv.read_text(encoding="utf-8").lower()


def test_board_row_eurusd_uses_existing_signals_csv():
    row = build_board_row("EURUSD", refresh_data=False, regenerate=False)
    assert row.status == "ready"
    assert row.pair == "EURUSD"
    assert row.timeframe == "1h"
    assert row.buy_sell in {"BUY", "SELL", "HOLD"}
    assert row.target != ""
    assert "conf=" in row.signal_details
    assert row.model
    assert row.rationale
    assert row.explain_method


def test_build_board_rows_mixed_status(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    wl = Watchlist(pairs=[WatchItem("GBPUSD"), WatchItem("AUDUSD")])
    rows = build_board_rows(wl, cfg, refresh_data=False)
    assert len(rows) == 2
    assert all(r.status == "need_fetch" for r in rows)
    table = board_table(rows)
    assert list(table["Pair"]) == ["GBPUSD", "AUDUSD"]
