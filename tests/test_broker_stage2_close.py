"""Streamlit refresh_from_ohlcv path journals a Stage-2 close once."""
import pandas as pd

from forex_lab import paper_shadow as ps
from forex_lab.broker import PaperBroker


def _df():
    idx = pd.date_range("2026-10-14 05:00", periods=4, freq="h")
    return pd.DataFrame({"Open": [1.1, 1.1, 1.1, 1.1], "High": [1.101, 1.101, 1.121, 1.101],
                         "Low": [1.099, 1.099, 1.0995, 1.099], "Close": [1.1, 1.1, 1.12, 1.1]}, index=idx)


def _open(tmp_path, journal=True):
    b = PaperBroker(tmp_path / "b.json", cfg={"spread_pips": 0.0, "horizon": 8})
    pos = b.submit("BUY", "EURUSD", sl=1.09, tp=1.12, price=1.1, entry_bar_time="2026-10-14 05:00:00",
                   validity="OK", confidence=0.7, horizon=8)
    pid = b.list_positions()[0]["id"]
    if journal:
        ps.record_open(pair="EURUSD", side="BUY", position_id=pid, fill_id="f")
    return b, pid


def test_refresh_close_appends_stage2_close_at_touch_bar(tmp_path):
    b, pid = _open(tmp_path)
    b.refresh_from_ohlcv("EURUSD", _df())
    closes = [e for e in ps.load_events() if e.get("event") == "close"]
    assert len(closes) == 1 and closes[0]["position_id"] == pid
    assert closes[0]["exit_reason"] == "tp" and closes[0]["exit_mid"] == 1.12
    assert b.list_closed()[0]["exit_time"] == "2026-10-14 07:00:00 UTC"


def test_no_double_close(tmp_path):
    b, pid = _open(tmp_path)
    ps.record_close(pair="EURUSD", side="BUY", position_id=pid, exit_mid=1.12, exit_reason="tp")
    b.refresh_from_ohlcv("EURUSD", _df())
    assert len([e for e in ps.load_events() if e.get("event") == "close"]) == 1


def test_untracked_position_not_journaled(tmp_path):
    b, _ = _open(tmp_path, journal=False)
    b.refresh_from_ohlcv("EURUSD", _df())
    assert not [e for e in ps.load_events() if e.get("event") == "close"]
