"""Decision API: watchlist, board honesty, consensus MISSING, OHLCV cache."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.consensus import read_consensus
from api.limiter import reset as reset_limiter
from forex_lab.freshness import VALIDITY_MISSING, VALIDITY_OK, VALIDITY_STALE


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(tmp_path / "watchlist.yaml"))
    monkeypatch.setenv("FORX_ALERT_STATE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "consensus.json"))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    monkeypatch.setenv("FORX_PAPER_STORE", str(tmp_path / "paper.json"))
    monkeypatch.setenv("FORX_REFRESH_MIN_S", "60")
    reset_limiter()
    from api.main import create_app

    return TestClient(create_app())


def test_health_reports_dhaka_and_lab_version(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["timezone"] == "Asia/Dhaka"
    assert "Asia/Dhaka" in body["now_dhaka"]
    assert body["forex_lab"]
    assert "8501" in body["streamlit"]


def test_watchlist_add_and_remove(client: TestClient):
    res = client.get("/watchlist")
    assert res.status_code == 200
    symbols = [p["pair"] for p in res.json()["pairs"]]
    assert "EURUSD" in symbols

    added = client.post("/watchlist", json={"pair": "gbp/usd", "interval": "4h"})
    assert added.status_code == 200
    pairs = {p["pair"]: p for p in added.json()["pairs"]}
    assert pairs["GBPUSD"]["interval"] == "4h"
    assert pairs["GBPUSD"]["tf"] == "H4"

    bad = client.post("/watchlist", json={"pair": "EUR", "interval": "1h"})
    assert bad.status_code == 400

    removed = client.delete("/watchlist/GBPUSD")
    assert removed.status_code == 200
    assert "GBPUSD" not in [p["pair"] for p in removed.json()["pairs"]]

    assets = client.get("/assets")
    assert assets.status_code == 200
    names = [item["pair"] for item in assets.json()["assets"]]
    assert "EURUSD" in names
    assert "XAUUSD" in names
    assert "GBPUSD" in names
    junk = client.post("/watchlist", json={"pair": "ABCDEF", "interval": "1h"})
    assert junk.status_code == 400


def test_consensus_missing_without_cache(client: TestClient):
    res = client.get("/consensus/eurusd", params={"horizon": "hourly"})
    assert res.status_code == 200
    body = res.json()
    assert body["pair"] == "EURUSD"
    assert body["status"] == "MISSING"
    assert body["forecasters"]
    assert all(row["status"] == "MISSING" and row["direction"] is None for row in body["forecasters"])
    assert all(row["status"] == "MISSING" for row in body["ranges"])
    blob = json.dumps(body)
    assert "Buy" not in blob
    assert "Sell" not in blob

    bad = client.get("/consensus/EURUSD", params={"horizon": "weekly"})
    assert bad.status_code == 400


def test_consensus_cache_ok_and_stale_and_garbage(tmp_path: Path):
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    fresh = {
        "EURUSD": {
            "hourly": {
                "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "forecasters": [
                    {"source": "DailyForex", "direction": "Buy"},
                    {"source": "Investing.com", "direction": "Sell"},
                    {"source": "FXStreet", "direction": "moon"},
                ],
                "ranges": [
                    {"source": "DailyForex", "low": 1.08, "high": 1.09, "window": "within session"},
                    {"source": "Investing.com", "low": 1.2, "high": 1.1, "window": "bad"},
                ],
            }
        }
    }
    snap = read_consensus("EURUSD", "hourly", cache=fresh, now=now)
    by_src = {row["source"]: row for row in snap["forecasters"]}
    assert by_src["DailyForex"]["status"] == "OK"
    assert by_src["DailyForex"]["direction"] == "Buy"
    assert by_src["FXStreet"]["status"] == "MISSING"
    assert by_src["FXStreet"]["direction"] is None
    ranges = {row["source"]: row for row in snap["ranges"]}
    assert ranges["DailyForex"]["status"] == "OK"
    assert ranges["Investing.com"]["status"] == "MISSING"
    assert snap["status"] == "PARTIAL"

    stale_at = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    stale = {"EURUSD": {"daily": {"fetched_at": stale_at, "forecasters": [{"source": "DailyForex", "direction": "Buy"}]}}}
    old = read_consensus("EURUSD", "daily", cache=stale, now=now)
    assert old["status"] == "MISSING"
    assert all(row["direction"] is None for row in old["forecasters"])
    assert all("stale" in row["reason"] for row in old["forecasters"])


def test_ohlcv_uses_cache_and_missing_pair(client: TestClient):
    res = client.get("/ohlcv/EURUSD", params={"interval": "1h", "bars": 40})
    assert res.status_code == 200
    body = res.json()
    assert body["pair"] == "EURUSD"
    assert body["tf"] == "H1"
    assert len(body["bars"]) == 40
    bar = body["bars"][-1]
    assert set(bar) >= {"time", "open", "high", "low", "close", "volume"}
    assert body["validity"] in {VALIDITY_OK, VALIDITY_STALE, "CLOSED"}
    if body["validity"] == VALIDITY_STALE:
        assert "STALE" in body["note"]

    missing = client.get("/ohlcv/GBPUSD", params={"interval": "1h", "bars": 30})
    assert missing.status_code == 200
    miss = missing.json()
    assert miss["validity"] == VALIDITY_MISSING
    assert miss["bars"] == []
    assert "invented" in miss["note"].lower() or "MISSING" in miss["note"]


def test_brief_does_not_invent_daily_or_live_call_when_stale(client: TestClient):
    res = client.get("/brief/EURUSD", params={"tf": "1h"})
    assert res.status_code == 200
    body = res.json()
    assert body["pair"] == "EURUSD"
    assert body["hourly"]["interval"] == "1h"
    assert body["daily"]["interval"] == "1d"
    # No 1d cache in the repo — daily must not look like a live suggestion.
    assert body["daily"]["validity"] == VALIDITY_MISSING
    assert body["daily"]["signal"] is None
    assert body["daily"]["stop"] is None
    assert body["daily"]["target"] is None
    assert "Fetch" in body["daily"]["stop_text"]
    assert body["daily"]["duration"] != "—"
    assert body["daily"]["chip"] == "MISSING"
    assert "Fetch" in body["daily"]["scenario"]
    hourly = body["hourly"]
    assert hourly["validity"] in {VALIDITY_OK, VALIDITY_STALE, "CLOSED", VALIDITY_MISSING}
    if hourly["validity"] in {VALIDITY_STALE, VALIDITY_MISSING}:
        assert hourly["signal"] is None
        assert hourly["chip"] in {"STALE", "MISSING"}
        assert "not a live call" in hourly["scenario"] or "Fetch/Train" in hourly["scenario"]
    if hourly.get("now") is not None and hourly["validity"] != VALIDITY_MISSING:
        assert isinstance(hourly["stop"], float)
        assert isinstance(hourly["target"], float)
        assert hourly["stop_text"] != "—"
        assert hourly["target_text"] != "—"
        assert hourly["duration"] not in {"", "—"}
        assert hourly["horizon_bars"]
    elif hourly["validity"] == VALIDITY_MISSING:
        assert hourly["stop"] is None
        assert "Fetch" in hourly["stop_text"] or "Train" in hourly["stop_text"]
    assert body["consensus"]["hourly"]["status"] == "MISSING"
    assert "external forecasters MISSING" in body["sub"]
    assert "Asia/Dhaka" not in json.dumps(body["consensus"])


def test_board_rows_mark_stale_or_missing_honestly(client: TestClient):
    res = client.get("/board")
    assert res.status_code == 200
    body = res.json()
    assert "Asia/Dhaka" in body["refreshed_at_dhaka"]
    assert body["rows"]
    row = next(r for r in body["rows"] if r["pair"] == "EURUSD")
    assert row["tf"]
    assert row["last_bar_dhaka"]
    assert row["data"]["text"] in {"Live", "STALE", "MISSING", "Closed", "ERROR"}
    if row["validity"] in {VALIDITY_STALE, VALIDITY_MISSING}:
        assert row["signal"] == "—"
        assert row["data"]["text"] in {"STALE", "MISSING"}
        assert row["target"] is None
        assert any(row["validity"] in a["message"] for a in body["alerts"])


def test_session_alert_is_clock_only():
    from api.deskdata import session_alert

    london = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)  # Monday, before NY
    note = session_alert(now=london)
    assert note is not None
    assert note.startswith("USD session open in")
    ny = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)
    assert session_alert(now=ny) is None
    saturday = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    assert session_alert(now=saturday) == "FX session closed"


def test_refresh_is_rate_limited(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    calls = {"n": 0, "refresh": []}

    def _fake_row(*_a, **_k):
        calls["n"] += 1
        calls["refresh"].append(bool(_k.get("refresh_data")))
        from types import SimpleNamespace

        return SimpleNamespace(
            pair="EURUSD",
            timeframe="1h",
            buy_sell="—",
            raw_signal="SELL",
            close=1.1,
            validity="STALE",
            validity_reason="data stale — refresh required",
            status="stale",
            rationale="",
            signal_details="",
            confidence=None,
            last_bar_at=None,
            last_fetch_at=None,
            last_signal_at=None,
            session=None,
            quote=None,
            risk=None,
            mtf=None,
            data_source="cached (test)",
        )

    yf_calls = {"n": 0}

    def _yf(*_a, **_k):
        yf_calls["n"] += 1
        return None, "yfinance failed (test)"

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    monkeypatch.setattr("api.deskdata.build_board_row", _fake_row)
    first = client.post("/refresh/EURUSD", params={"interval": "1h"})
    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert first.json()["fetch_failed"] is True
    assert calls["n"] == 1
    assert yf_calls["n"] == 1
    second = client.post("/refresh/EURUSD", params={"interval": "1h"})
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["rate_limited"] is True
    assert body["retry_after_s"] > 0
    assert body["board"]["from_cache"] is True
    assert calls["refresh"] == [False, False]
    assert yf_calls["n"] == 1


def test_pipeline_stops_on_train_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    order: list[str] = []

    def _train(pair, cfg=None):
        order.append("train")
        return 1, "train failed"

    def _backtest(pair, cfg=None, model=None):
        order.append("backtest")
        return 0, "should not run"

    def _signals(pair, cfg=None):
        order.append("signals")
        return 0, "should not run"

    monkeypatch.setattr("forex_lab.ui.pipeline.run_train", _train)
    monkeypatch.setattr("forex_lab.ui.pipeline.run_backtest", _backtest)
    monkeypatch.setattr("forex_lab.ui.pipeline.run_signals", _signals)
    res = client.post("/pipeline/EURUSD")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["failed"] == "train"
    assert order == ["train"]
    assert body["steps"][0]["step"] == "train"


def _paper_row(validity: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        pair="EURUSD",
        timeframe="1h",
        validity=validity,
        buy_sell="BUY" if validity == "OK" else "—",
        raw_signal="BUY",
        close=1.1,
        confidence=None,
        dir_edge=None,
        p_buy=None,
        p_sell=None,
        p_hold=None,
        rationale="",
        last_bar_at="",
        gate_blocked=False,
        gate_reason="",
    )


def test_paper_buy_blocked_when_stale(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("api.deskdata.build_board_row", lambda *_a, **_k: _paper_row("STALE"))
    res = client.post("/paper/order", json={"pair": "EURUSD", "side": "BUY", "size": 1})
    assert res.status_code == 409
    assert "STALE" in res.json()["detail"]


def test_paper_buy_and_close_when_ok(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("api.deskdata.build_board_row", lambda *_a, **_k: _paper_row("OK"))
    opened = client.post("/paper/order", json={"pair": "EURUSD", "side": "BUY", "size": 1})
    assert opened.status_code == 200
    body = opened.json()
    assert body["ok"] is True
    assert "local journal" in body["message"]
    pos = body["paper"]["position"]
    assert pos["side"] == "BUY"
    assert "Asia/Dhaka" in pos["entry_time_dhaka"]
    assert body["paper"]["allowed"] is False
    closed = client.post("/paper/order", json={"pair": "EURUSD", "side": "CLOSE"})
    assert closed.status_code == 200
    assert closed.json()["paper"]["position"] is None
    assert (tmp_path / "paper.json").exists()


def _age_row(interval: str, last_bar_at: str, validity: str = "OK"):
    from types import SimpleNamespace

    return SimpleNamespace(
        pair="EURUSD",
        timeframe=interval,
        buy_sell="BUY",
        raw_signal="BUY",
        close=1.14705,
        validity=validity,
        validity_reason="",
        status="ready",
        rationale="",
        signal_details="",
        confidence=None,
        last_bar_at=last_bar_at,
        last_fetch_at=None,
        last_signal_at=None,
        session=SimpleNamespace(name="ny", labels=["ny"]),
        quote=None,
        risk=None,
        mtf=None,
    )


def test_live_label_agrees_with_bar_age_and_session_stays_separate():
    from api.deskdata import row_json

    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    bar = "2026-09-21 15:05:00 UTC"  # 55 minutes old
    m15 = row_json(_age_row("15m", bar), now=now, cfg={"board": {"stale_bars": 2}})
    h1 = row_json(_age_row("1h", bar), now=now, cfg={"board": {"stale_bars": 2}})
    assert m15["age"] == "55m"
    assert m15["data"]["text"] == "STALE"
    assert m15["validity"] == VALIDITY_STALE
    assert m15["signal"] == "—"
    assert h1["data"]["text"] == "Live"
    assert h1["validity"] == VALIDITY_OK
    assert h1["session"]["text"] == "NY"
    assert h1["session"]["text"] not in h1["data"]["text"]
    assert h1["age"] == "55m"


def test_hold_brief_uses_research_barriers_when_price_exists():
    from types import SimpleNamespace

    from api.deskdata import suggestion_from_row
    from forex_lab.data import generate_synthetic_ohlcv

    df = generate_synthetic_ohlcv(bars=80, seed=1)
    close = float(df["Close"].iloc[-1])
    cfg = {
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
        "entry_timing": "next_open",
        "spread_pips": 1.0,
    }
    row = SimpleNamespace(
        pair="EURUSD",
        timeframe="1h",
        validity="OK",
        buy_sell="HOLD",
        raw_signal="HOLD",
        close=close,
        status="ready",
        rationale="",
        signal_details="",
        risk=None,
        quote=None,
    )
    sug = suggestion_from_row(row, cfg, ohlcv=df)
    assert sug["signal"] is None
    assert sug["chip"] == "HOLD"
    assert sug["now"] == pytest.approx(close)
    assert isinstance(sug["stop"], float) and sug["stop"] < close
    assert isinstance(sug["target"], float) and sug["target"] > close
    assert sug["stop_text"] != "—"
    assert sug["duration"].startswith("≤")
    assert sug["horizon_bars"] == 8
    assert "not an order" in sug["scenario"]

    missing = SimpleNamespace(
        pair="EURUSD",
        timeframe="1d",
        validity="MISSING",
        buy_sell="—",
        raw_signal=None,
        close=None,
        status="need_fetch",
        rationale="",
        signal_details="",
        risk=None,
        quote=None,
        validity_reason="no OHLCV cache — Fetch required",
    )
    gap = suggestion_from_row(missing, cfg, ohlcv=None)
    assert gap["stop"] is None
    assert gap["target"] is None
    assert gap["stop_text"] == "need Fetch"
    assert gap["duration"] == "need Fetch"

    untrained = SimpleNamespace(**{**missing.__dict__, "status": "need_train", "validity": "OK"})
    train = suggestion_from_row(untrained, cfg, ohlcv=None)
    assert train["stop"] is None
    assert train["stop_text"] == "need Train"

    no_barriers = suggestion_from_row(row, {**cfg, "label_scheme": "forward_return"}, ohlcv=df)
    assert no_barriers["stop"] is None
    assert no_barriers["stop_text"] == "no barriers"
    assert no_barriers["target_text"] == "no barriers"


def test_failed_refresh_is_not_live_and_keeps_age(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from api.deskdata import refresh_pair

    now_bar = "2026-09-21 15:05:00 UTC"
    row = _age_row("1h", now_bar, validity="OK")

    def _yf(*_a, **_k):
        return None, "yfinance failed (offline)"

    def _board(*_a, **_k):
        return row

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    monkeypatch.setattr("api.deskdata.build_board_row", _board)
    monkeypatch.setattr("api.deskdata.ensure_consensus", lambda *_a, **_k: None)
    out = refresh_pair("EURUSD", interval="1h", cfg={"interval": "1h", "board": {"stale_bars": 2}})
    assert out["fetch_failed"] is True
    assert out["row"]["data"]["text"] == "STALE"
    assert out["row"]["session"]["text"] == "NY"
    assert out["row"]["age"] not in {"", "—"}
    assert "refresh failed" in out["row"]["validity_reason"]
