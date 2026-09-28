"""Live Decision refresh: Dukascopy primary, yfinance fallback. No synthetic."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from forex_lab.data import (
    SOURCE_DUKASCOPY,
    SOURCE_YFINANCE,
    ensure_interval_ohlcv,
    generate_synthetic_ohlcv,
    load_cached_ohlcv,
    read_cache_source,
    try_live_refresh,
)


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
        "board": {
            "stale_bars": 2,
            "incremental_period": "5d",
            "incremental_min_bars": 20,
            "dukascopy_lookback_hours": 4,
        },
    }


def _seed_cache(cfg: dict, pair: str = "EURUSD", bars: int = 120) -> pd.DataFrame:
    frame = generate_synthetic_ohlcv(pair=pair, bars=bars, interval="1h", seed=11)
    path = Path(cfg["paths"]["data_dir"]) / f"{pair}_1h.csv"
    frame.to_csv(path)
    return frame


def test_primary_dukascopy_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    existing = _seed_cache(cfg)
    last_ts = existing.index[-1]
    denser = existing.iloc[[-1]].copy()
    denser.loc[last_ts, "Close"] = float(existing["Close"].iloc[-1]) + 0.0015
    denser.loc[last_ts, "High"] = max(float(denser.loc[last_ts, "High"]), float(denser.loc[last_ts, "Close"]))

    def _duka(pair, cfg, period=None, interval=None, *, incremental=False):
        out = Path(cfg["paths"]["data_dir"]) / f"{pair}_{interval or '1h'}.csv"
        merged = pd.concat([existing, denser])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        merged.to_csv(out)
        return merged, SOURCE_DUKASCOPY

    def _yf(*_a, **_k):
        raise AssertionError("yfinance must not run when Dukascopy succeeds")

    monkeypatch.setattr("forex_lab.data.try_dukascopy_refresh", _duka)
    monkeypatch.setattr("forex_lab.data.try_yfinance_refresh", _yf)

    frame, source, reason = ensure_interval_ohlcv(
        "EURUSD", cfg, "1h", incremental=True, refresh=try_live_refresh
    )
    assert source == SOURCE_DUKASCOPY
    assert reason == ""
    assert frame is not None
    assert float(frame["Close"].iloc[-1]) == pytest.approx(float(denser["Close"].iloc[-1]))
    assert read_cache_source("EURUSD", cfg, "1h") == SOURCE_DUKASCOPY


def test_primary_fail_falls_back_to_yfinance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    existing = _seed_cache(cfg)
    last_ts = existing.index[-1]
    yf_bar = existing.iloc[[-1]].copy()
    yf_bar.loc[last_ts, "Close"] = float(existing["Close"].iloc[-1]) + 0.0007

    def _duka(*_a, **_k):
        return None, "dukascopy empty"

    def _yf(pair, cfg, period=None, interval=None, *, incremental=False):
        out = Path(cfg["paths"]["data_dir"]) / f"{pair}_{interval or '1h'}.csv"
        merged = pd.concat([existing, yf_bar])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        merged.to_csv(out)
        return merged, SOURCE_YFINANCE

    monkeypatch.setattr("forex_lab.data.try_dukascopy_refresh", _duka)
    monkeypatch.setattr("forex_lab.data.try_yfinance_refresh", _yf)

    frame, source, reason = ensure_interval_ohlcv(
        "EURUSD", cfg, "1h", incremental=True, refresh=try_live_refresh
    )
    assert source == SOURCE_YFINANCE
    assert reason == ""
    assert frame is not None
    assert float(frame["Close"].iloc[-1]) == pytest.approx(float(yf_bar["Close"].iloc[-1]))
    assert read_cache_source("EURUSD", cfg, "1h") == SOURCE_YFINANCE


def test_both_fail_leaves_cache_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    existing = _seed_cache(cfg)
    before_close = float(existing["Close"].iloc[-1])
    before_mtime = (Path(cfg["paths"]["data_dir"]) / "EURUSD_1h.csv").stat().st_mtime
    sidecar = Path(cfg["paths"]["data_dir"]) / "EURUSD_1h.source"
    sidecar.write_text("yfinance\n", encoding="utf-8")

    def _duka(*_a, **_k):
        return None, "dukascopy rate limited (429)"

    def _yf(*_a, **_k):
        return None, "yfinance empty"

    monkeypatch.setattr("forex_lab.data.try_dukascopy_refresh", _duka)
    monkeypatch.setattr("forex_lab.data.try_yfinance_refresh", _yf)

    frame, source, reason = ensure_interval_ohlcv(
        "EURUSD", cfg, "1h", incremental=True, refresh=try_live_refresh
    )
    assert frame is None
    assert source == "cache"
    assert "dukascopy" in reason.lower()
    assert "yfinance" in reason.lower()
    cached = load_cached_ohlcv("EURUSD", cfg, "1h")
    assert cached is not None
    assert float(cached["Close"].iloc[-1]) == pytest.approx(before_close)
    after_mtime = (Path(cfg["paths"]["data_dir"]) / "EURUSD_1h.csv").stat().st_mtime
    assert after_mtime == before_mtime
    assert read_cache_source("EURUSD", cfg, "1h") == "yfinance"


def test_try_live_refresh_reason_chains_both_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    _seed_cache(cfg)

    monkeypatch.setattr(
        "forex_lab.data.try_dukascopy_refresh",
        lambda *_a, **_k: (None, "dukascopy empty"),
    )
    monkeypatch.setattr(
        "forex_lab.data.try_yfinance_refresh",
        lambda *_a, **_k: (None, "yfinance rate limited (429)"),
    )
    frame, reason = try_live_refresh("EURUSD", cfg, interval="1h", incremental=True)
    assert frame is None
    assert reason.startswith("dukascopy:")
    assert "yfinance rate limited" in reason


def test_dense_write_flattens_volume_when_joblib_lacks_vol_z(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Dukascopy tick volume must not flip schema before a vol_z retrain."""
    cfg = _cfg(tmp_path)
    existing = _seed_cache(cfg)
    # Flat volume like yfinance.
    existing["Volume"] = 0.0
    existing.to_csv(Path(cfg["paths"]["data_dir"]) / "EURUSD_1h.csv")

    last_ts = existing.index[-1]
    denser = existing.iloc[[-1]].copy()
    denser.loc[last_ts, "Close"] = float(existing["Close"].iloc[-1]) + 0.0012
    denser.loc[last_ts, "High"] = max(float(denser.loc[last_ts, "High"]), float(denser.loc[last_ts, "Close"]))
    denser.loc[last_ts, "Volume"] = 4321.0  # real Dukascopy-like volume

    def _duka(pair, cfg, period=None, interval=None, *, incremental=False):
        from forex_lab.data import (
            SOURCE_DUKASCOPY,
            _align_live_volume_to_model,
            data_path,
        )

        out = data_path(pair, cfg, interval or "1h")
        merged = pd.concat([existing, denser])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        merged = _align_live_volume_to_model(
            merged, pair, cfg, interval or "1h", existing=existing
        )
        merged.to_csv(out)
        return merged, SOURCE_DUKASCOPY

    monkeypatch.setattr("forex_lab.data.try_dukascopy_refresh", _duka)
    monkeypatch.setattr(
        "forex_lab.data.try_yfinance_refresh",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("yf should not run")),
    )
    monkeypatch.setattr("forex_lab.data._joblib_has_vol_z", lambda *a, **k: False)

    # Call align path through ensure via a thin wrapper that uses our _duka
    from forex_lab.data import try_live_refresh, load_cached_ohlcv

    # Re-bind try_live_refresh internals: patch try_dukascopy at module used by try_live
    frame, source, reason = ensure_interval_ohlcv(
        "EURUSD", cfg, "1h", incremental=True, refresh=try_live_refresh
    )
    assert source == SOURCE_DUKASCOPY
    assert reason == ""
    assert frame is not None
    assert float(frame["Close"].iloc[-1]) == pytest.approx(float(denser["Close"].iloc[-1]))
    cached = load_cached_ohlcv("EURUSD", cfg, "1h")
    assert cached is not None
    assert float(cached["Volume"].std() or 0.0) == pytest.approx(0.0)
    assert float(cached["Volume"].iloc[-1]) == pytest.approx(0.0)


