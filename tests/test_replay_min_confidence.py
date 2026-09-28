"""Per-job min_confidence reaches run_replay without rewriting default.yaml."""
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


def test_replay_train_min_confidence_override(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}

    def fake_status(*_a, **_k):
        return {"stale": False, "source": "dukascopy", "rows": 4, "bid_ask": True}

    def fake_meta(*_a, **_k):
        return {"source": "dukascopy"}

    def fake_load(*_a, **_k):
        idx = pd.date_range("2024-01-01", periods=4, freq="h")
        return pd.DataFrame(
            {"Open": [1.1, 1.2, 1.3, 1.4], "High": 1.2, "Low": 1.0, "Close": [1.1, 1.2, 1.3, 1.4], "Volume": 1},
            index=idx,
        )

    def fake_run(_df, cfg, pair, **kwargs):
        seen["min_confidence"] = (cfg.get("signals") or {}).get("min_confidence")
        folder = Path(kwargs["job_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scoreboard.csv").write_text("book,net_pnl\nchampion,0.0\n", encoding="utf-8")
        (folder / "scoreboard.xlsx").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        (folder / "equity.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (folder / "report.md").write_text("# scoreboard\n", encoding="utf-8")
        return {
            "source": "dukascopy",
            "bid_ask": True,
            "rows": 4,
            "promotion_line": "Promotion null: stub",
            "end_dhaka": "2024-01-01 10:00 Asia/Dhaka",
            "pair": pair,
            "scoreboard": [{"book": "champion", "n_trades": 0, "net_pnl": 0.0, "profit_factor": None}],
        }

    monkeypatch.setattr("api.replayjob.history_status", fake_status)
    monkeypatch.setattr("api.replayjob.load_meta", fake_meta)
    monkeypatch.setattr("api.replayjob.load_history", fake_load)
    monkeypatch.setattr("api.replayjob.run_replay", fake_run)

    started = client.post(
        "/replay/train",
        json={
            "pair": "EURUSD",
            "interval": "1h",
            "start": "2024-09-24",
            "pull": False,
            "min_confidence": 0.55,
        },
    )
    assert started.status_code == 200
    body = started.json()
    assert body["min_confidence"] == 0.55

    from api.replayjob import wait_job

    done = wait_job(body["job_id"], timeout=10)
    assert done["status"] == "done"
    assert seen["min_confidence"] == pytest.approx(0.55)

    # Live yaml on disk is not rewritten by the override path.
    from pathlib import Path as P
    yaml_text = P("config/default.yaml").read_text(encoding="utf-8")
    assert "min_confidence: 0.40" in yaml_text or "min_confidence: 0.4" in yaml_text
