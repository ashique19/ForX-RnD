"""No desk path may train/overwrite production models implicitly."""
from __future__ import annotations

from argparse import Namespace

import pandas as pd
import pytest

from forex_lab import cli
from forex_lab.train_lock import NEED_TRAIN_RC, TRAIN_LOCKED_MSG


def _boom(*_a, **_k):
    raise AssertionError("train_models must not be called from a desk path")


def test_cmd_train_is_locked(monkeypatch):
    monkeypatch.setattr(cli, "train_models", _boom)
    monkeypatch.setattr("forex_lab.model.train_models", _boom)
    monkeypatch.setattr(cli, "load_ohlcv", _boom)
    assert cli.cmd_train(Namespace(pair="EURGBP", config=None), {}) == NEED_TRAIN_RC


def test_signals_missing_model_reports_need_train(monkeypatch, capsys):
    monkeypatch.setattr(cli, "train_models", _boom)
    monkeypatch.setattr("forex_lab.model.train_models", _boom)
    monkeypatch.setattr(cli, "load_ohlcv", lambda *_a, **_k: pd.DataFrame({"Close": [1.0]}))

    def _missing(*_a, **_k):
        raise FileNotFoundError("no model")

    monkeypatch.setattr(cli, "generate_signals", _missing)
    monkeypatch.setattr(cli, "write_signals", _boom)
    rc = cli.cmd_signals(Namespace(pair="gbpusd", interval="1h", config=None), {})
    assert rc == NEED_TRAIN_RC
    assert "need_train" in capsys.readouterr().out


def test_pipeline_need_train_when_model_missing(monkeypatch):
    from api import deskdata

    monkeypatch.setattr("forex_lab.ui.pipeline.run_train", _boom)
    monkeypatch.setattr("forex_lab.ui.pipeline.run_backtest", _boom)
    monkeypatch.setattr("forex_lab.ui.pipeline.run_signals", lambda pair, cfg=None: (NEED_TRAIN_RC, "need_train"))
    monkeypatch.setattr(deskdata, "normalize_pair", lambda p: str(p).upper())
    out = deskdata.run_pipeline_pair("usdchf", cfg={})
    assert out["ok"] is False
    assert out["status"] == "need_train"
    assert out["trained"] is False


def test_ui_run_train_wrapper_is_locked(monkeypatch):
    from forex_lab.ui import pipeline as pl

    monkeypatch.setattr(cli, "train_models", _boom)
    monkeypatch.setattr("forex_lab.model.train_models", _boom)
    rc, log = pl.run_train("EURGBP", cfg={})
    assert rc == NEED_TRAIN_RC
    assert TRAIN_LOCKED_MSG.split(":")[0] in log
