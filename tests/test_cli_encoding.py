"""Windows console encoding and fetch exit-code tests."""
from __future__ import annotations

import io
from argparse import Namespace
from pathlib import Path

import pandas as pd
import pytest

from forex_lab.cli import cmd_fetch, main
from forex_lab.console import ascii_text, safe_print
from forex_lab.data import generate_synthetic_ohlcv


class _Cp1252Stream(io.TextIOBase):
    """Simulate a Windows cp1252 console that cannot encode arrows."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self._buf = io.StringIO()
        self.writes = 0

    def write(self, s: str) -> int:  # type: ignore[override]
        self.writes += 1
        s.encode("cp1252")  # raises UnicodeEncodeError on →
        return self._buf.write(s)

    def flush(self) -> None:
        self._buf.flush()

    def reconfigure(self, **kwargs):  # noqa: ANN003
        raise OSError("reconfigure refused")

    def getvalue(self) -> str:
        return self._buf.getvalue()


def test_ascii_text_strips_arrow():
    assert "\u2192" not in ascii_text("range 1 \u2192 2")
    assert "->" in ascii_text("range 1 \u2192 2")


def test_safe_print_does_not_raise_on_cp1252(monkeypatch):
    stream = _Cp1252Stream()
    monkeypatch.setattr("sys.stdout", stream)
    safe_print("range 2024-01-01 \u2192 2024-02-01")
    assert "->" in stream.getvalue()
    assert "\u2192" not in stream.getvalue()


def test_cmd_fetch_exits_zero_if_csv_saved_even_when_print_would_fail(tmp_path, monkeypatch):
    pair = "EURUSD"
    csv_path = tmp_path / f"{pair}_1h.csv"
    df = generate_synthetic_ohlcv(pair=pair, bars=150, seed=1)

    def _fake_fetch(*_a, **_k):
        df.to_csv(csv_path)
        return df, "yfinance"

    monkeypatch.setattr("forex_lab.cli.fetch_ohlcv", _fake_fetch)
    monkeypatch.setattr("forex_lab.cli.data_path", lambda *_a, **_k: csv_path)

    boom_count = {"n": 0}

    def _boom(*_a, **_k):
        boom_count["n"] += 1
        raise UnicodeEncodeError("cp1252", "\u2192", 0, 1, "arrow")

    monkeypatch.setattr("forex_lab.cli.safe_print", _boom)
    args = Namespace(pair=pair, period="2y", interval="1h", synthetic=False)
    rc = cmd_fetch(args, {"interval": "1h", "paths": {"data_dir": str(tmp_path)}})
    assert rc == 0
    assert csv_path.exists()
    assert boom_count["n"] >= 1
    reloaded = pd.read_csv(csv_path, index_col=0)
    assert len(reloaded) == len(df)


def test_cmd_fetch_does_not_synthetic_fallback_after_yfinance_save(tmp_path, monkeypatch):
    """A post-save print failure must not rewrite the CSV as synthetic."""
    pair = "EURUSD"
    csv_path = tmp_path / f"{pair}_1h.csv"
    real = generate_synthetic_ohlcv(pair=pair, bars=200, seed=11)
    real["Close"] = 1.2345  # marker

    def _fake_fetch(*_a, **_k):
        real.to_csv(csv_path)
        return real, "yfinance"

    monkeypatch.setattr("forex_lab.cli.fetch_ohlcv", _fake_fetch)
    monkeypatch.setattr("forex_lab.cli.data_path", lambda *_a, **_k: csv_path)
    monkeypatch.setattr(
        "forex_lab.cli.safe_print",
        lambda *_a, **_k: (_ for _ in ()).throw(UnicodeEncodeError("cp1252", "x", 0, 1, "x")),
    )
    args = Namespace(pair=pair, period="2y", interval="1h", synthetic=False)
    assert cmd_fetch(args, {"interval": "1h"}) == 0
    saved = pd.read_csv(csv_path)
    assert (saved["Close"] == 1.2345).all()


def test_main_help_is_ascii(capsys):
    with pytest.raises(SystemExit) as ei:
        main(["-h"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    out.encode("ascii")
    assert "\u2192" not in out
    assert "\u2014" not in out
