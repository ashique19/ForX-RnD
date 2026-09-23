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
    assert "weekly retrain gate" in done["advisory"]
    assert "does not overwrite the live champion" in done["advisory"]
    assert done["scoreboard"][0]["book"] == "champion"
    assert done["scoreboard"][0]["net_pnl"] == 0.1
    assert "Asia/Dhaka" in (done.get("finished_at_dhaka") or "")

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


def test_job_status_names_the_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from forex_lab.history import HistoryError, explain_failure
    from forex_lab.replay import ReplayError
    from api.replayjob import wait_job

    blank_reason, blank_text = explain_failure(RuntimeError())
    assert blank_text.strip()
    assert blank_reason == "error"
    assert "RuntimeError" in blank_text

    def boom(*_a, **_k):
        raise HistoryError("Dukascopy unreachable for https://datafeed.dukascopy.com/example", reason="download")

    monkeypatch.setattr("api.replayjob.pull_history", boom)
    started = client.post("/history/pull", json={"pair": "EURUSD", "interval": "1h", "start": "2015-01-05"})
    assert started.status_code == 200
    done = wait_job(started.json()["job_id"], timeout=10)
    assert done["status"] == "error"
    assert done["reason"] == "download"
    assert done["error"].startswith("Download failed")
    assert "unreachable" in done["error"]
    assert done["message"] == done["error"]

    def no_bars(*_a, **_k):
        raise ReplayError("Not enough settled labels for EURUSD (need 500 trainable rows).", reason="insufficient_bars")

    monkeypatch.setattr("api.replayjob.history_status", lambda *_a, **_k: {"stale": False, "source": "dukascopy", "rows": 2, "bid_ask": True})
    monkeypatch.setattr("api.replayjob.load_meta", lambda *_a, **_k: {"source": "dukascopy"})
    monkeypatch.setattr("api.replayjob.load_history", lambda *_a, **_k: pd.DataFrame({"Open": [1.0]}))
    monkeypatch.setattr("api.replayjob.run_replay", no_bars)
    replay = client.post("/replay/train", json={"pair": "GBPUSD", "interval": "1h", "pull": False})
    assert replay.status_code == 200
    trained = wait_job(replay.json()["job_id"], timeout=10)
    assert trained["reason"] == "insufficient_bars"
    assert trained["error"].startswith("Not enough bars")
    assert "500" in trained["error"]

    def train_blew(*_a, **_k):
        raise ReplayError("Train failed (xgboost): feature mismatch", reason="train")

    monkeypatch.setattr("api.replayjob.run_replay", train_blew)
    again = client.post("/replay/train", json={"pair": "USDJPY", "interval": "1h", "pull": False})
    finished = wait_job(again.json()["job_id"], timeout=10)
    assert finished["reason"] == "train"
    assert "feature mismatch" in finished["error"]
    assert finished["error"].strip()


def test_compact_scoreboard_rejects_non_finite_numbers():
    import json

    from api.replayjob import compact_scoreboard

    rows = compact_scoreboard(
        [
            {"book": "sma", "n_trades": 1, "profit_factor": float("nan"), "net_pnl": 0.0, "promotion": "baseline"},
            {
                "book": "challenger",
                "n_trades": "2",
                "profit_factor": float("inf"),
                "win_rate": 0.5,
                "expectancy": 0.01,
                "net_pnl": 0.02,
                "max_drawdown": -0.1,
                "total_return": 0.02,
                "promotion": "null",
            },
            {"book": "champion", "n_trades": 3, "profit_factor": 1.25, "net_pnl": -0.01, "promotion": "incumbent"},
        ]
    )
    assert [row["book"] for row in rows] == ["champion", "challenger", "sma"]
    assert rows[0]["profit_factor"] == 1.25
    assert rows[1]["profit_factor"] == "inf"
    assert rows[1]["n_trades"] == 2
    assert rows[2]["profit_factor"] is None
    text = json.dumps(rows)
    assert "Infinity" not in text
    assert "NaN" not in text