def test_dense_write_keeps_volume_when_joblib_has_vol_z(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = _cfg(tmp_path)
    existing = _seed_cache(cfg)
    existing["Volume"] = 0.0
    existing.to_csv(Path(cfg["paths"]["data_dir"]) / "EURUSD_1h.csv")
    last_ts = existing.index[-1]
    denser = existing.iloc[[-1]].copy()
    denser.loc[last_ts, "Close"] = float(existing["Close"].iloc[-1]) + 0.0005
    denser.loc[last_ts, "Volume"] = 5555.0

    monkeypatch.setattr("forex_lab.data._joblib_has_vol_z", lambda *a, **k: True)

    from forex_lab.data import _align_live_volume_to_model, SOURCE_DUKASCOPY

    merged = pd.concat([existing, denser])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    out = _align_live_volume_to_model(merged, "EURUSD", cfg, "1h", existing=existing)
    assert float(out["Volume"].iloc[-1]) == pytest.approx(5555.0)
    assert float(out["Volume"].std()) > 0.0


def test_m1_overlay_volume_zero_does_not_clobber_positive_volume():
    """yfinance 1m overlay with Volume=0 must keep prior Dukascopy volume."""
    from forex_lab.data import _merge_live_ohlcv_parts

    idx = pd.date_range("2026-09-25 10:00:00", periods=3, freq="1h")
    existing = pd.DataFrame(
        {
            "Open": [1.1, 1.2, 1.3],
            "High": [1.15, 1.25, 1.35],
            "Low": [1.05, 1.15, 1.25],
            "Close": [1.12, 1.22, 1.32],
            "Volume": [1000.0, 2000.0, 3000.0],
        },
        index=idx,
    )
    # Overlay densifies last bar OHLC but carries Volume=0 (yfinance FX).
    m1 = existing.iloc[[-1]].copy()
    m1.loc[idx[-1], "Close"] = 1.33
    m1.loc[idx[-1], "High"] = 1.36
    m1.loc[idx[-1], "Volume"] = 0.0
    # Brand-new tip bar only in m1, also vol=0 — stays 0 (unknown).
    new_ts = idx[-1] + pd.Timedelta(hours=1)
    brand_new = pd.DataFrame(
        {
            "Open": [1.33],
            "High": [1.34],
            "Low": [1.32],
            "Close": [1.335],
            "Volume": [0.0],
        },
        index=pd.DatetimeIndex([new_ts]),
    )
    m1 = pd.concat([m1, brand_new])

    out = _merge_live_ohlcv_parts([existing, m1])
    assert float(out.loc[idx[-1], "Close"]) == pytest.approx(1.33)
    assert float(out.loc[idx[-1], "High"]) == pytest.approx(1.36)
    assert float(out.loc[idx[-1], "Volume"]) == pytest.approx(3000.0)
    assert float(out.loc[idx[0], "Volume"]) == pytest.approx(1000.0)
    assert float(out.loc[new_ts, "Volume"]) == pytest.approx(0.0)


def test_m1_overlay_positive_yf_volume_fills_when_prior_missing():
    """yfinance volume used only when prior is 0 and yf volume > 0."""
    from forex_lab.data import _merge_live_ohlcv_parts

    idx = pd.date_range("2026-09-25 12:00:00", periods=2, freq="1h")
    existing = pd.DataFrame(
        {
            "Open": [1.1, 1.2],
            "High": [1.15, 1.25],
            "Low": [1.05, 1.15],
            "Close": [1.12, 1.22],
            "Volume": [5000.0, 0.0],
        },
        index=idx,
    )
    m1 = existing.copy()
    m1["Close"] = [1.13, 1.23]
    m1["Volume"] = [0.0, 42.0]  # only tip has yf volume
    out = _merge_live_ohlcv_parts([existing, m1])
    assert float(out.loc[idx[0], "Volume"]) == pytest.approx(5000.0)
    assert float(out.loc[idx[1], "Volume"]) == pytest.approx(42.0)
    assert float(out.loc[idx[1], "Close"]) == pytest.approx(1.23)
