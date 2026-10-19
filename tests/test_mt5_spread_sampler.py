import importlib.util
import pathlib
from datetime import datetime, timezone

import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "mt5_spread_sampler", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "mt5_spread_sampler.py")
S = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(S)


def test_fx_open_weekend_utc():
    assert S.fx_open(datetime(2026, 10, 19, 8, tzinfo=timezone.utc))          # Mon
    assert S.fx_open(datetime(2026, 10, 16, 20, 59, tzinfo=timezone.utc))     # Fri before close
    assert not S.fx_open(datetime(2026, 10, 16, 21, tzinfo=timezone.utc))     # Fri close
    assert not S.fx_open(datetime(2026, 10, 17, 12, tzinfo=timezone.utc))     # Sat
    assert not S.fx_open(datetime(2026, 10, 18, 20, tzinfo=timezone.utc))     # Sun before open
    assert S.fx_open(datetime(2026, 10, 18, 21, tzinfo=timezone.utc))         # Sun open


def test_zero_valid_quotes_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUT", tmp_path / "s.csv")
    q = pd.DataFrame([{"pair": "EURUSD", "symbol": "EURUSD.sml", "ok": False},
                      {"pair": "USDJPY", "symbol": "USDJPY.sml", "ok": True, "bid": 0.0, "ask": 0.0, "spread_pips": 0.0}])
    monkeypatch.setattr(S.F, "quotes", lambda syms: q)
    assert S.sample({"EURUSD": "EURUSD.sml"}) == 0 and not (tmp_path / "s.csv").exists()


def test_main_exits_after_repeated_empty_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "LOG", tmp_path / "l.log"); monkeypatch.setattr(S, "HEART", tmp_path / "h")
    monkeypatch.setattr(S, "fx_open", lambda now: True)
    monkeypatch.setattr(S.F, "connect", lambda require_demo=True: {"is_demo": True})
    monkeypatch.setattr(S.F, "resolve_symbols", lambda: {"EURUSD": "EURUSD.sml"})
    monkeypatch.setattr(S.F, "shutdown", lambda: None)
    monkeypatch.setattr(S, "sample", lambda syms: 0)
    monkeypatch.setattr(S, "_diag", lambda: "diag")
    monkeypatch.setattr(S.time, "sleep", lambda s: None)
    try:
        S.main()
    except SystemExit as e:
        assert e.code == 3
    else:
        raise AssertionError("sampler should exit for a clean restart")
    text = (tmp_path / "l.log").read_text(encoding="utf-8")
    assert text.count("error[") == S.MAX_BAD and "stale MT5 session" in text
