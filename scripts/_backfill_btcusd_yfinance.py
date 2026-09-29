"""Backfill BTCUSD (BTC-USD) 1h+1d history via yfinance — NOT Dukascopy."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forex_lab.config_loader import load_config, pair_to_ticker  # noqa: E402
from forex_lab.data import _normalize_ohlcv  # noqa: E402
from forex_lab.history import (  # noqa: E402
    _merge,
    _save_bars,
    history_path,
    load_history_csv,
    resample_bars,
)

SOURCE_YF = "yfinance"  # crypto path; Dukascopy does not apply


def _yf_chunk(ticker: str, start: str, end: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        ticker,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    return _normalize_ohlcv(raw)


def main() -> int:
    pair = "BTCUSD"
    cfg = load_config()
    ticker = pair_to_ticker(pair, cfg)
    print(f"BTCUSD via yfinance ticker={ticker} (Dukascopy N/A)", flush=True)
    path = history_path(pair, "1h", cfg)
    hist = load_history_csv(path)
    print(f"BEFORE 1h bars={len(hist)} path={path}", flush=True)

    # yfinance 1h max ~730d; walk yearly from 2015 (earlier chunks may be empty).
    years = list(range(2015, datetime.now(timezone.utc).year + 1))
    for year in years:
        start = f"{year}-01-01"
        end = f"{year + 1}-01-01"
        print(f"=== yfinance 1h chunk {start} -> {end} ===", flush=True)
        try:
            chunk = _yf_chunk(ticker, start, end, "1h")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip err: {exc}", flush=True)
            continue
        if chunk.empty:
            print("  empty", flush=True)
            continue
        # Strip tz for history store consistency
        if getattr(chunk.index, "tz", None) is not None:
            chunk.index = chunk.index.tz_convert("UTC").tz_localize(None)
        hist = _merge(hist, chunk)
        _save_bars(pair, "1h", hist, SOURCE_YF, cfg)
        vol = pd.to_numeric(hist["Volume"], errors="coerce")
        print(
            f"  checkpoint bars={len(hist)} {hist.index.min()} -> {hist.index.max()} "
            f"zero_vol={int((vol.fillna(0) <= 0).sum())}",
            flush=True,
        )

    hist = load_history_csv(path)
    if hist.empty:
        print("ERROR: no BTCUSD 1h bars from yfinance", flush=True)
        return 1
    print(f"AFTER 1h bars={len(hist)} {hist.index.min()} -> {hist.index.max()}", flush=True)

    # Daily: prefer direct yfinance 1d (deeper), merge with resampled 1h.
    daily_path = history_path(pair, "1d", cfg)
    daily = load_history_csv(daily_path)
    for year in years:
        start = f"{year}-01-01"
        end = f"{year + 1}-01-01"
        print(f"=== yfinance 1d chunk {start} -> {end} ===", flush=True)
        try:
            chunk = _yf_chunk(ticker, start, end, "1d")
        except Exception as exc:  # noqa: BLE001
            print(f"  skip err: {exc}", flush=True)
            continue
        if chunk.empty:
            print("  empty", flush=True)
            continue
        if getattr(chunk.index, "tz", None) is not None:
            chunk.index = chunk.index.tz_convert("UTC").tz_localize(None)
        daily = _merge(daily, chunk)
        _save_bars(pair, "1d", daily, SOURCE_YF, cfg)
        print(f"  checkpoint 1d bars={len(daily)}", flush=True)

    # Also merge resampled 1h→1d so recent hours contribute.
    from_h1 = resample_bars(hist, "1d")
    if not from_h1.empty:
        daily = _merge(daily, from_h1)
        _save_bars(pair, "1d", daily, SOURCE_YF, cfg)

    print(f"AFTER 1d bars={len(daily)} {daily.index.min()} -> {daily.index.max()}", flush=True)
    print("DONE_BTCUSD_YFINANCE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
