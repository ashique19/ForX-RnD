"""Active-pair H1+D1 cache: download 1d, else aggregate from 1h. No synthetic prices."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from forex_lab.data import (
    aggregate_hourly_to_daily,
    ensure_interval_ohlcv,
    generate_synthetic_ohlcv,
    read_cache_source,
)
from forex_lab.freshness import format_failure_reason
from forex_lab.ui.board import build_board_row


def _cfg(tmp_path: Path) -> dict:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    return {
        "interval": "1h",
        "period": "2y",
        "pairs": {"EURUSD": "EURUSD=X"},
        "paths": {
            "data_dir": str(data),
            "models_dir": str(tmp_path / "models"),
            "signals_dir": str(tmp_path / "signals"),
        },
        "board": {"stale_bars": 2, "incremental_period": "5d", "incremental_min_bars": 20},
    }


def _install_download(monkeypatch: pytest.MonkeyPatch, download) -> None:
    fake = types.ModuleType("yfinance")
    fake.download = download
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def _daily_frame(bars: int = 120) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=bars, freq="D")
    return pd.DataFrame(
        {"Open": 1.10, "High": 1.20, "Low": 1.00, "Close": 1.15, "Volume": 1000},
        index=idx,
    )


def test_aggregate_hourly_to_daily_uses_only_cached_bars():
    hourly = generate_synthetic_ohlcv(pair="EURUSD", bars=24 * 3, interval="1h", seed=7)
    daily = aggregate_hourly_to_daily(hourly)
    assert len(daily) == 3
    day = hourly.iloc[:24]
    bar = daily.iloc[0]
    assert bar["Open"] == pytest.approx(float(day["Open"].iloc[0]))
    assert bar["High"] == pytest.approx(float(day["High"].max()))
    assert bar["Low"] == pytest.approx(float(day["Low"].min()))
    assert bar["Close"] == pytest.approx(float(day["Close"].iloc[-1]))
    assert bar["Volume"] == pytest.approx(float(day["Volume"].sum()))


def test_ensure_prefers_provider_1d_over_resample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    hourly = generate_synthetic_ohlcv(pair="EURUSD", bars=24 * 40, interval="1h", seed=3)
    hourly.to_csv(tmp_path / "data" / "EURUSD_1h.csv")
    provider = _daily_frame()

    def _download(*_a, **kwargs):
        assert kwargs.get("interval") == "1d"
        return provider

    _install_download(monkeypatch, _download)
    frame, source, reason = ensure_interval_ohlcv("EURUSD", cfg, "1d", incremental=True)
    assert source == "yfinance"
    assert reason == ""
    assert frame is not None and len(frame) == 120
    assert read_cache_source("EURUSD", cfg, "1d") == "yfinance"
    saved = pd.read_csv(tmp_path / "data" / "EURUSD_1d.csv", index_col=0, parse_dates=True)
    assert float(saved["Close"].iloc[-1]) == pytest.approx(1.15)


def test_only_1h_cache_resampled_when_provider_has_no_1d(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    hourly = generate_synthetic_ohlcv(pair="EURUSD", bars=24 * 40, interval="1h", seed=5)
    hourly.to_csv(tmp_path / "data" / "EURUSD_1h.csv")

    def _download(*_a, **kwargs):
        assert kwargs.get("interval") == "1d"
        return pd.DataFrame()

    _install_download(monkeypatch, _download)
    frame, source, reason = ensure_interval_ohlcv("EURUSD", cfg, "1d", incremental=True)
    assert source == "resampled_from_1h"
    assert "empty" in reason.lower()
    assert frame is not None and len(frame) >= 20
    assert (tmp_path / "data" / "EURUSD_1d.csv").is_file()
    assert read_cache_source("EURUSD", cfg, "1d") == "resampled_from_1h"
    # Provider bars were not invented: the daily close is the last 1h close of that UTC day.
    assert float(frame["Close"].iloc[0]) == pytest.approx(float(hourly["Close"].iloc[23]))


def test_short_1h_cache_skips_daily_with_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    short = generate_synthetic_ohlcv(pair="EURUSD", bars=30, interval="1h", seed=1)
    short.to_csv(tmp_path / "data" / "EURUSD_1h.csv")

    def _download(*_a, **_k):
        return pd.DataFrame()

    _install_download(monkeypatch, _download)
    frame, source, reason = ensure_interval_ohlcv("EURUSD", cfg, "1d", incremental=True)
    assert frame is None
    assert source == "missing"
    assert "cannot build" in reason
    assert not (tmp_path / "data" / "EURUSD_1d.csv").exists()
    text = format_failure_reason("1d", reason)
    assert text.startswith("Daily — failed:")
    assert text


def test_refresh_pair_fills_missing_daily_from_hourly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from api.deskdata import refresh_pair

    cfg = _cfg(tmp_path)
    hourly = generate_synthetic_ohlcv(pair="EURUSD", bars=24 * 40, interval="1h", seed=9)
    hourly.to_csv(tmp_path / "data" / "EURUSD_1h.csv")

    def _download(*_a, **_k):
        raise RuntimeError("interval 1d not available")

    _install_download(monkeypatch, _download)
    monkeypatch.setattr("api.deskdata.ensure_consensus", lambda *_a, **_k: None)
    out = refresh_pair("EURUSD", interval="1d", cfg=cfg)
    assert out["fetch_failed"] is False
    assert out["cache_source"] == "resampled_from_1h"
    assert "not available" in (out.get("provider_note") or "")
    assert (tmp_path / "data" / "EURUSD_1d.csv").is_file()
    row = build_board_row("EURUSD", cfg, interval="1d", refresh_data=False, regenerate=False)
    assert row.status != "need_fetch"
    assert row.validity != "MISSING"
    assert "failed:" not in (row.validity_reason or "")


def test_daily_bars_do_not_reuse_the_hourly_signal(tmp_path: Path):
    from api.deskdata import suggestion_from_row

    cfg = _cfg(tmp_path)
    hourly = generate_synthetic_ohlcv(pair="EURUSD", bars=24 * 40, interval="1h", seed=8)
    end = pd.Timestamp.now("UTC").tz_convert(None).floor("h")
    hourly.index = pd.date_range(end=end, periods=len(hourly), freq="h")
    hourly.to_csv(tmp_path / "data" / "EURUSD_1h.csv")
    daily = aggregate_hourly_to_daily(hourly)
    daily.to_csv(tmp_path / "data" / "EURUSD_1d.csv")
    models = tmp_path / "models"
    models.mkdir()
    # Presence only — this timeframe must not load or score the lab model.
    (models / "EURUSD_xgboost.joblib").write_bytes(b"not-a-real-model")
    signals = tmp_path / "signals"
    signals.mkdir()
    (signals / "latest_signals.csv").write_text(
        "datetime,pair,close,signal,raw_signal,confidence,model\n"
        "2024-06-01 07:00:00,EURUSD,9.999,SELL,SELL,0.9,xgboost\n",
        encoding="utf-8",
    )
    row = build_board_row("EURUSD", cfg, interval="1d", refresh_data=False, regenerate=False)
    assert row.buy_sell == "—"
    assert row.status == "need_train"
    assert row.close == pytest.approx(float(daily["Close"].iloc[-1]))
    assert "not copied" in (row.validity_reason or "")
    assert "9.999" not in (row.validity_reason or "")
    sug = suggestion_from_row(row, cfg, ohlcv=daily)
    assert sug["signal"] is None
    assert sug["chip"] == "need Train"
    assert sug["now"] == pytest.approx(float(daily["Close"].iloc[-1]))
    assert "not copied" in sug["validity_reason"]
    assert "not copied" in sug["scenario"]


def test_failure_reason_names_interval_and_keeps_last_ok():
    assert format_failure_reason("1d", "no OHLCV cache — Fetch required") == "Daily — failed: no OHLCV cache"
    stamped = format_failure_reason("1d", "no OHLCV cache", last_ok="2026-09-22 16:04:00 Asia/Dhaka")
    assert stamped == "Daily — failed: no OHLCV cache. Last OK 2026-09-22 16:04:00 Asia/Dhaka."
    assert format_failure_reason("1h", "", last_ok="n/a") == "Hourly — failed: no OHLCV cache"
    again = format_failure_reason("1d", stamped, last_ok="2026-09-22 16:04:00 Asia/Dhaka")
    assert again.count("Last OK") == 1
