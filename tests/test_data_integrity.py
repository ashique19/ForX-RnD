"""Data-integrity gate: Replay and backtest refuse corrupted Jetta bars."""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import pytest

from forex_lab.backtest import walk_forward_backtest
from forex_lab.cli import cmd_backtest, cmd_replay
from forex_lab.data_integrity import (
    DEFAULT_MAX_MEDIAN_OPEN_GAP_PIPS,
    DEFAULT_MAX_WICKLESS_SHARE,
    DataIntegrityError,
    assert_bar_integrity,
    bar_integrity_stats,
)
from forex_lab.replay import run_replay


def _frame(n: int, *, wickless_at: tuple[int, ...] = (), gap_pips: float = 0.05, pip: float = 0.0001) -> pd.DataFrame:
    idx = pd.date_range("2020-01-06", periods=n, freq="h")
    close = np.full(n, 1.10)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1] + gap_pips * pip
    high = np.maximum(open_, close) + pip
    low = np.minimum(open_, close) - pip
    for i in wickless_at:
        high[i] = max(open_[i], close[i])
        low[i] = min(open_[i], close[i])
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 1.0},
        index=idx,
    )


def test_clean_bars_pass_and_report_the_gap():
    df = _frame(20, gap_pips=0.05)
    stats = assert_bar_integrity(df, "EURUSD", context="replay")
    assert stats["wickless_share"] == 0.0
    assert stats["median_open_gap_pips"] == pytest.approx(0.05)


def test_defaults_are_ten_percent_and_one_pip():
    assert DEFAULT_MAX_WICKLESS_SHARE == 0.10
    assert DEFAULT_MAX_MEDIAN_OPEN_GAP_PIPS == 1.0
    # Equal to the limit is allowed.
    at_wick = _frame(10, wickless_at=(0,), gap_pips=0.0)
    assert bar_integrity_stats(at_wick, "EURUSD")["wickless_share"] == pytest.approx(0.10)
    assert_bar_integrity(at_wick, "EURUSD")
    at_gap = _frame(8, gap_pips=1.0)
    assert bar_integrity_stats(at_gap, "EURUSD")["median_open_gap_pips"] == pytest.approx(1.0)
    assert_bar_integrity(at_gap, "EURUSD")


def test_wickless_share_above_ten_percent_refuses():
    df = _frame(10, wickless_at=(0, 1), gap_pips=0.0)
    with pytest.raises(DataIntegrityError, match="Refusing replay") as exc:
        assert_bar_integrity(df, "EURUSD", context="replay")
    assert "wickless" in str(exc.value)
    assert exc.value.reason == "integrity"


def test_median_open_gap_above_one_pip_refuses():
    df = _frame(12, gap_pips=3.5)
    with pytest.raises(DataIntegrityError, match="previous Close") as exc:
        assert_bar_integrity(df, "EURUSD", context="backtest")
    text = str(exc.value)
    assert "Refusing backtest" in text
    assert "3.50 pips" in text
    assert "Re-pull history" in text


def test_jpy_gap_uses_the_jpy_pip():
    # 0.005 price = 0.5 pip on USDJPY; 0.02 price = 2 pips.
    df = _frame(6, gap_pips=0.5, pip=0.01)
    # _frame treats `pip` as the price step for the requested gap_pips.
    assert_bar_integrity(df, "USDJPY")
    wide = _frame(6, gap_pips=2.0, pip=0.01)
    with pytest.raises(DataIntegrityError, match="USDJPY"):
        assert_bar_integrity(wide, "USDJPY", context="replay")


def test_run_replay_refuses_bad_bars(tmp_path):
    df = _frame(30, wickless_at=tuple(range(0, 30, 2)), gap_pips=0.0)
    with pytest.raises(DataIntegrityError, match="Refusing replay on EURUSD") as exc:
        run_replay(df, {"replay": {"store_dir": str(tmp_path)}}, "EURUSD", job_dir=tmp_path / "job")
    assert "wickless" in str(exc.value)
    assert not (tmp_path / "job").exists()


def test_walk_forward_refuses_bad_bars():
    df = _frame(40, gap_pips=4.0)
    with pytest.raises(DataIntegrityError, match="Refusing backtest on GBPUSD"):
        walk_forward_backtest(df, {"horizon": 4}, "GBPUSD", model_type="logistic")


def test_replay_cli_refuses_corrupt_history(monkeypatch, capsys):
    bad = _frame(16, wickless_at=tuple(range(16)), gap_pips=0.0)

    monkeypatch.setattr("forex_lab.history.history_status", lambda *_a, **_k: {"stale": False})
    monkeypatch.setattr("forex_lab.history.load_history", lambda *_a, **_k: bad)
    monkeypatch.setattr("forex_lab.history.load_meta", lambda *_a, **_k: {"source": "jetta"})
    args = argparse.Namespace(
        pair="EURUSD",
        interval="1h",
        start="2020-01-01",
        end=None,
        no_pull=True,
        model=None,
        slippage_pips=None,
        min_confidence=None,
        source=None,
    )
    rc = cmd_replay(args, {"replay": {}, "signals": {}})
    assert rc == 1
    err = capsys.readouterr().out
    assert "[replay] failed:" in err
    assert "Refusing replay" in err
    assert "wickless" in err


def test_backtest_cli_refuses_corrupt_history(monkeypatch, capsys):
    bad = _frame(16, gap_pips=6.0)
    monkeypatch.setattr("forex_lab.cli.load_ohlcv", lambda *_a, **_k: bad)
    rc = cmd_backtest(argparse.Namespace(pair="EURUSD", model="logistic"), {"horizon": 4})
    assert rc == 1
    err = capsys.readouterr().out
    assert "[backtest] refused:" in err
    assert "Refusing backtest" in err
    assert "previous Close" in err
