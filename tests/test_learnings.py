"""GET /learnings reads stored artifacts and does not invent rows."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.learnings import collect_learnings
from api.limiter import reset as reset_limiter
from forex_lab.paths import project_root


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


def _cfg(tmp: Path) -> dict:
    return {
        "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
        "broker": {"backend": "paper", "store": str(tmp / "paper.json")},
        "digest": {"persist_file": str(tmp / "digest.json"), "when": "both"},
        "retrain": {"store": str(tmp / "champion")},
        "board": {"alerts": {"persist_file": str(tmp / "alerts.json")}},
    }


def test_empty_when_nothing_is_stored(tmp_path: Path):
    payload = collect_learnings(_cfg(tmp_path), reports=())
    assert payload["items"] == []
    assert payload["count"] == 0
    assert payload["total"] == 0
    assert payload["latest_at"] is None
    assert payload["timezone"] == "Asia/Dhaka"
    assert "Asia/Dhaka" in payload["generated_at_dhaka"]
    present = {row["id"]: row["present"] for row in payload["feeds"]}
    assert present["paper"] is False
    assert present["digest"] is False
    assert present["retrain"] is False
    assert present["awareness"] is False
    assert present["gate_screen"] is False


def test_paper_outcome_and_scoring_note_are_real(tmp_path: Path):
    journal = {
        "positions": [],
        "closed": [
            {
                "id": "pos_old",
                "pair": "EURUSD",
                "side": "BUY",
                "outcome": "WRONG",
                "exit_reason": "sl",
                "exit_time": "2026-09-21 08:00:00 UTC",
                "session": "london",
                "validity_at_entry": "STALE",
                "conf_bucket": "<0.40",
                "realized": -0.01,
            },
            {
                "id": "pos_new",
                "pair": "GBPUSD",
                "side": "SELL",
                "outcome": "RIGHT",
                "exit_reason": "tp",
                "exit_time": "2026-09-22 12:30:00 UTC",
                "session": "ny",
                "validity_at_entry": "OK",
                "conf_bucket": ">=0.60",
                "realized": 0.02,
            },
            {
                "id": "pos_stale2",
                "pair": "EURUSD",
                "side": "SELL",
                "outcome": "WRONG",
                "exit_reason": "sl",
                "exit_time": "2026-09-21 09:00:00 UTC",
                "session": "london",
                "validity_at_entry": "STALE",
                "conf_bucket": "<0.40",
                "realized": -0.004,
            },
        ],
        "fills": [],
    }
    (tmp_path / "paper.json").write_text(json.dumps(journal), encoding="utf-8")
    payload = collect_learnings(_cfg(tmp_path), reports=())
    paper = [row for row in payload["items"] if row["source"] == "paper" and row["title"] != "Scoring note"]
    assert [row["title"] for row in paper] == [
        "GBPUSD SELL paper RIGHT",
        "EURUSD SELL paper WRONG",
        "EURUSD BUY paper WRONG",
    ]
    top = paper[0]
    assert top["at"].startswith("2026-09-22T")
    assert "Asia/Dhaka" in top["at_dhaka"]
    assert "Closed on tp" in top["detail"]
    assert "session ny" in top["detail"]
    notes = [row for row in payload["items"] if row["title"] == "Scoring note"]
    assert notes
    blob = " ".join(row["detail"] for row in notes)
    assert "STALE" in blob
    assert not any(row["detail"].startswith("Paper lookback only") for row in notes)
    assert not any(row["detail"].startswith("News remains context only") for row in notes)
    assert payload["latest_at"] == top["at"]
    assert all(row["source"] in {"paper", "digest", "model", "awareness"} for row in payload["items"])


def test_open_pending_is_not_a_learning(tmp_path: Path):
    journal = {
        "positions": [
            {
                "id": "pos_open",
                "pair": "EURUSD",
                "side": "BUY",
                "outcome": "PENDING",
                "entry_time": "2026-09-23 01:00:00 UTC",
                "status": "open",
            }
        ],
        "closed": [],
        "fills": [],
    }
    (tmp_path / "paper.json").write_text(json.dumps(journal), encoding="utf-8")
    payload = collect_learnings(_cfg(tmp_path), reports=())
    assert payload["items"] == []


def test_digest_summary_and_flip_skip_when_awareness_has_it(tmp_path: Path):
    digest = {
        "generated_at": "2026-09-22 18:00:00 Asia/Dhaka",
        "paper": {"right": 1, "wrong": 2, "n_scored": 3, "hit_rate": 1 / 3},
        "flips": [
            {
                "pair": "EURUSD",
                "from": "HOLD",
                "to": "BUY",
                "message": "EURUSD HOLD → BUY",
                "when": "2026-09-22 16:00:00 Asia/Dhaka",
            }
        ],
        "awareness": {"n_unhealthy": 1, "issues": [{"source": "news"}]},
        "freshness": [],
    }
    alerts = {
        "alerts": [
            {
                "id": "a1",
                "kind": "flip",
                "pair": "EURUSD",
                "timeframe": "1h",
                "message": "EURUSD HOLD → BUY",
                "created_at": "2026-09-22T10:00:00Z",
                "from_value": "HOLD",
                "to_value": "BUY",
            }
        ]
    }
    (tmp_path / "digest.json").write_text(json.dumps(digest), encoding="utf-8")
    (tmp_path / "alerts.json").write_text(json.dumps(alerts), encoding="utf-8")
    payload = collect_learnings(_cfg(tmp_path), reports=())
    titles = [row["title"] for row in payload["items"]]
    assert titles.count("EURUSD flipped HOLD → BUY") == 1
    flip = next(row for row in payload["items"] if row["title"].startswith("EURUSD flipped"))
    assert flip["source"] == "awareness"
    summary = next(row for row in payload["items"] if row["title"] == "Daily digest")
    assert summary["source"] == "digest"
    assert "1 RIGHT / 2 WRONG" in summary["detail"]
    assert "1 signal flip" in summary["detail"]
    assert "1 awareness issue" in summary["detail"]
    assert "not a live edge" in summary["detail"]


def test_retrain_history_is_chronological(tmp_path: Path):
    store = tmp_path / "champion"
    store.mkdir()
    history = store / "EURUSD_history.jsonl"
    history.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "verdict": "seed",
                        "promote": False,
                        "promoted_at": "2026-09-01T00:00:00Z",
                    }
                ),
                json.dumps(
                    {
                        "verdict": "null",
                        "promote": False,
                        "deltas": {"profit_factor": -0.02, "total_return": -0.01, "max_drawdown": -0.004},
                        "promoted_at": "2026-09-20T04:00:00Z",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (store / "EURUSD.json").write_text(
        json.dumps({"pair": "EURUSD", "verdict": "seed", "promoted_at": "2026-09-01T00:00:00Z"}),
        encoding="utf-8",
    )
    payload = collect_learnings(_cfg(tmp_path), reports=())
    model = [row for row in payload["items"] if row["source"] == "model"]
    assert [row["title"] for row in model] == ["EURUSD retrain null", "EURUSD retrain seed"]
    assert "not promoted" in model[0]["detail"]
    assert "PF -0.0200" in model[0]["detail"]
    assert "Asia/Dhaka" in model[0]["at_dhaka"]


def test_gate_screen_quotes_takeaway(tmp_path: Path):
    screen = tmp_path / "gate_screen.md"
    screen.write_text(
        "# screen\n\n**Takeaway (not a live edge):**\n"
        "- **Gates** cut trades. Profit factor fell.\n"
        "\nDefault: `gates.enabled: false`.\n",
        encoding="utf-8",
    )
    payload = collect_learnings(_cfg(tmp_path), reports=(screen,))
    titles = [row["title"] for row in payload["items"]]
    assert "Edge gate · Gates" in titles
    assert "Edge gate · default" in titles
    gates = next(row for row in payload["items"] if row["title"] == "Edge gate · Gates")
    assert gates["source"] == "model"
    assert "Profit factor fell" in gates["detail"]
    assert "**" not in gates["detail"]
    assert "*" not in gates["detail"]
    default = next(row for row in payload["items"] if row["title"] == "Edge gate · default")
    assert "gates.enabled: false" in default["detail"]
    assert "`" not in default["detail"]


def test_repo_gate_screen_feeds_the_tab():
    path = project_root() / "reports" / "gate_screen.md"
    payload = collect_learnings(
        {"ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"}},
        reports=(path,),
    )
    titles = [row["title"] for row in payload["items"]]
    assert "Edge gate · Gates" in titles
    assert "Edge gate · Cost-aware" in titles
    assert "Edge gate · default" in titles
    assert all(row["source"] == "model" for row in payload["items"])
    assert all("Asia/Dhaka" in row["at_dhaka"] for row in payload["items"])
    assert all("*" not in row["detail"] and "`" not in row["detail"] for row in payload["items"])


def test_limit_keeps_newest(tmp_path: Path):
    store = tmp_path / "champion"
    store.mkdir()
    lines = [
        json.dumps({"verdict": f"v{i}", "promote": False, "promoted_at": f"2026-09-{i+1:02d}T00:00:00Z"})
        for i in range(3)
    ]
    (store / "EURUSD_history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = collect_learnings(_cfg(tmp_path), limit=2, reports=())
    assert payload["count"] == 2
    assert payload["total"] == 3
    assert payload["limit"] == 2
    assert [row["title"] for row in payload["items"]] == ["EURUSD retrain v2", "EURUSD retrain v1"]


def test_http_learnings_paper_and_repo_gate(client: TestClient, monkeypatch, tmp_path: Path):
    paper = Path(os.environ["FORX_PAPER_STORE"])
    paper.write_text(
        json.dumps(
            {
                "positions": [],
                "fills": [],
                "closed": [
                    {
                        "id": "pos_http",
                        "pair": "USDJPY",
                        "side": "BUY",
                        "outcome": "FLAT",
                        "exit_reason": "manual",
                        "exit_time": "2026-09-23 02:15:00 UTC",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    res = client.get("/learnings", params={"limit": 50})
    assert res.status_code == 200
    body = res.json()
    assert body["timezone"] == "Asia/Dhaka"
    titles = [row["title"] for row in body["items"]]
    assert "USDJPY BUY paper FLAT" in titles
    assert "Edge gate · Gates" in titles
    flat = next(row for row in body["items"] if row["title"] == "USDJPY BUY paper FLAT")
    assert flat["source"] == "paper"
    assert "Asia/Dhaka" in flat["at_dhaka"]
    feeds = {row["id"]: row for row in body["feeds"]}
    assert feeds["paper"]["count"] >= 1
    assert feeds["gate_screen"]["present"] is True

    monkeypatch.setattr("api.learnings.DEFAULT_REPORTS", (tmp_path / "missing.md",))
    paper.write_text(json.dumps({"positions": [], "closed": [], "fills": []}), encoding="utf-8")
    empty = client.get("/learnings")
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    assert empty.json()["latest_at"] is None


def test_http_limit_validation(client: TestClient):
    bad = client.get("/learnings", params={"limit": 0})
    assert bad.status_code == 422
    huge = client.get("/learnings", params={"limit": 500})
    assert huge.status_code == 422
