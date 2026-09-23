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

    def _quiet_calendar(*_args, **_kwargs):
        from forex_lab.calendar import CalendarBundle

        return CalendarBundle()

    monkeypatch.setattr("api.deskdata.fetch_calendar", _quiet_calendar)
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
    assert len(body["forecasters"]) >= 10
    assert all(row["direction"] is None for row in body["forecasters"])
    assert all(row["status"] in {"MISSING", "SKIPPED", "RANGE"} for row in body["forecasters"])
    assert any(row["status"] == "SKIPPED" and row["reason"] for row in body["forecasters"])
    assert all(row["status"] == "MISSING" for row in body["ranges"])
    agg = body["aggregate"]
    assert agg["counts"] == {"Buy": 0, "Sell": 0, "Neutral": 0}
    assert agg["top_side"] is None
    assert agg["confidence"] is None
    assert agg["range_span"] is None

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
    assert old["fresh"] is False
    assert old["stale"] is True
    assert old["age_s"] >= 12 * 3600
    assert old["fetched_at_dhaka"] and "Asia/Dhaka" in old["fetched_at_dhaka"]
    by_old = {row["source"]: row for row in old["forecasters"]}
    assert by_old["DailyForex"]["status"] == "OK"
    assert by_old["DailyForex"]["direction"] == "Buy"


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


def test_ohlcv_indicators_align_with_bars(client: TestClient):
    from forex_lab.chart_indicators import INDICATOR_KEYS

    res = client.get("/ohlcv/EURUSD", params={"interval": "1h", "bars": 40})
    assert res.status_code == 200
    body = res.json()
    assert body["digits"] == 5
    assert len(body["bars"]) == 40
    indicators = body["indicators"]
    assert set(indicators) == set(INDICATOR_KEYS)
    for key, series in indicators.items():
        assert len(series) == len(body["bars"]), key
    assert any(value is not None for value in indicators["ema21"])
    assert any(value is not None for value in indicators["rsi"])
    assert any(value is not None for value in indicators["macd"])
    assert any(value is not None for value in indicators["macd_hist"])
    assert any(value is not None for value in indicators["atr"])
    checked = 0
    for macd, signal, hist in zip(indicators["macd"], indicators["macd_signal"], indicators["macd_hist"]):
        if macd is None or signal is None or hist is None:
            continue
        assert hist == pytest.approx(macd - signal)
        checked += 1
    assert checked > 0
    rsi_values = [value for value in indicators["rsi"] if value is not None]
    assert rsi_values and all(0.0 <= value <= 100.0 for value in rsi_values)

    missing = client.get("/ohlcv/GBPUSD", params={"interval": "1h", "bars": 30})
    miss = missing.json()
    assert miss["bars"] == []
    assert miss["digits"] == 5
    assert set(miss["indicators"]) == set(INDICATOR_KEYS)
    assert all(series == [] for series in miss["indicators"].values())


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
    assert body["daily"]["status"] == "need_fetch"
    daily_reason = str(body["daily"].get("validity_reason") or "")
    assert daily_reason
    assert daily_reason.startswith("Daily — failed")
    assert "no OHLCV cache" in daily_reason
    assert body["daily"]["scenario"]
    assert "Daily — failed" in body["daily"]["scenario"]
    hourly = body["hourly"]
    assert hourly["validity"] in {VALIDITY_OK, VALIDITY_STALE, "CLOSED", VALIDITY_MISSING}
    if hourly["validity"] in {VALIDITY_STALE, VALIDITY_MISSING}:
        assert hourly["signal"] is None
        assert hourly["chip"] in {"STALE", "MISSING"}
        assert (
            "not a live call" in hourly["scenario"]
            or "Fetch/Train" in hourly["scenario"]
            or "failed:" in hourly["scenario"]
        )
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
    assert body["data_refresh_seconds"] == 60
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
    monkeypatch.setattr(
        "forex_lab.data.resample_daily_cache_from_hourly",
        lambda *_a, **_k: (None, "resample skipped"),
    )
    first = client.post("/refresh/EURUSD", params={"interval": "1h"})
    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert first.json()["fetch_failed"] is True
    assert first.json()["ensured"] == ["1h", "1d"]
    assert calls["n"] == 2
    assert yf_calls["n"] == 2
    second = client.post("/refresh/EURUSD", params={"interval": "1h"})
    assert second.status_code == 200
    body = second.json()
    assert body["ok"] is True
    assert body["rate_limited"] is True
    assert body["retry_after_s"] > 0
    assert body["board"]["from_cache"] is True
    assert calls["refresh"] == [False, False, False, False]
    assert yf_calls["n"] == 2


