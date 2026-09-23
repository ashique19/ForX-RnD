"""Portfolio listing and auto paper. Fills stay on the local PaperBroker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.learnings import collect_learnings
from api.paperdesk import human_duration, portfolio_payload, run_auto_paper, set_auto_enabled, set_auto_settings
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


def _row(signal: str = "BUY", *, validity: str = "OK", confidence: float | None = 0.70) -> BoardRow:
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

    def _risk(_frame, _cfg, side, _validity):
        if str(side).upper() == "SELL":
            return (1.12, 1.09)
        return (1.09, 1.12)

    monkeypatch.setattr("api.paperdesk.load_cached_ohlcv", lambda *_a, **_k: frame)
    monkeypatch.setattr("api.deskdata.suggestion_from_row", _suggestion)
    monkeypatch.setattr("api.paperdesk.paper_submit_risk_defaults", _risk)
    monkeypatch.setattr("api.consensus.read_consensus", lambda *_a, **_k: {"forecasters": []})


def _suggest(**over: object) -> dict:
    base = {
        "signal": "BUY",
        "confidence": 0.70,
        "stop": 1.09,
        "target": 1.12,
        "horizon_bars": 8,
        "rationale": "test brief",
        "validity": "OK",
    }
    base.update(over)
    return base


def test_auto_without_rows_builds_only_the_active_pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    watch = tmp_path / "watchlist.yaml"
    watch.write_text(
        "refresh_seconds: 60\nactive: GBPUSD\npairs:\n- EURUSD\n- GBPUSD\n",
        encoding="utf-8",
    )
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(watch))
    monkeypatch.setenv("FORX_PAPER_STORE", str(store))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    built: list[str] = []

    def _build(pair, *_a, **_k):
        built.append(str(pair).upper())
        row = _row("HOLD", confidence=None)
        row.pair = str(pair).upper()
        return row

    monkeypatch.setattr("api.deskdata.build_board_row", _build)
    monkeypatch.setattr("api.consensus.read_consensus", lambda *_a, **_k: {"forecasters": []})
    set_auto_enabled(False, cfg=cfg)
    result = run_auto_paper(cfg, now=T0)
    assert built == ["GBPUSD"]
    assert result["events"] == []
    book = portfolio_payload(cfg, sync=False, now=T0)
    assert book["active_pair"] == "GBPUSD"


def test_inactive_open_stays_frozen_until_that_pair_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    watch = tmp_path / "watchlist.yaml"
    watch.write_text(
        "refresh_seconds: 60\nactive: GBPUSD\npairs:\n- EURUSD\n- GBPUSD\n",
        encoding="utf-8",
    )
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(watch))
    _install(monkeypatch, store, _suggest(), 1.05)
    from forex_lab.broker import make_broker

    broker = make_broker(cfg, path=store)
    broker.submit(
        "BUY",
        "EURUSD",
        size=1.0,
        sl=1.09,
        tp=1.12,
        price=1.10,
        timeframe="1h",
        horizon=0,
        timestamp=T0.isoformat(),
        source="manual",
        strategy_id="brief",
        strategy_name="Brief",
    )

    def _build(pair, *_a, **_k):
        row = _row("SELL", confidence=0.9)
        row.pair = str(pair).upper()
        row.close = 1.05
        return row

    monkeypatch.setattr("api.deskdata.build_board_row", _build)
    result = run_auto_paper(cfg, now=T0 + timedelta(minutes=5))
    assert result["events"] == []
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=5))
    assert len(book["open"]) == 1
    assert book["open"][0]["pair"] == "EURUSD"
    assert book["closed"] == []


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

    opened = run_auto_paper(cfg, rows=[_row("HOLD", confidence=0.70)], now=T0 + timedelta(minutes=1))
    assert opened["events"] == []
    opened = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=2))
    assert opened["events"] == ["EURUSD open BUY"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=2))
    assert len(book["open"]) == 1
    row = book["open"][0]
    assert row["pair"] == "EURUSD"
    assert row["status"] == "open"
    assert row["trigger"] == "BUY"
    assert row["confidence"] == pytest.approx(0.70)
    assert row["confidence_text"] == "70%"
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


def test_missing_confidence_does_not_auto_open_and_a_manual_fill_stays_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = tmp_path / "paper.json"
    _install(monkeypatch, store, _suggest(confidence=None), 1.10)
    cfg = _cfg(store)
    run_auto_paper(cfg, rows=[_row("HOLD", confidence=None)], now=T0)
    blocked = run_auto_paper(cfg, rows=[_row(confidence=None)], now=T0 + timedelta(minutes=1))
    assert blocked["events"] == []
    assert "below" in blocked["blocks"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=1))
    assert book["open"] == []
    assert book["min_confidence"] == 65

    row = _row(confidence=None)
    monkeypatch.setattr("api.deskdata.build_board_row", lambda *_a, **_k: row)
    from api.paperdesk import paper_order

    paper_order("EURUSD", "BUY", cfg=cfg)
    filled = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=2))["open"][0]
    assert filled["source"] == "manual"
    assert filled["confidence"] is None
    assert filled["confidence_text"] == "—"


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
    assert sl["confidence_text"] == "70%"
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
    assert on.json()["max_opens_per_hour"] == 3
    assert on.json()["opens_this_hour"] == 0
    assert on.json()["rate_limited"] is False
    assert on.json()["rate_status"] is None

    missing = client.post("/portfolio/auto", json={})
    assert missing.status_code == 400
    too_high = client.post("/portfolio/auto", json={"max_opens_per_hour": 100})
    assert too_high.status_code == 422

    capped = client.post("/portfolio/auto", json={"max_opens_per_hour": 5})
    assert capped.status_code == 200
    assert capped.json()["max_opens_per_hour"] == 5
    assert client.get("/portfolio", params={"sync": "false"}).json()["max_opens_per_hour"] == 5

    paused = client.post("/portfolio/auto", json={"enabled": False, "max_opens_per_hour": 0})
    assert paused.status_code == 200
    body = paused.json()
    assert body["auto_enabled"] is False
    assert body["max_opens_per_hour"] == 0
    assert body["min_confidence"] == 65
    assert body["rate_status"] == "Rate-limited: 0/0 opens this hour"
    assert body["block_status"] == "Paused · Rate-limited: 0/0 opens this hour"

    client.post("/portfolio/auto", json={"enabled": True, "max_opens_per_hour": 3})
    tuned = client.post("/portfolio/auto", json={"min_confidence": 80})
    assert tuned.status_code == 200
    assert tuned.json()["min_confidence"] == 80
    assert client.get("/portfolio", params={"sync": "false"}).json()["min_confidence"] == 80
    assert client.post("/portfolio/auto", json={"min_confidence": 40}).status_code == 422
    assert client.post("/portfolio/auto", json={"min_confidence": 95}).status_code == 422


def _reenter(cfg: dict, when: datetime) -> dict:
    return run_auto_paper(cfg, rows=[_row()], now=when)


def _arm(cfg: dict, when: datetime) -> None:
    run_auto_paper(cfg, rows=[_row("HOLD")], now=when)


def _stop_out(cfg: dict, when: datetime) -> dict:
    return run_auto_paper(cfg, rows=[_row()], now=when)


def test_hourly_cap_skips_auto_open_and_retries_after_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    set_auto_settings(max_opens_per_hour=3, cfg=cfg)

    last_entry = T0
    for i in range(3):
        slot = T0 + timedelta(minutes=i * 2)
        _arm(cfg, slot)
        opened = _reenter(cfg, slot + timedelta(seconds=1))
        assert opened["events"] == ["EURUSD open BUY"]
        last_entry = slot + timedelta(seconds=1)
        _install(monkeypatch, store, _suggest(), 1.08)
        stopped = _stop_out(cfg, slot + timedelta(seconds=30))
        assert stopped["events"] == ["EURUSD close sl"]
        _install(monkeypatch, store, _suggest(), 1.10)

    slot = T0 + timedelta(minutes=8)
    _arm(cfg, slot)
    blocked = _reenter(cfg, slot + timedelta(seconds=1))
    assert blocked["events"] == ["EURUSD skip rate"]
    book = portfolio_payload(cfg, sync=False, now=slot + timedelta(seconds=1))
    assert book["open"] == []
    assert book["opens_this_hour"] == 3
    assert book["max_opens_per_hour"] == 3
    assert book["rate_limited"] is True
    assert book["rate_status"] == "Rate-limited: 3/3 opens this hour"
    assert len(book["closed"]) == 3

    still = _reenter(cfg, slot + timedelta(minutes=1))
    assert still["events"] == ["EURUSD skip rate"]

    set_auto_enabled(False, cfg)
    paused = _reenter(cfg, slot + timedelta(minutes=2))
    assert paused["events"] == []
    paused_book = portfolio_payload(cfg, sync=False, now=slot + timedelta(minutes=2))
    assert paused_book["auto_enabled"] is False
    assert paused_book["rate_status"] == "Rate-limited: 3/3 opens this hour"
    assert paused_book["opens_this_hour"] == 3
    set_auto_enabled(True, cfg)

    later = last_entry + timedelta(minutes=60, seconds=5)
    _install(monkeypatch, store, _suggest(), 1.11)
    opened = _reenter(cfg, later)
    assert opened["events"] == ["EURUSD open BUY"]
    book = portfolio_payload(cfg, sync=False, now=later)
    assert book["opens_this_hour"] == 1
    assert book["rate_limited"] is False
    assert book["rate_status"] is None
    assert book["open"][0]["entry_price"] == pytest.approx(1.11)
    assert book["open"][0]["source"] == "auto"


def test_manual_orders_do_not_spend_the_auto_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    set_auto_settings(max_opens_per_hour=1, cfg=cfg)
    row = _row(confidence=0.5)
    monkeypatch.setenv("FORX_PAPER_STORE", str(store))
    monkeypatch.setattr("api.deskdata.build_board_row", lambda *_a, **_k: row)
    monkeypatch.setattr("api.paperdesk.load_cached_ohlcv", lambda *_a, **_k: _frame(1.101))
    monkeypatch.setattr("api.paperdesk.paper_submit_risk_defaults", lambda *_a, **_k: (1.09, 1.12))
    from api.paperdesk import paper_order

    paper_order("EURUSD", "BUY", cfg=cfg)
    paper_order("EURUSD", "CLOSE", cfg=cfg)
    book = portfolio_payload(cfg, sync=False, now=T0)
    assert book["opens_this_hour"] == 0
    assert book["rate_limited"] is False
    assert book["closed"][0]["source"] == "manual"

    _install(monkeypatch, store, _suggest(), 1.10)
    _arm(cfg, T0 + timedelta(minutes=1))
    opened = _reenter(cfg, T0 + timedelta(minutes=2))
    assert opened["events"] == ["EURUSD open BUY"]
    _install(monkeypatch, store, _suggest(), 1.08)
    assert _stop_out(cfg, T0 + timedelta(minutes=3))["events"] == ["EURUSD close sl"]

    _install(monkeypatch, store, _suggest(), 1.10)
    _arm(cfg, T0 + timedelta(minutes=4))
    blocked = _reenter(cfg, T0 + timedelta(minutes=5))
    assert blocked["events"] == ["EURUSD skip rate"]
    assert portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=5))["opens_this_hour"] == 1

    paper_order("EURUSD", "SELL", cfg=cfg)
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=6))
    assert book["open"][0]["source"] == "manual"
    assert book["open"][0]["trigger"] == "SELL"
    assert book["opens_this_hour"] == 1
    assert book["rate_status"] == "Rate-limited: 1/1 opens this hour"


def test_opposite_close_still_runs_when_the_hourly_cap_is_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    set_auto_settings(max_opens_per_hour=1, cfg=cfg)
    _arm(cfg, T0)
    assert _reenter(cfg, T0 + timedelta(seconds=1))["events"] == ["EURUSD open BUY"]
    _install(monkeypatch, store, _suggest(signal="SELL"), 1.10)
    flipped = run_auto_paper(cfg, rows=[_row("SELL")], now=T0 + timedelta(minutes=2))
    assert flipped["events"] == ["EURUSD close opposite", "EURUSD skip rate"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=2))
    assert book["open"] == []
    assert book["closed"][0]["exit_reason"] == "opposite"
    assert book["closed"][0]["outcome"] == "FLAT"
    assert book["opens_this_hour"] == 1
    assert book["rate_status"] == "Rate-limited: 1/1 opens this hour"


def test_confidence_crosses_once_and_a_tick_does_not_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)

    first = run_auto_paper(cfg, rows=[_row(confidence=0.80)], now=T0)
    assert first["events"] == []
    tick = run_auto_paper(cfg, rows=[_row(confidence=0.82)], now=T0 + timedelta(minutes=1))
    assert tick["events"] == []
    assert portfolio_payload(cfg, sync=False)["open"] == []

    dipped = run_auto_paper(cfg, rows=[_row(confidence=0.40)], now=T0 + timedelta(minutes=2))
    assert dipped["events"] == []
    assert "below" in dipped["blocks"]
    status = portfolio_payload(
        cfg, sync=True, rows=[_row(confidence=0.40)], now=T0 + timedelta(minutes=3)
    )
    assert status["open"] == []
    assert status["block_status"] == "Below threshold (65%)"

    opened = run_auto_paper(cfg, rows=[_row(confidence=0.80)], now=T0 + timedelta(minutes=4))
    assert opened["events"] == ["EURUSD open BUY"]
    again = run_auto_paper(cfg, rows=[_row(confidence=0.81)], now=T0 + timedelta(minutes=5))
    assert again["events"] == []

    _install(monkeypatch, store, _suggest(), 1.08)
    assert run_auto_paper(cfg, rows=[_row(confidence=0.81)], now=T0 + timedelta(minutes=6))["events"] == [
        "EURUSD close sl"
    ]
    held = run_auto_paper(cfg, rows=[_row(confidence=0.90)], now=T0 + timedelta(minutes=7))
    assert held["events"] == []
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row(confidence=0.40)], now=T0 + timedelta(minutes=8))
    reopened = run_auto_paper(cfg, rows=[_row(confidence=0.90)], now=T0 + timedelta(minutes=9))
    assert reopened["events"] == ["EURUSD open BUY"]


def test_side_flip_opens_and_a_weak_opposite_only_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)
    assert run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(seconds=1))["events"] == ["EURUSD open BUY"]

    _install(monkeypatch, store, _suggest(signal="SELL"), 1.10)
    flipped = run_auto_paper(cfg, rows=[_row("SELL", confidence=0.40)], now=T0 + timedelta(minutes=2))
    assert flipped["events"] == ["EURUSD close opposite"]
    assert portfolio_payload(cfg, sync=False)["open"] == []

    run_auto_paper(cfg, rows=[_row("HOLD", confidence=0.40)], now=T0 + timedelta(minutes=3))
    _install(monkeypatch, store, _suggest(), 1.10)
    assert run_auto_paper(cfg, rows=[_row(confidence=0.80)], now=T0 + timedelta(minutes=4))["events"] == [
        "EURUSD open BUY"
    ]
    _install(monkeypatch, store, _suggest(signal="SELL"), 1.11)
    opp = run_auto_paper(cfg, rows=[_row("SELL", confidence=0.88)], now=T0 + timedelta(minutes=5))
    assert opp["events"] == ["EURUSD close opposite", "EURUSD open SELL"]


def test_stale_gate_blocks_the_cross(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)
    gated = run_auto_paper(cfg, rows=[_row(validity="STALE", confidence=0.90)], now=T0 + timedelta(minutes=1))
    assert gated["events"] == []
    assert "gated" in gated["blocks"]
    book = portfolio_payload(
        cfg,
        sync=True,
        rows=[_row(validity="STALE", confidence=0.90)],
        now=T0 + timedelta(minutes=2),
    )
    assert book["open"] == []
    assert book["block_status"] == "Gated"
    opened = run_auto_paper(cfg, rows=[_row(confidence=0.90)], now=T0 + timedelta(minutes=3))
    assert opened["events"] == ["EURUSD open BUY"]


def test_stale_raw_signal_is_gated_when_the_brief_hides_the_side(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    hidden = _row("HOLD", validity="STALE", confidence=0.90)
    hidden.raw_signal = "SELL"
    gated = run_auto_paper(cfg, rows=[hidden], now=T0)
    assert gated["events"] == []
    assert "gated" in gated["blocks"]
    book = portfolio_payload(cfg, sync=True, rows=[hidden], now=T0 + timedelta(minutes=1))
    assert book["open"] == []
    assert book["block_status"] == "Gated"


def _dirs(*names: str):
    def _read(*_a, **_k):
        return {"forecasters": [{"status": "OK", "direction": name} for name in names]}

    return _read


def test_two_strategy_books_stay_separate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    assert run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)["events"] == []
    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Sell", "Sell", "Buy"))
    opened = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=1))
    assert opened["events"] == ["EURUSD open BUY", "EURUSD open SELL consensus"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=1))
    by_book = {row["strategy_id"]: row for row in book["open"]}
    assert set(by_book) == {"brief", "consensus"}
    assert by_book["brief"]["trigger"] == "BUY"
    assert by_book["brief"]["confidence"] == pytest.approx(0.70)
    assert by_book["consensus"]["trigger"] == "SELL"
    assert by_book["consensus"]["confidence"] == pytest.approx(2 / 3)
    assert by_book["consensus"]["confidence_text"] == "67%"
    assert by_book["brief"]["pnl_text"] != "—"
    assert by_book["consensus"]["pnl_text"] != "—"
    assert by_book["brief"]["id"] != by_book["consensus"]["id"]


def test_consensus_needs_a_majority_and_a_cross(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(signal=None), 1.10)
    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Buy", "Sell", "Neutral"))
    assert run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)["events"] == []
    tied = run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(minutes=1))
    assert tied["events"] == []
    assert portfolio_payload(cfg, sync=False)["open"] == []

    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Buy", "Neutral", "Neutral"))
    assert run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(minutes=2))["events"] == []

    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Sell", "Sell", "Buy"))
    opened = run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(minutes=3))
    assert opened["events"] == ["EURUSD open SELL consensus"]
    row = portfolio_payload(cfg, sync=False)["open"][0]
    assert row["strategy_id"] == "consensus"
    assert row["confidence_text"] == "67%"


def test_each_strategy_has_its_own_hourly_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    set_auto_settings(max_opens_per_hour=1, cfg=cfg)
    _arm(cfg, T0)
    assert _reenter(cfg, T0 + timedelta(seconds=1))["events"] == ["EURUSD open BUY"]
    _install(monkeypatch, store, _suggest(), 1.08)
    assert _stop_out(cfg, T0 + timedelta(seconds=30))["events"] == ["EURUSD close sl"]
    _install(monkeypatch, store, _suggest(), 1.10)
    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Sell", "Sell", "Neutral"))
    opened = run_auto_paper(cfg, rows=[_row("HOLD")], now=T0 + timedelta(minutes=2))
    assert opened["events"] == ["EURUSD open SELL consensus"]
    blocked = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=3))
    assert blocked["events"] == ["EURUSD skip rate"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=3))
    assert book["opens_this_hour"] == 1
    assert book["rate_status"] == "Rate-limited: 1/1 opens this hour"
    assert book["open"][0]["strategy_id"] == "consensus"
    cards = {item["id"]: item for item in book["strategies"]}
    assert cards["brief"]["opens_this_hour"] == 1
    assert cards["brief"]["rate_limited"] is True
    assert cards["consensus"]["opens_this_hour"] == 1
    assert cards["consensus"]["open_count"] == 1


def test_champion_is_manual_and_unknown_ids_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row()], now=T0)
    with pytest.raises(ValueError):
        set_auto_settings(champion="nope", cfg=cfg)
    set_auto_settings(champion="consensus", cfg=cfg)
    book = portfolio_payload(cfg, sync=False, now=T0)
    assert book["champion"] == "consensus"
    assert [item["id"] for item in book["strategies"] if item["champion"]] == ["consensus"]
    from forex_lab.broker import PaperBroker

    remembered = PaperBroker(store, cfg=cfg).read_auto()["seen"]["EURUSD"]["brief"]
    assert remembered == "BUY"

    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(tmp_path / "watchlist.yaml"))
    monkeypatch.setenv("FORX_ALERT_STATE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "consensus.json"))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    from api.limiter import reset as reset_limiter
    from api.main import create_app

    reset_limiter()
    client = TestClient(create_app())
    bad = client.post("/portfolio/auto", json={"champion": "nope"})
    assert bad.status_code == 400
    saved = client.post("/portfolio/auto", json={"champion": "mtf"})
    assert saved.status_code == 200
    assert saved.json()["champion"] == "mtf"
    assert client.get("/portfolio", params={"sync": "false"}).json()["champion"] == "mtf"
    client.post("/portfolio/auto", json={"champion": "brief"})


def test_champion_headline_follows_the_promoted_strategy():
    from api.deskdata import apply_champion_headline

    tone, bias, headline, sub = apply_champion_headline(
        "EURUSD",
        "1h",
        {"id": "consensus", "name": "Consensus", "signal": "SELL", "confidence_text": "67%"},
        "flat",
        "NO LIVE BIAS",
        "EURUSD — data stale, not a live call",
        "external forecasters MISSING",
    )
    assert tone == "sell"
    assert bias == "SELL bias"
    assert "Consensus" in headline
    assert sub.startswith("Champion Consensus · 67%")
    assert "external forecasters MISSING" in sub

    tone, bias, headline, sub = apply_champion_headline(
        "EURUSD",
        "1h",
        {"id": "brief", "name": "Brief", "signal": "BUY", "confidence_text": "70%"},
        "buy",
        "BUY bias",
        "EURUSD — bullish research bias on 1h",
        "Event before NFP",
    )
    assert bias == "BUY bias"
    assert headline == "EURUSD — bullish research bias on 1h"
    assert sub.startswith("Champion Brief · 70%")
    assert "Event before NFP" in sub


def test_comparison_counts_only_closed_trades_in_the_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    empty = portfolio_payload(cfg, sync=False, now=T0)
    assert empty["compare_window"] == "7d"
    for card in empty["strategies"]:
        assert card["trade_count"] == 0
        assert card["open_count"] == 0
        assert card["win_rate_text"] == "—"
        assert card["expectancy_text"] == "—"

    _arm(cfg, T0)
    assert _reenter(cfg, T0 + timedelta(minutes=1))["events"] == ["EURUSD open BUY"]
    _install(monkeypatch, store, _suggest(), 1.08)
    assert _stop_out(cfg, T0 + timedelta(minutes=2))["events"] == ["EURUSD close sl"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=2))
    brief = next(item for item in book["strategies"] if item["id"] == "brief")
    consensus = next(item for item in book["strategies"] if item["id"] == "consensus")
    assert brief["trade_count"] == 1
    assert brief["win_rate_text"] == "0%"
    assert brief["expectancy_text"] == "-2.00R"
    assert brief["open_count"] == 0
    assert consensus["trade_count"] == 0
    assert consensus["win_rate_text"] == "—"
    assert consensus["expectancy_text"] == "—"

    import json

    raw = json.loads(store.read_text(encoding="utf-8"))
    aged = dict(raw["closed"][0])
    aged["id"] = "pos_old"
    aged["exit_time"] = "2026-08-01 10:00:00 UTC"
    aged["outcome"] = "RIGHT"
    raw["closed"].append(aged)
    store.write_text(json.dumps(raw), encoding="utf-8")
    later = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=3))
    brief = next(item for item in later["strategies"] if item["id"] == "brief")
    assert brief["trade_count"] == 1
    assert brief["win_rate_text"] == "0%"


def test_mtf_agree_reuses_model_confidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from forex_lab.mtf import MTF_AGREE, MTF_CONFLICT, MtfStatus

    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    quiet = _row("HOLD")
    quiet.mtf = MtfStatus(status=MTF_AGREE, direction="up")
    assert run_auto_paper(cfg, rows=[quiet], now=T0)["events"] == []

    agreed = _row(confidence=0.70)
    agreed.mtf = MtfStatus(status=MTF_AGREE, direction="up")
    opened = run_auto_paper(cfg, rows=[agreed], now=T0 + timedelta(minutes=1))
    assert "EURUSD open BUY mtf" in opened["events"]
    mtf = next(row for row in portfolio_payload(cfg, sync=False)["open"] if row["strategy_id"] == "mtf")
    assert mtf["confidence"] == pytest.approx(0.70)
    assert mtf["trigger"] == "BUY"

    conflict = _row("SELL", confidence=0.90)
    conflict.mtf = MtfStatus(status=MTF_CONFLICT, direction="down")
    again = run_auto_paper(cfg, rows=[conflict], now=T0 + timedelta(minutes=2))
    assert not any(event.endswith("mtf") and "open" in event for event in again["events"])
    still = [row for row in portfolio_payload(cfg, sync=False)["open"] if row["strategy_id"] == "mtf"]
    assert len(still) == 1
    assert still[0]["trigger"] == "BUY"


def test_learnings_name_the_strategy_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Sell", "Sell", "Buy"))
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)
    run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=1))
    _install(monkeypatch, store, _suggest(), 1.08)
    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Sell", "Sell", "Buy"))
    closed = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=2))
    assert "EURUSD close sl" in closed["events"]
    learned = collect_learnings(cfg, limit=20)
    titles = [item["title"] for item in learned["items"]]
    assert any(title.startswith("EURUSD BUY paper WRONG") and title.endswith("Brief") for title in titles)
    detail = next(item["detail"] for item in learned["items"] if item["title"].endswith("Brief"))
    assert "strategy Brief" in detail


def test_legacy_row_without_strategy_id_blocks_only_brief(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = tmp_path / "paper.json"
    cfg = _cfg(store)
    _install(monkeypatch, store, _suggest(), 1.10)
    run_auto_paper(cfg, rows=[_row("HOLD")], now=T0)
    from forex_lab.broker import PaperBroker

    broker = PaperBroker(store, cfg=cfg)
    broker.submit(
        "BUY",
        "EURUSD",
        price=1.10,
        sl=1.09,
        tp=1.12,
        timestamp="2026-09-23 10:00:30 UTC",
        source="manual",
    )
    pos = broker.list_positions()[0]
    pos.pop("strategy_id", None)
    pos.pop("strategy_name", None)
    broker._state["positions"] = [pos]
    broker._save()

    monkeypatch.setattr("api.consensus.read_consensus", _dirs("Sell", "Sell", "Buy"))
    opened = run_auto_paper(cfg, rows=[_row()], now=T0 + timedelta(minutes=1))
    assert opened["events"] == ["EURUSD open SELL consensus"]
    book = portfolio_payload(cfg, sync=False, now=T0 + timedelta(minutes=1))
    brief_rows = [row for row in book["open"] if row["strategy_id"] == "brief"]
    consensus_rows = [row for row in book["open"] if row["strategy_id"] == "consensus"]
    assert len(brief_rows) == 1
    assert brief_rows[0]["source"] == "manual"
    assert len(consensus_rows) == 1
    assert consensus_rows[0]["source"] == "auto"
