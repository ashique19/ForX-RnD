"""Portfolio listing and auto paper. Fills stay on the local PaperBroker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.learnings import collect_learnings
from api.paperdesk import human_duration, portfolio_payload, run_auto_paper, set_auto_enabled
from forex_lab.ui.board import BoardRow


T0 = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def _cfg(store: Path) -> dict:
    return {
        "broker": {"backend": "paper", "store": str(store), "default_size": 1.0},
        "horizon": 8,
        "interval": "1h",
        "spread_pips": 0.0,
        "commission_pips": 0.0,
        "pip_size": 0.0001,
        "label_scheme": "triple_barrier",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"},
        "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
    }


def _row(signal: str = "BUY", *, validity: str = "OK", confidence: float | None = 0.64) -> BoardRow:
    return BoardRow(
        pair="EURUSD",
        timeframe="1h",
        buy_sell=signal,
        target="n/a",
        signal_details="",
        status="ready",
        confidence=confidence,
        close=1.1,
        validity=validity,
        raw_signal=signal,
    )


def _frame(close: float) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp("2026-09-23 09:00", tz="UTC")])
    return pd.DataFrame(
        {"Open": [close], "High": [close], "Low": [close], "Close": [close], "Volume": [1.0]},
        index=idx,
    )


def _install(monkeypatch: pytest.MonkeyPatch, store: Path, suggestion: dict, close: float) -> None:
    monkeypatch.setenv("FORX_PAPER_STORE", str(store))
    frame = _frame(close)

    def _suggestion(row: BoardRow, *_a, **_k) -> dict:
        data = dict(suggestion)
        signal = str(getattr(row, "buy_sell", "") or "").upper()
        data["signal"] = signal if signal in {"BUY", "SELL"} else None
        data["confidence"] = getattr(row, "confidence", None)
        return data

    monkeypatch.setattr("api.paperdesk.load_cached_ohlcv", lambda *_a, **_k: frame)
    monkeypatch.setattr("api.deskdata.suggestion_from_row", _suggestion)
    monkeypatch.setattr("api.paperdesk.paper_submit_risk_defaults", lambda *_a, **_k: (1.09, 1.12))


def _suggest(**over: object) -> dict:
    base = {
        "signal": "BUY",
        "confidence": 0.64,
        "stop": 1.09,
        "target": 1.12,
        "horizon_bars": 8,
        "rationale": "test brief",
        "validity": "OK",
    }
    base.update(over)
    return base


def test_human_duration_reads_in_hours_and_minutes():
    start = T0
    assert human_duration(start, start + timedelta(hours=2, minutes=15)) == "2h 15m"
    assert human_duration(start, start + timedelta(seconds=45)) == "45s"
    assert human_duration(start, start - timedelta(seconds=1)) is None


def test_first_sight_does_not_open_and_a_flip_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    _install(monkeypatch, store, _suggest(), 1.10)
    cfg = _cfg(store)
    first = run_auto_paper(cfg, rows=[_row()], now=T0)
    assert first["events"] == []
    assert first["errors"] == []
    book = portfolio_payload(cfg, sync=False, now=T0)
    assert book["open"] == []
    assert book["auto_enabled"] is True

    opened = run_auto_paper(cfg, rows=[_row("HOLD", confidence=0.64)], now=T0 + timedelta(minutes=1))
    assert opened["events"] == []
    opened = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=2))
    assert opened["events"] == ["EURUSD open BUY"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=2))
    assert len(book["open"]) == 1
    row = book["open"][0]
    assert row["pair"] == "EURUSD"
    assert row["status"] == "open"
    assert row["trigger"] == "BUY"
    assert row["confidence"] == pytest.approx(0.64)
    assert row["confidence_text"] == "64%"
    assert row["entry_price"] == pytest.approx(1.10)
    assert row["exit_price"] is None
    assert row["exit_price_text"] == "—"
    assert row["exit_time_dhaka"] is None
    assert "Asia/Dhaka" in (row["entry_time_dhaka"] or "")
    assert "16:00:02" in (row["entry_time_dhaka"] or "") or "16:02" in (row["entry_time_dhaka"] or "")
    assert row["outcome"] is None
    assert row["pnl_basis"] == "mark"
    assert row["sl"] == pytest.approx(1.09)
    assert row["tp"] == pytest.approx(1.12)
    assert row["source"] == "auto"
    assert row["duration"]


def test_missing_confidence_is_an_em_dash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    _install(monkeypatch, store, _suggest(confidence=None), 1.10)
    cfg = _cfg(store)
    run_auto_paper(cfg, rows=[_row("HOLD", confidence=None)], now=T0)
    run_auto_paper(cfg, rows=[_row(confidence=None)], now=T0 + timedelta(minutes=1))
    row = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=1))["open"][0]
    assert row["confidence"] is None
    assert row["confidence_text"] == "—"


def test_stale_and_pause_and_same_signal_do_not_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    _install(monkeypatch, store, _suggest(), 1.10)
    cfg = _cfg(store)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)
    blocked = run_auto_paper(cfg, rows=[_row(validity="STALE")], now=T0 + timedelta(minutes=1))
    assert blocked["events"] == []
    assert portfolio_payload(cfg, sync=False)["open"] == []

    run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=2))
    assert len(portfolio_payload(cfg, sync=False)["open"]) == 1
    again = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=3))
    assert again["events"] == []
    assert len(portfolio_payload(cfg, sync=False)["open"]) == 1

    set_auto_enabled(False, cfg)
    _install(monkeypatch, store, _suggest(signal="SELL"), 1.05)
    paused = run_auto_paper(cfg, rows=[_row("SELL")], now=T0 + timedelta(minutes=4))
    assert paused["auto_enabled"] is False
    assert paused["events"] == []
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=4))
    assert book["auto_enabled"] is False
    assert len(book["open"]) == 1
    assert book["open"][0]["trigger"] == "BUY"


def test_sl_tp_duration_and_opposite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)

    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)
    run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=1))

    _install(monkeypatch, store, _suggest(), 1.08)
    closed = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=30))
    assert closed["events"] == ["EURUSD close sl"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=30))
    assert book["open"] == []
    sl = book["closed"][0]
    assert sl["exit_reason"] == "sl"
    assert sl["outcome"] == "WRONG"
    assert sl["exit_price"] == pytest.approx(1.08)
    assert sl["pnl_price"] == pytest.approx(-0.02)
    assert sl["pnl_text"].startswith("-")
    assert "R" in sl["pnl_text"]
    assert sl["confidence_text"] == "64%"
    assert "Asia/Dhaka" in (sl["exit_time_dhaka"] or "")

    # Same BUY is not a new flip, so the stop-out does not immediately re-enter.
    replay = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=31))
    assert replay["events"] == []

    _install(monkeypatch, store, _suggest(signal=None), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(hours=1))
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(hours=1, minutes=1))
    _install(monkeypatch, store, _suggest(), 1.13)
    tp = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(hours=1, minutes=20))
    assert tp["events"] == ["EURUSD close tp"]
    tp_row = portfolio_payload(cfg, sync=False, now=T0 + timedelta(hours=1, minutes=20))["closed"][0]
    assert tp_row["exit_reason"] == "tp"
    assert tp_row["outcome"] == "RIGHT"
    assert tp_row["exit_price"] == pytest.approx(1.13)
    assert tp_row["pnl_price"] == pytest.approx(0.03)

    _install(monkeypatch, store, _suggest(signal=None, horizon_bars=2), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(hours=3))
    _install(monkeypatch, store, _suggest(horizon_bars=2), 1.10)
    run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(hours=3, minutes=1))
    _install(monkeypatch, store, _suggest(horizon_bars=2), 1.105)
    dur = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(hours=5, minutes=1))
    assert dur["events"] == ["EURUSD close duration"]
    dur_row = portfolio_payload(cfg, sync=False, now=T0 + timedelta(hours=5, minutes=1))["closed"][0]
    assert dur_row["exit_reason"] == "duration"
    assert dur_row["outcome"] == "RIGHT"
    assert dur_row["duration"].startswith("2h")

    _install(monkeypatch, store, _suggest(signal=None), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(hours=6))
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(hours=6, minutes=1))
    _install(monkeypatch, store, _suggest(signal="SELL"), 1.10)
    opp = run_auto_paper(cfg, rows=[_row("SELL")], now=T0 + timedelta(hours=6, minutes=10))
    assert opp["events"] == ["EURUSD close opposite", "EURUSD open SELL"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(hours=6, minutes=10))
    assert book["open"][0]["trigger"] == "SELL"
    assert book["open"][0]["source"] == "auto"
    flat = next(row for row in book["closed"] if row["exit_reason"] == "opposite")
    assert flat["outcome"] == "FLAT"
    learned = collect_learnings(cfg, limit=20)
    titles = " ".join(item["title"] for item in learned["items"])
    assert "WRONG" in titles
    assert "RIGHT" in titles
    assert "FLAT" in titles
    assert learned["feeds"][0]["source"] == "paper"


def test_manual_order_lands_in_the_same_portfolio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    monkeypatch.setenv("FORX_PAPER_STORE", str(store))
    row = _row(confidence=0.81)
    monkeypatch.setattr("api.deskdata.build_board_row", lambda *_a, **_k: row)
    monkeypatch.setattr("api.paperdesk.load_cached_ohlcv", lambda *_a, **_k: _frame(1.101))
    monkeypatch.setattr("api.paperdesk.paper_submit_risk_defaults", lambda *_a, **_k: (1.09, 1.12))
    from api.paperdesk import paper_order

    result = paper_order("EURUSD", "BUY", cfg=cfg)
    assert result["ok"] is True
    book = portfolio_payload(cfg, sync=False, now=T0)
    assert book["open"][0]["source"] == "manual"
    assert book["open"][0]["confidence_text"] == "81%"
    assert book["open"][0]["entry_price"] == pytest.approx(1.101)

    paper_order("EURUSD", "CLOSE", cfg=cfg)
    closed = portfolio_payload(cfg, sync=False, now=datetime.now(timezone.utc))["closed"][0]
    assert closed["exit_reason"] == "manual"
    assert closed["outcome"] == "FLAT"
    assert closed["pnl_price"] is not None


def test_http_portfolio_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(tmp_path / "watchlist.yaml"))
    monkeypatch.setenv("FORX_ALERT_STATE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "consensus.json"))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    monkeypatch.setenv("FORX_PAPER_STORE", str(tmp_path / "paper.json"))
    from api.limiter import reset as reset_limiter
    from api.main import create_app

    reset_limiter()
    client = TestClient(create_app())
    res = client.get("/portfolio", params={"sync": "false"})
    assert res.status_code == 200
    body = res.json()
    assert body["open"] == []
    assert body["closed"] == []
    assert body["auto_enabled"] is True
    assert "Asia/Dhaka" in body["generated_at_dhaka"]

    off = client.post("/portfolio/auto", json={"enabled": False})
    assert off.status_code == 200
    assert off.json()["auto_enabled"] is False
    assert off.json()["open"] == []

    on = client.post("/portfolio/auto", json={"enabled": True})
    assert on.status_code == 200
    assert on.json()["auto_enabled"] is True
