"""Pure helpers of the read-only partial/forming-bar entry audit (scripts/audit_partial_bar_entries.py)."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_pb", ROOT / "scripts" / "audit_partial_bar_entries.py")
A = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(A)
U = timezone.utc


def test_parse_dhaka_and_bad_values():
    assert A.parse_dhaka("2026-10-19 18:00:00 Asia/Dhaka") == datetime(2026, 10, 19, 12, 0, tzinfo=U)
    assert A.parse_dhaka("n/a") is None and A.parse_dhaka(None) is None
    assert A.parse_utc("2026-10-19T13:02:43Z") == datetime(2026, 10, 19, 13, 2, 43, tzinfo=U)


def test_bar_status_forming_stale_closed():
    bar = datetime(2026, 10, 19, 12, 0, tzinfo=U)
    assert A.bar_status(datetime(2026, 10, 19, 12, 35, tzinfo=U), bar) == "forming"
    assert A.bar_status(datetime(2026, 10, 19, 13, 2, tzinfo=U), bar, last_bar_utc=bar) == "stale_tip"
    assert A.bar_status(datetime(2026, 10, 19, 13, 2, tzinfo=U), bar, last_bar_utc=None) == "closed"
    assert A.bar_status(datetime(2026, 10, 19, 16, 0, tzinfo=U), bar, last_bar_utc=bar) == "closed"


def test_still_fires_needs_direction_and_floor():
    assert A.still_fires("BUY", "BUY", 0.46, 0.45)
    assert not A.still_fires("BUY", "BUY", 0.44, 0.45)
    assert not A.still_fires("BUY", "SELL", 0.9, 0.45)
    assert not A.still_fires("BUY", "HOLD", 0.9, 0.45)
    assert not A.still_fires("BUY", None, None, 0.45)


def test_faithful_tolerance():
    assert A.faithful("SELL", 0.5128, "SELL", 0.5255)
    assert not A.faithful("SELL", 0.5128, "SELL", 0.5685)
    assert not A.faithful("SELL", 0.5128, "BUY", 0.5128)


def test_nearest_row_window():
    t = datetime(2026, 10, 15, 2, 21, 6, tzinfo=U)
    rows = [{"pair": "AUDUSD", "ts_utc": "2026-10-15T02:21:04Z", "x": 1},
            {"pair": "AUDUSD", "ts_utc": "2026-10-15T02:40:00Z", "x": 2},
            {"pair": "NZDUSD", "ts_utc": "2026-10-15T02:21:06Z", "x": 3}]
    assert A.nearest_row(rows, "AUDUSD", t)["x"] == 1
    assert A.nearest_row(rows, "EURUSD", t) is None


def test_partial_from_m1_uses_only_closed_minutes():
    idx = pd.date_range("2026-10-19 12:00", periods=60, freq="1min")
    px = 1.10 + np.arange(60) * 1e-5
    m1 = pd.DataFrame({"Open": px, "High": px + 2e-5, "Low": px - 2e-5, "Close": px + 1e-5, "Volume": 10.0}, index=idx)
    bar = datetime(2026, 10, 19, 12, 0, tzinfo=U)
    p = A.partial_from_m1(m1, bar, datetime(2026, 10, 19, 12, 3, 30, tzinfo=U))  # minutes 12:00, 12:01, 12:02 closed
    assert p["minutes"] == 3 and p["Volume"] == 30.0 and p["Open"] == px[0] and p["Close"] == px[2] + 1e-5
    assert A.partial_from_m1(m1, bar, datetime(2026, 10, 19, 12, 0, 30, tzinfo=U)) is None


def test_audit_script_has_no_write_calls():
    src = (ROOT / "scripts" / "audit_partial_bar_entries.py").read_text(encoding="utf-8")
    for bad in ("to_csv(", "write_text(", "write_bytes(", ".append(close", "record_annotation", "append_event", "order_send"):
        assert bad not in src