"""Main book: auto close uses the first bar that touches SL/TP, not the pass-time price."""
import pandas as pd
import pytest

from api import paperdesk as pd_desk


def _frame():
    idx = pd.date_range("2026-10-14 05:00", periods=8, freq="h")
    low = [1.1205, 1.1203, 1.1199, 1.1194, 1.1190, 1.1130, 1.1120, 1.1110]
    return pd.DataFrame({"Open": 1.121, "High": 1.1215, "Low": low, "Close": [x + 0.0003 for x in low]}, index=idx)


def test_bar_path_exit_closes_at_sl_on_touch_bar():
    pos = {"side": "BUY", "entry_price": 1.12108, "sl": 1.11966, "tp": 1.12250, "horizon": 8,
           "entry_bar_time": "2026-10-14 05:00:00"}
    reason, px, stamp = pd_desk._bar_path_exit(pos, _frame(), {})
    assert reason == "sl" and px == pytest.approx(1.11966)
    assert stamp == "2026-10-14 08:00:00 UTC"  # not the late last bar


def test_bar_path_exit_pending_returns_none():
    pos = {"side": "BUY", "entry_price": 1.12108, "sl": 1.10, "tp": 1.13, "horizon": 50,
           "entry_bar_time": "2026-10-14 05:00:00"}
    assert pd_desk._bar_path_exit(pos, _frame(), {}) is None


def test_bar_path_horizon_maps_to_duration():
    pos = {"side": "BUY", "entry_price": 1.12108, "sl": 1.10, "tp": 1.13, "horizon": 3,
           "entry_bar_time": "2026-10-14 05:00:00"}
    reason, px, _ = pd_desk._bar_path_exit(pos, _frame(), {})
    assert reason == "duration" and px == pytest.approx(1.1197)
