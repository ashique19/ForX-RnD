"""Main book: exits run for EVERY open position, not only the active watchlist pair.

An open paper position on a non-active pair is closed at its first SL/TP touch
(or the horizon close) on completed cached 1h bars, stamped at the exit bar,
even when auto paper is paused. No live-price fallback for inactive pairs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from api.paperdesk import run_auto_paper, set_auto_enabled
from forex_lab.broker import make_broker
from forex_lab.ui.board import BoardRow

ENTRY_BAR = "2026-09-23 05:00:00"
ENTRY, SL, TP = 1.1000, 1.1050, 1.0950  # SELL


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(tmp_path / "shadow-journal.jsonl"))
    watch = tmp_path / "watchlist.yaml"
    watch.write_text("refresh_seconds: 60\nactive: GBPUSD\npairs:\n- EURUSD\n- GBPUSD\n", encoding="utf-8")
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(watch))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    monkeypatch.setattr("api.consensus.read_consensus", lambda *_a, **_k: {"forecasters": []})

    def _build(pair, *_a, **_k):  # active pair row: HOLD, never opens
        return BoardRow(pair=str(pair).upper(), timeframe="1h", buy_sell="HOLD", target="n/a",
                        signal_details="", status="ready", confidence=None, close=1.30,
                        validity="OK", raw_signal="HOLD")

    monkeypatch.setattr("api.deskdata.build_board_row", _build)


def _cfg(store: Path) -> dict:
    return {
        "broker": {"backend": "paper", "store": str(store), "default_size": 1.0},
        "horizon": 8, "interval": "1h", "spread_pips": 0.0, "commission_pips": 0.0,
        "pip_size": 0.0001, "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"},
        "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
    }


def _frame(bars: list[tuple[float, float, float]]) -> pd.DataFrame:
    """Entry bar (05:00 UTC) + the given (high, low, close) bars from 06:00 UTC."""
    rows = [(1.1005, 1.0995, ENTRY)] + list(bars)
    idx = pd.date_range("2026-09-23 05:00", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame({"Open": [r[2] for r in rows], "High": [r[0] for r in rows],
                         "Low": [r[1] for r in rows], "Close": [r[2] for r in rows],
                         "Volume": 1.0}, index=idx)


def _setup(tmp_path, monkeypatch, bars, *, paused=False):
    store = tmp_path / "paper.json"
    monkeypatch.setenv("FORX_PAPER_STORE", str(store))
    cfg = _cfg(store)
    frames = {"EURUSD": _frame(bars)}
    monkeypatch.setattr("api.paperdesk.load_cached_ohlcv", lambda pair, *_a, **_k: frames.get(str(pair).upper()))
    shadow: list[dict] = []
    monkeypatch.setattr("api.paperdesk._shadow_record_close", lambda **kw: shadow.append(kw) or True)
    broker = make_broker(cfg, path=store)
    broker.submit("SELL", "EURUSD", size=1.0, sl=SL, tp=TP, price=ENTRY, timeframe="1h", horizon=8,
                  timestamp="2026-09-23 06:05:00 UTC", entry_bar_time=ENTRY_BAR,
                  source="manual", strategy_id="brief", strategy_name="Brief")
    set_auto_enabled(not paused, cfg=cfg)
    return cfg, store, shadow


def _book(cfg, store):
    b = make_broker(cfg, path=store)
    return b.list_positions(), b.list_closed()


FLAT = (1.1010, 1.0990, 1.1000)


@pytest.mark.parametrize("paused", [False, True])
def test_non_active_stop_hit_closes_at_sl_on_touch_bar(tmp_path, monkeypatch, paused):
    cfg, store, shadow = _setup(tmp_path, monkeypatch, [FLAT, (1.1060, 1.1000, 1.1055), (1.1090, 1.1050, 1.1080)],
                                paused=paused)
    res = run_auto_paper(cfg, now=datetime(2026, 9, 23, 9, 10, tzinfo=timezone.utc))
    opens, closed = _book(cfg, store)
    assert opens == [] and len(closed) == 1
    c = closed[0]
    assert c["exit_reason"] == "sl" and c["exit_price"] == pytest.approx(SL)
    assert c["exit_time"] == "2026-09-23 07:00:00 UTC"  # the touch bar, not the late mark
    assert any("close sl" in e for e in res["events"])
    assert len(shadow) == 1 and shadow[0]["exit_reason"] == "sl" and shadow[0]["exit_mid"] == pytest.approx(SL)


def test_non_active_target_hit_closes_at_tp(tmp_path, monkeypatch):
    cfg, store, _ = _setup(tmp_path, monkeypatch, [FLAT, FLAT, (1.1000, 1.0940, 1.0945)])
    run_auto_paper(cfg, now=datetime(2026, 9, 23, 9, 10, tzinfo=timezone.utc))
    opens, closed = _book(cfg, store)
    assert opens == [] and closed[0]["exit_reason"] == "tp"
    assert closed[0]["exit_price"] == pytest.approx(TP)
    assert closed[0]["exit_time"] == "2026-09-23 08:00:00 UTC"


def test_non_active_time_exit_at_horizon_bar_close(tmp_path, monkeypatch):
    bars = [FLAT] * 7 + [(1.1010, 1.0990, 1.0980)]  # 8 bars 06:00..13:00, no touch
    cfg, store, _ = _setup(tmp_path, monkeypatch, bars)
    run_auto_paper(cfg, now=datetime(2026, 9, 23, 14, 5, tzinfo=timezone.utc))
    opens, closed = _book(cfg, store)
    assert opens == [] and closed[0]["exit_reason"] == "duration"
    assert closed[0]["exit_price"] == pytest.approx(1.0980)
    assert closed[0]["exit_time"] == "2026-09-23 13:00:00 UTC"


def test_non_active_no_touch_stays_open(tmp_path, monkeypatch):
    cfg, store, shadow = _setup(tmp_path, monkeypatch, [FLAT, FLAT, FLAT])
    res = run_auto_paper(cfg, now=datetime(2026, 9, 23, 9, 10, tzinfo=timezone.utc))
    opens, closed = _book(cfg, store)
    assert len(opens) == 1 and closed == [] and shadow == []
    assert res["events"] == []


def test_forming_bar_touch_is_ignored(tmp_path, monkeypatch):
    # 08:00 bar touches SL but is still forming at 08:30 UTC.
    cfg, store, _ = _setup(tmp_path, monkeypatch, [FLAT, FLAT, (1.1060, 1.1000, 1.1055)])
    run_auto_paper(cfg, now=datetime(2026, 9, 23, 8, 30, tzinfo=timezone.utc))
    opens, closed = _book(cfg, store)
    assert len(opens) == 1 and closed == []