def _board_row(pair: str, *_a, **_k):
    from types import SimpleNamespace

    return SimpleNamespace(
        pair=pair,
        timeframe=_k.get("interval") or "1h",
        buy_sell="—",
        raw_signal="SELL",
        close=1.1,
        validity="STALE",
        validity_reason="cached",
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


def test_data_refresh_seconds_follows_limiter(monkeypatch: pytest.MonkeyPatch):
    from api.limiter import data_refresh_seconds

    monkeypatch.delenv("FORX_REFRESH_MIN_S", raising=False)
    assert data_refresh_seconds() == 18
    monkeypatch.setenv("FORX_REFRESH_MIN_S", "18.2")
    assert data_refresh_seconds() == 19
    monkeypatch.setenv("FORX_REFRESH_MIN_S", "0")
    assert data_refresh_seconds() == 18


def test_refresh_watchlist_is_market_data_only(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """POST /refresh updates watched pairs and does not run the research pipeline."""
    yf_calls: list[tuple[str, str]] = []
    pipeline: list[str] = []

    def _yf(pair, _cfg, interval="1h", **_k):
        yf_calls.append((pair, interval))
        return object(), "yfinance"

    def _pipeline(*_a, **_k):
        pipeline.append("run")
        raise AssertionError("data refresh must not run the pipeline")

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    monkeypatch.setattr("api.deskdata.build_board_row", _board_row)
    monkeypatch.setattr("api.deskdata.run_pipeline_pair", _pipeline)
    body_pairs = [
        {"pair": "EURUSD", "interval": "1h"},
        {"pair": "GBPUSD", "interval": "1h"},
        {"pair": "EURUSD", "interval": "1h"},
    ]
    first = client.post("/refresh", json={"pairs": body_pairs})
    assert first.status_code == 200
    body = first.json()
    assert body["updated"] is True
    assert body["reason"] is None
    assert body["rate_limited"] is False
    assert body["data_refresh_seconds"] == 60
    assert [item["pair"] for item in body["results"]] == ["EURUSD", "GBPUSD"]
    assert yf_calls == [("EURUSD", "1h"), ("GBPUSD", "1h")]
    assert pipeline == []
    second = client.post("/refresh", json={"pairs": body_pairs})
    assert second.status_code == 200
    limited = second.json()
    assert limited["updated"] is False
    assert limited["rate_limited"] is True
    assert limited["reason"] == "rate_limited"
    assert limited["retry_after_s"] > 0
    assert all(item["source"] == "cache" for item in limited["results"])
    assert yf_calls == [("EURUSD", "1h"), ("GBPUSD", "1h")]
    assert pipeline == []


def test_refresh_watchlist_classifies_failures(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("api.deskdata.build_board_row", _board_row)

    def _limited(*_a, **_k):
        return None, "yfinance rate limited (HTTP 429)"

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _limited)
    limited = client.post("/refresh", json={"pairs": [{"pair": "EURUSD", "interval": "15m"}]}).json()
    assert limited["reason"] == "rate_limited"
    assert limited["updated"] is False
    assert limited["results"][0]["fetch_failed"] is True

    def _down(*_a, **_k):
        return None, "yfinance failed (connection timed out)"

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _down)
    failed = client.post("/refresh", json={"pairs": [{"pair": "GBPUSD", "interval": "1h"}]}).json()
    assert failed["reason"] == "error"
    assert failed["fetch_failed"] is True
    assert failed["updated"] is False


def test_refresh_empty_watchlist_does_not_fetch(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def _yf(*_a, **_k):
        raise AssertionError("empty refresh must not fetch")

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    res = client.post("/refresh", json={"pairs": []})
    assert res.status_code == 200
    body = res.json()
    assert body["results"] == []
    assert body["updated"] is True
    assert body["reason"] is None


def test_refresh_watchlist_defaults_to_saved_pairs(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "watchlist.yaml").write_text(
        "refresh_seconds: 60\npairs:\n- EURUSD\n- {pair: USDJPY, interval: 1d}\n",
        encoding="utf-8",
    )
    seen: list[tuple[str, str]] = []

    def _yf(pair, _cfg, interval="1h", **_k):
        seen.append((pair, interval))
        return object(), "yfinance"

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    monkeypatch.setattr("api.deskdata.build_board_row", _board_row)
    res = client.post("/refresh", json={})
    assert res.status_code == 200
    assert seen == [("EURUSD", "1h"), ("USDJPY", "1d")]
    assert [item["pair"] for item in res.json()["results"]] == ["EURUSD", "USDJPY"]


def test_refresh_active_fetches_1h_and_1d_only_for_that_pair(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Heavy H1+D1 fill is the Active subject. Other watchlist intervals stay single."""
    seen: list[tuple[str, str]] = []

    def _yf(pair, _cfg, interval="1h", **_k):
        seen.append((pair, interval))
        return object(), "yfinance"

    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    monkeypatch.setattr("api.deskdata.build_board_row", _board_row)
    res = client.post(
        "/refresh",
        json={
            "pairs": [
                {"pair": "GBPUSD", "interval": "1h"},
                {"pair": "USDJPY", "interval": "4h"},
            ],
            "active": "EURUSD",
        },
    )
    assert res.status_code == 200
    assert ("GBPUSD", "1h") in seen
    assert ("USDJPY", "4h") in seen
    assert ("GBPUSD", "1d") not in seen
    assert ("USDJPY", "1d") not in seen
    assert ("EURUSD", "1h") in seen
    assert ("EURUSD", "1d") in seen
    assert seen.index(("EURUSD", "1h")) < seen.index(("EURUSD", "1d"))


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
    assert h1["fetch_age"] == "—"
    assert h1["fetch_age_s"] is None


def test_fetch_age_is_last_refresh_and_forming_h1_stays_live():
    """Watchlist primary age is last successful fetch, not the open H1 bar."""
    from api.deskdata import fetch_age_label, row_json

    now = datetime(2026, 9, 22, 0, 30, 12, tzinfo=timezone.utc)
    cfg = {"board": {"stale_bars": 2}}
    forming = _age_row("1h", "2026-09-22 00:00:00 UTC", validity="OK")
    forming.last_fetch_at = "2026-09-22 00:30:00 UTC"
    body = row_json(forming, now=now, cfg=cfg)
    assert body["validity"] == VALIDITY_OK
    assert body["data"]["text"] == "Live"
    assert body["signal"] == "BUY"
    assert body["age"] == "30m"
    assert body["age_s"] == pytest.approx(1812, abs=1)
    assert body["fetch_age"] == "just now"
    assert body["fetch_age_s"] == pytest.approx(12, abs=1)

    two_min = _age_row("1h", "2026-09-22 00:00:00 UTC", validity="OK")
    two_min.last_fetch_at = "2026-09-22 00:28:00 UTC"
    older = row_json(two_min, now=now, cfg=cfg)
    assert older["fetch_age"] == "fetched 2m"
    assert older["validity"] == VALIDITY_OK
    assert older["data"]["text"] == "Live"
    assert older["age"] == "30m"

    missing = row_json(_age_row("1h", "2026-09-22 00:00:00 UTC"), now=now, cfg=cfg)
    assert missing["fetch_age"] == "—"
    assert missing["fetch_age_s"] is None
    assert missing["data"]["text"] == "Live"
    assert fetch_age_label(0) == "just now"
    assert fetch_age_label(59) == "just now"
    assert fetch_age_label(60) == "fetched 1m"
    assert fetch_age_label(None) == "—"


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
    assert sug["confidence"] is None
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
    assert gap["validity_reason"].startswith("Daily — failed")
    assert "no OHLCV cache" in gap["validity_reason"]
    assert gap["scenario"]
    assert "Daily — failed" in gap["scenario"]
    remembered = suggestion_from_row(
        SimpleNamespace(**{**missing.__dict__, "last_fetch_at": "2026-09-22 16:04:00 Asia/Dhaka"}),
        cfg,
        ohlcv=None,
    )
    assert "Last OK 2026-09-22 16:04:00 Asia/Dhaka" in remembered["validity_reason"]
    assert "Last OK 2026-09-22 16:04:00 Asia/Dhaka" in remembered["scenario"]

    untrained = SimpleNamespace(**{**missing.__dict__, "status": "need_train", "validity": "OK"})
    train = suggestion_from_row(untrained, cfg, ohlcv=None)
    assert train["stop"] is None
    assert train["stop_text"] == "need Train"

    no_barriers = suggestion_from_row(row, {**cfg, "label_scheme": "forward_return"}, ohlcv=df)
    assert no_barriers["stop"] is None
    assert no_barriers["stop_text"] == "no barriers"
    assert no_barriers["target_text"] == "no barriers"


def test_suggestion_passes_model_confidence_and_leaves_it_blank_when_missing():
    """Collapsed brief title reads this field. Do not invent 0 when the row has none."""
    from types import SimpleNamespace

    from api.deskdata import suggestion_from_row
    from forex_lab.data import generate_synthetic_ohlcv

    df = generate_synthetic_ohlcv(bars=40, seed=2)
    cfg = {"label_scheme": "forward_return", "horizon": 4}
    base = dict(
        pair="EURUSD",
        timeframe="1h",
        validity="OK",
        buy_sell="SELL",
        raw_signal="SELL",
        close=float(df["Close"].iloc[-1]),
        status="ready",
        rationale="",
        signal_details="",
        risk=None,
        quote=None,
    )
    priced = suggestion_from_row(SimpleNamespace(**base, confidence=0.7), cfg, ohlcv=df)
    assert priced["signal"] == "SELL"
    assert priced["confidence"] == pytest.approx(0.7)

    blank = suggestion_from_row(SimpleNamespace(**base, confidence=None), cfg, ohlcv=df)
    assert blank["confidence"] is None

    absent = suggestion_from_row(SimpleNamespace(**base), cfg, ohlcv=df)
    assert absent["confidence"] is None


def test_failed_refresh_is_not_live_and_keeps_age(monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from api.deskdata import refresh_pair

    now_bar = "2026-09-21 15:05:00 UTC"
    row = _age_row("1h", now_bar, validity="OK")

    def _yf(*_a, **_k):
        return None, "yfinance failed (offline)"

    def _board(*_a, **_k):
        return row

    calls: list[tuple] = []
    monkeypatch.setattr("api.deskdata.try_yfinance_refresh", _yf)
    monkeypatch.setattr("api.deskdata.build_board_row", _board)
    monkeypatch.setattr("api.deskdata.ensure_consensus", lambda *a, **_k: calls.append(a))
    out = refresh_pair("EURUSD", interval="1h", cfg={"interval": "1h", "board": {"stale_bars": 2}})
    assert calls == []
    assert out["fetch_failed"] is True
    assert out["row"]["data"]["text"] == "STALE"
    assert out["row"]["session"]["text"] == "NY"
    assert out["row"]["age"] not in {"", "—"}
    assert "refresh failed" in out["row"]["validity_reason"]