def _write_replay(root: Path, job_id: str, pair: str, interval: str, *, when: float, status: str = "done", verdict: str = "null", pf: str = "1.8") -> None:
    import json

    folder = root / job_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": job_id,
        "kind": "replay",
        "status": status,
        "phase": "done" if status == "done" else status,
        "pair": pair,
        "interval": interval,
        "fraction": 1.0,
        "message": "Scoreboard ready" if status == "done" else "Train failed (xgboost): feature mismatch",
        "error": None if status == "done" else "Train failed (xgboost): feature mismatch",
        "reason": None if status == "done" else "train",
        "as_of_dhaka": "2015-06-01 18:00 Asia/Dhaka",
        "finished_at_dhaka": "2026-09-23 21:05 Asia/Dhaka",
        "promotion_line": f"Promotion {verdict}: stub",
        "updated_at": when,
        "report": {
            "csv": f"/replay/jobs/{job_id}/scoreboard?format=csv",
            "xlsx": f"/replay/jobs/{job_id}/scoreboard?format=xlsx",
            "equity_png": f"/replay/jobs/{job_id}/equity",
            "report_md": f"/replay/jobs/{job_id}/report",
        },
    }
    (folder / "status.json").write_text(json.dumps(payload), encoding="utf-8")
    if status != "done":
        return
    csv_text = (
        "book,n_trades,win_rate,expectancy,net_pnl,max_drawdown,profit_factor,total_return,promotion\n"
        f"sma,4,0.25,-0.002,-0.008,-0.12,0.40,-0.02,baseline\n"
        f"challenger,9,0.555,0.004,0.036,-0.03,{pf},0.09,{verdict}\n"
        "champion,12,0.50,0.001,0.012,-0.04,1.30,0.04,incumbent\n"
    )
    (folder / "scoreboard.csv").write_text(csv_text, encoding="utf-8")
    (folder / "promotion.json").write_text(
        json.dumps(
            {
                "verdict": verdict,
                "promote": verdict == "promote",
                "reasons": ["PF, total return, and max DD all improved vs champion (research sample only)"]
                if verdict == "promote"
                else ["keep champion and report null"],
                "mode": "improve",
            }
        ),
        encoding="utf-8",
    )
    (folder / "equity.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (folder / "report.md").write_text("# scoreboard\n", encoding="utf-8")


def test_latest_replay_scoreboard_for_active_pair(client: TestClient, tmp_path: Path):
    root = tmp_path / "replay"
    older = "a" * 32
    newer = "b" * 32
    other = "c" * 32
    pull = "d" * 32
    _write_replay(root, older, "EURUSD", "1h", when=10, verdict="null")
    _write_replay(root, newer, "EURUSD", "1h", when=20, verdict="promote", pf="inf")
    _write_replay(root, other, "GBPUSD", "1h", when=30, verdict="null")
    pull_dir = root / pull
    pull_dir.mkdir(parents=True, exist_ok=True)
    (pull_dir / "status.json").write_text(
        '{"job_id":"%s","kind":"pull","status":"done","pair":"EURUSD","interval":"1h","updated_at":99}' % pull,
        encoding="utf-8",
    )

    empty = client.get("/replay/latest", params={"pair": "USDJPY"})
    assert empty.status_code == 200
    assert empty.json()["job"] is None
    assert "weekly retrain gate" in empty.json()["advisory"]

    body = client.get("/replay/latest", params={"pair": "eurusd", "interval": "1h"})
    assert body.status_code == 200
    assert "Infinity" not in body.text
    assert "NaN" not in body.text
    payload = body.json()
    assert payload["pair"] == "EURUSD"
    assert payload["interval_match"] is True
    assert payload["job"]["job_id"] == newer
    assert payload["recent_error"] is None
    assert payload["running"] is None
    books = payload["job"]["scoreboard"]
    assert [row["book"] for row in books] == ["champion", "challenger", "sma"]
    challenger = books[1]
    assert challenger["n_trades"] == 9
    assert challenger["win_rate"] == 0.555
    assert challenger["expectancy"] == 0.004
    assert challenger["net_pnl"] == 0.036
    assert challenger["max_drawdown"] == -0.03
    assert challenger["profit_factor"] == "inf"
    assert payload["job"]["promotion"]["verdict"] == "promote"
    assert payload["job"]["promotion"]["promote"] is True
    assert "advisory" in payload["advisory"]
    assert "weekly retrain gate" in payload["job"]["advisory"]
    assert "Asia/Dhaka" in payload["job"]["finished_at_dhaka"]

    direct = client.get(f"/replay/jobs/{newer}")
    assert direct.status_code == 200
    assert direct.json()["scoreboard"][1]["profit_factor"] == "inf"
    png = client.get(payload["job"]["report"]["equity_png"])
    assert png.status_code == 200

    mismatch = client.get("/replay/latest", params={"pair": "EURUSD", "interval": "4h"})
    assert mismatch.status_code == 200
    assert mismatch.json()["interval_match"] is False
    assert mismatch.json()["job"]["interval"] == "1h"

    refused = client.get("/replay/latest", params={"pair": "ALL"})
    assert refused.status_code == 400
    bad_iv = client.get("/replay/latest", params={"pair": "EURUSD", "interval": "2h"})
    assert bad_iv.status_code == 400


def test_latest_replay_surfaces_newer_error_and_dead_run(client: TestClient, tmp_path: Path):
    root = tmp_path / "replay"
    done = "e" * 32
    failed = "f" * 32
    dead = "1" * 32
    _write_replay(root, done, "EURUSD", "1h", when=5)
    _write_replay(root, failed, "EURUSD", "1h", when=6, status="error")
    dead_dir = root / dead
    dead_dir.mkdir(parents=True)
    (dead_dir / "status.json").write_text(
        '{"job_id":"%s","kind":"replay","status":"running","phase":"replay","pair":"EURUSD","interval":"1h","updated_at":7,"message":"Walking bars"}'
        % dead,
        encoding="utf-8",
    )

    payload = client.get("/replay/latest", params={"pair": "EURUSD", "interval": "1h"}).json()
    assert payload["job"]["job_id"] == done
    assert payload["job"]["scoreboard"][0]["book"] == "champion"
    # The dead run is newer than the train error, and both are newer than the success.
    assert payload["recent_error"]["job_id"] == dead
    assert payload["recent_error"]["status"] == "error"
    assert "desk restarted" in payload["recent_error"]["error"]
    assert payload["running"] is None

    older_only = client.get("/replay/latest", params={"pair": "GBPUSD"}).json()
    assert older_only["job"] is None
    assert older_only["recent_error"] is None


def test_latest_replay_reports_a_live_run(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    import threading

    started = threading.Event()
    release = threading.Event()

    def fake_status(*_a, **_k):
        return {"stale": False, "source": "dukascopy", "rows": 4, "bid_ask": True}

    def fake_meta(*_a, **_k):
        return {"source": "dukascopy"}

    def fake_load(*_a, **_k):
        return pd.DataFrame({"Open": [1.0]})

    def fake_run(_df, _cfg, pair, **_kwargs):
        started.set()
        assert release.wait(5)
        return {
            "source": "dukascopy",
            "bid_ask": True,
            "rows": 1,
            "promotion_line": "Promotion null: live",
            "end_dhaka": "2015-01-05 10:00 Asia/Dhaka",
            "pair": pair,
            "promotion": {"verdict": "null", "promote": False, "reasons": ["keep champion"], "mode": "improve"},
            "scoreboard": [
                {
                    "book": "champion",
                    "n_trades": 1,
                    "win_rate": 1.0,
                    "expectancy": 0.01,
                    "net_pnl": 0.01,
                    "max_drawdown": 0.0,
                    "profit_factor": float("-inf"),
                    "total_return": 0.01,
                    "promotion": "incumbent",
                }
            ],
        }

    monkeypatch.setattr("api.replayjob.history_status", fake_status)
    monkeypatch.setattr("api.replayjob.load_meta", fake_meta)
    monkeypatch.setattr("api.replayjob.load_history", fake_load)
    monkeypatch.setattr("api.replayjob.run_replay", fake_run)

    started_resp = client.post("/replay/train", json={"pair": "USDJPY", "interval": "1h", "pull": False})
    assert started_resp.status_code == 200
    assert started.wait(5)
    live = client.get("/replay/latest", params={"pair": "USDJPY", "interval": "1h"})
    assert live.status_code == 200
    assert "Infinity" not in live.text
    assert live.json()["running"]["job_id"] == started_resp.json()["job_id"]
    assert live.json()["running"]["status"] == "running"
    assert live.json()["job"] is None
    release.set()
    from api.replayjob import wait_job

    done = wait_job(started_resp.json()["job_id"], timeout=10)
    assert done["status"] == "done"
    assert done["scoreboard"][0]["profit_factor"] == "-inf"
    assert done["promotion"]["verdict"] == "null"
    again = client.get("/replay/latest", params={"pair": "USDJPY"}).json()
    assert again["running"] is None
    assert again["job"]["scoreboard"][0]["book"] == "champion"
    assert again["job"]["promotion"]["promote"] is False
