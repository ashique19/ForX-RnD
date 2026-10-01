"""Cheap idle-pair Train does not change Active and skips retrain gate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from api.deskdata import run_train_idle_pair


def test_run_train_idle_pair_train_and_signals(tmp_path: Path):
    calls: list[str] = []

    def fake_train(pair, *, cfg=None):
        calls.append(f"train:{pair}")
        return 0, "[train] ok\n"

    def fake_signals(pair, *, cfg=None):
        calls.append(f"signals:{pair}")
        return 0, "[signals] ok\n"

    with (
        patch("forex_lab.ui.pipeline.run_train", side_effect=fake_train),
        patch("forex_lab.ui.pipeline.run_signals", side_effect=fake_signals),
        patch("forex_lab.ui.model_build.model_build_status", return_value={"status": "OK", "pair": "USDJPY"}),
        patch("api.deskdata.normalize_pair", side_effect=lambda p: str(p).upper()),
        patch("api.deskdata.app_config", return_value={}),
    ):
        out = run_train_idle_pair("usdjpy", with_signals=True, cfg={})

    assert out["ok"] is True
    assert out["pair"] == "USDJPY"
    assert out["failed"] is None
    assert out["active_unchanged"] is True
    assert "promote" not in str(out).lower() or "no promote" in out.get("note", "").lower()
    assert calls == ["train:USDJPY", "signals:USDJPY"]
    assert [s["step"] for s in out["steps"]] == ["train", "signals"]


def test_run_train_idle_pair_stops_on_train_fail():
    with (
        patch("forex_lab.ui.pipeline.run_train", return_value=(1, "boom")),
        patch("forex_lab.ui.pipeline.run_signals") as sig,
        patch("forex_lab.ui.model_build.model_build_status", return_value={"status": "need Train"}),
        patch("api.deskdata.normalize_pair", side_effect=lambda p: str(p).upper()),
        patch("api.deskdata.app_config", return_value={}),
    ):
        out = run_train_idle_pair("AUDUSD", with_signals=True, cfg={})
    assert out["ok"] is False
    assert out["failed"] == "train"
    assert out["active_unchanged"] is True
    sig.assert_not_called()
