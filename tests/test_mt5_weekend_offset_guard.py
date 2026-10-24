"""fetch_mt5_1h_utc on the FX weekend: the stale last tick must not be read as a clock offset."""
import pandas as pd
import pytest

from forex_lab import data as D
from forex_lab import freshness
from forex_lab import mt5_feed as F


def _rig(monkeypatch, *, session_open: bool, tick_off: int):
    idx = pd.date_range("2026-10-23 21:00", periods=3, freq="h")  # server time (UTC+3): 18:00..20:00 UTC
    bars = pd.DataFrame({"Open": 1.0, "High": 1.1, "Low": 0.9, "Close": 1.05, "Volume": 10.0}, index=idx)
    monkeypatch.setattr(F, "connect", lambda require_demo=True: {"is_demo": True})
    monkeypatch.setattr(F, "shutdown", lambda: None)
    monkeypatch.setattr(F, "resolve_symbols", lambda pairs: {p.upper(): p.upper() + ".sml" for p in pairs})
    monkeypatch.setattr(F, "bars_1h_synced", lambda sym, count: (bars, 0, False))
    monkeypatch.setattr(F, "server_offset_hours", lambda sym: tick_off)
    monkeypatch.setattr(freshness, "fx_session_open", lambda now=None: session_open)
    monkeypatch.setattr(D, "expected_mt5_server_offset_hours", lambda now_utc=None: 3)


def test_weekend_stale_tick_offset_ignored(monkeypatch):
    _rig(monkeypatch, session_open=False, tick_off=-5)  # Sat ~05Z: tick 23:59 server vs now
    out = D.fetch_mt5_1h_utc("EURUSD", 3)
    assert str(out.index.max()) == "2026-10-23 20:00:00"  # converted with the expected NY+7 offset


def test_in_session_offset_mismatch_still_refused(monkeypatch):
    _rig(monkeypatch, session_open=True, tick_off=-5)
    with pytest.raises(D.MT5GuardError):
        D.fetch_mt5_1h_utc("EURUSD", 3)


def test_in_session_matching_offset_ok(monkeypatch):
    _rig(monkeypatch, session_open=True, tick_off=3)
    assert str(D.fetch_mt5_1h_utc("EURUSD", 3).index.min()) == "2026-10-23 18:00:00"
