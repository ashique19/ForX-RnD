"""Idle-pair Train is locked: the desk never fits production models."""
from __future__ import annotations

from unittest.mock import patch

from api.deskdata import run_train_idle_pair


def test_run_train_idle_pair_is_locked_and_never_trains():
    with (
        patch("forex_lab.ui.pipeline.run_train") as train,
        patch("forex_lab.ui.pipeline.run_signals") as sig,
        patch("forex_lab.ui.model_build.model_build_status", return_value={"status": "need Train"}),
        patch("api.deskdata.normalize_pair", side_effect=lambda p: str(p).upper()),
        patch("api.deskdata.app_config", return_value={}),
    ):
        out = run_train_idle_pair("usdjpy", with_signals=True, cfg={})
    train.assert_not_called()
    sig.assert_not_called()
    assert out["ok"] is False
    assert out["pair"] == "USDJPY"
    assert out["failed"] == "train_locked"
    assert out["locked"] is True
    assert out["trained"] is False
    assert out["status"] == "need_train"
    assert out["active_unchanged"] is True
    assert "retrain" in out["note"]
