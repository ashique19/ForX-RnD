"""Replay / history job API. Network is stubbed — no invented prices in the handler."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.limiter import reset as reset_limiter


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FORX_WATCHLIST_PATH", str(tmp_path / "watchlist.yaml"))
    monkeypatch.setenv("FORX_ALERT_STATE", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("FORX_CONSENSUS_CACHE", str(tmp_path / "consensus.json"))
    monkeypatch.setenv("FORX_CONSENSUS_NETWORK", "0")
    monkeypatch.setenv("FORX_PAPER_STORE", str(tmp_path / "paper.json"))
    monkeypatch.setenv("FORX_REPLAY_DIR", str(tmp_path / "replay"))
    monkeypatch.setenv("FORX_HISTORY_DIR", str(tmp_path / "history"))
    monkeypatch.setenv("FORX_REFRESH_MIN_S", "60")
    reset_limiter()
    from api.main import create_app

    def _quiet_calendar(*_args, **_kwargs):
        from forex_lab.calendar import CalendarBundle

        return CalendarBundle()

    monkeypatch.setattr("api.deskdata.fetch_calendar", _quiet_calendar)
    from api.replayjob import reset_jobs

    reset_jobs()
    return TestClient(create_app())


def test_replay_train_job_status_and_scoreboard(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    def fake_status(*_a, **_k):
        return {"stale": True, "source": None, "rows": 0, "bid_ask": False}

    def fake_pull(*_a, **kwargs):
        progress = kwargs.get("progress")
        if progress:
            progress({"phase": "pull", "fraction": 1.0, "message": "stub pull"})
        return {"source": "dukascopy", "rows": 4, "bid_ask": True}

    def fake_meta(*_a, **_k):
        return {"source": "dukascopy"}

    def fake_load(*_a, **_k):
        idx = pd.date_range("2015-01-05", periods=4, freq="h")
        return pd.DataFrame(
            {"Open": [1.1, 1.2, 1.3, 1.4], "High": 1.2, "Low": 1.0, "Close": [1.1, 1.2, 1.3, 1.4], "Volume": 1},
            index=idx,
        )

    def fake_run(_df, _cfg, pair, **kwargs):
        folder = Path(kwargs["job_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scoreboard.csv").write_text("book,net_pnl\nchampion,0.1\n", encoding="utf-8")
        (folder / "scoreboard.xlsx").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (folder / "equity.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (folder / "report.md").write_text("# scoreboard\n", encoding="utf-8")
        return {
            "source": "dukascopy",
            "bid_ask": True,
            "rows": 4,
            "promotion_line": "Promotion null: stub",
            "end_dhaka": "2015-01-05 10:00 Asia/Dhaka",
            "pair": pair,
        }

    monkeypatch.setattr("api.replayjob.history_status", fake_status)
    monkeypatch.setattr("api.replayjob.pull_history", fake_pull)
    monkeypatch.setattr("api.replayjob.load_meta", fake_meta)
    monkeypatch.setattr("api.replayjob.load_history", fake_load)
    monkeypatch.setattr("api.replayjob.run_replay", fake_run)

    started = client.post(
        "/replay/train",
        json={"pair": "EURUSD", "interval": "1h", "start": "2015-01-01", "end": "2015-02-01", "pull": True},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["status"] == "running"
    assert body["pair"] == "EURUSD"
    assert "current-week" in body["calendar_note"]

    from api.replayjob import wait_job

    done = wait_job(body["job_id"], timeout=10)
    assert done["status"] == "done"
    assert done["promotion_line"] == "Promotion null: stub"
    assert done["report"]["csv"].endswith("format=csv")

    csv = client.get(done["report"]["csv"])
    assert csv.status_code == 200
    assert "champion" in csv.text
    png = client.get(done["report"]["equity_png"])
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG")
    # Live paper store was not created by the stubbed replay.
    assert not (tmp_path / "paper.json").exists()


def test_history_pull_and_bad_pair(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    def fake_pull(*_a, **kwargs):
        progress = kwargs.get("progress")
        if progress:
            progress({"phase": "pull", "fraction": 0.5, "message": "hour 1"})
        return {"source": "dukascopy", "rows": 2, "bid_ask": True}

    monkeypatch.setattr("api.replayjob.pull_history", fake_pull)
    started = client.post("/history/pull", json={"pair": "gbpusd", "interval": "1h", "start": "2015-01-01"})
    assert started.status_code == 200
    from api.replayjob import wait_job

    done = wait_job(started.json()["job_id"], timeout=10)
    assert done["status"] == "done"
    assert done["source"] == "dukascopy"
    assert done["rows"] == 2

    bad = client.post("/replay/train", json={"pair": "ABCDEF", "interval": "1h"})
    assert bad.status_code == 400
    missing = client.get("/replay/jobs/nope")
    assert missing.status_code == 404


def test_historic_train_is_one_active_pair(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import threading

    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def fake_pull(pair, *_a, **_k):
        calls.append(str(pair))
        started.set()
        assert release.wait(5)
        return {"source": "dukascopy", "rows": 1, "bid_ask": True}

    monkeypatch.setattr("api.replayjob.pull_history", fake_pull)
    first = client.post("/history/pull", json={"pair": "EURUSD", "interval": "1h", "start": "2015-01-05"})
    assert first.status_code == 200
    assert started.wait(5)
    again = client.post("/history/pull", json={"pair": "EURUSD", "interval": "1h", "start": "2015-01-05"})
    assert again.status_code == 200
    assert again.json()["job_id"] == first.json()["job_id"]
    other = client.post("/replay/train", json={"pair": "GBPUSD", "interval": "1h", "pull": True})
    assert other.status_code == 409
    assert "EURUSD" in other.json()["detail"]
    assert calls == ["EURUSD"]
    batch = client.post("/replay/train", json={"pair": "EURUSD,GBPUSD", "interval": "1h"})
    assert batch.status_code == 400
    assert "one Active pair" in batch.json()["detail"]
    listed = client.post("/history/pull", json={"pair": ["EURUSD", "GBPUSD"], "interval": "1h"})
    assert listed.status_code == 422
    extra = client.post("/replay/train", json={"pair": "EURUSD", "pairs": ["GBPUSD"]})
    assert extra.status_code == 422
    release.set()
    from api.replayjob import wait_job

    done = wait_job(first.json()["job_id"], timeout=10)
    assert done["status"] == "done"
    assert calls == ["EURUSD"]
