"""Shared FX session helper: Fri/Sun cut at 17:00 America/New_York (DST-aware), and both
burst scripts gate on it instead of the Asia/Dhaka weekday (bug: Dhaka Saturday starts Fri 18:00 UTC)."""
import importlib.util
import json
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from forex_lab import explore_book as eb
from forex_lab.freshness import fx_session_open

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
DHAKA = ZoneInfo("Asia/Dhaka")
U = timezone.utc

# (utc datetime, expected open)
SUMMER = [  # EDT: cut 21:00 UTC
    (datetime(2026, 10, 23, 20, 30, tzinfo=U), True),   # Fri 20:30Z (Sat 02:30 Dhaka) -> open
    (datetime(2026, 10, 23, 21, 30, tzinfo=U), False),  # Fri 21:30Z -> closed
    (datetime(2026, 10, 24, 12, 0, tzinfo=U), False),   # Sat
    (datetime(2026, 10, 25, 20, 30, tzinfo=U), False),  # Sun 20:30Z -> closed
    (datetime(2026, 10, 25, 21, 30, tzinfo=U), True),   # Sun 21:30Z -> open
]
WINTER = [  # EST (after 2026-11-01): cut 22:00 UTC
    (datetime(2026, 11, 6, 21, 30, tzinfo=U), True),    # Fri 21:30Z still open in winter
    (datetime(2026, 11, 6, 22, 30, tzinfo=U), False),   # Fri 22:30Z closed
    (datetime(2026, 11, 7, 12, 0, tzinfo=U), False),    # Sat
    (datetime(2026, 11, 8, 21, 30, tzinfo=U), False),   # Sun 21:30Z still closed in winter
    (datetime(2026, 11, 8, 22, 30, tzinfo=U), True),    # Sun 22:30Z open
]
CASES = SUMMER + WINTER


@pytest.mark.parametrize("t,expected", CASES)
def test_fx_session_open_ny_cut(t, expected):
    assert fx_session_open(t) is expected
    assert fx_session_open(t.astimezone(DHAKA)) is expected           # tz-aware Dhaka input
    assert fx_session_open(t.replace(tzinfo=None)) is expected        # naive = UTC
    assert eb._fx_closed_hour(pd.Timestamp(t.replace(tzinfo=None))) is (not expected)


def test_dst_switch_weekend_nov1():
    # US DST ends Sun 2026-11-01 02:00 local; that Sunday the open is 17:00 EST = 22:00 UTC.
    assert fx_session_open(datetime(2026, 11, 1, 21, 30, tzinfo=U)) is False
    assert fx_session_open(datetime(2026, 11, 1, 22, 0, tzinfo=U)) is True
    # The Friday before (Oct 30) is still EDT: close 21:00 UTC.
    assert fx_session_open(datetime(2026, 10, 30, 20, 59, tzinfo=U)) is True
    assert fx_session_open(datetime(2026, 10, 30, 21, 0, tzinfo=U)) is False


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_fxs_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_script_gates_use_shared_helper():
    for name in ("refresh_if_gap", "mt5_spread_sampler"):
        mod = _load(name)
        for t, expected in CASES:
            assert mod.fx_open(t) is expected, (name, t)


@pytest.mark.parametrize("t,expected", CASES)
def test_explore_burst_weekend_gate(t, expected, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "explore.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    mod = _load("explore_paper_burst")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(mod, "_now_dhaka", lambda: t.astimezone(DHAKA))
    monkeypatch.setattr(mod, "http_json", lambda *a, **k: (0, {"error": "no desk in test"}))
    rc = mod.main(["--no-mt5-exec"])
    res = json.loads(capsys.readouterr().out)
    if expected:  # gate passed -> reaches the (mocked, failing) health check
        assert res.get("market_closed") is not True and res["reason"] == "desk API health check failed"
    else:
        assert rc == 0 and res["market_closed"] is True and "America/New_York" in res["reason"]


@pytest.mark.parametrize("t,expected", CASES)
def test_main_shadow_burst_weekend_gate(t, expected, tmp_path, monkeypatch, capsys):
    mod = _load("shadow_paper_burst")
    monkeypatch.setattr(mod, "JOURNAL", tmp_path / "journal.jsonl")

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)

    monkeypatch.setattr(mod, "datetime", FakeDT)
    monkeypatch.setattr(mod, "http_json", lambda *a, **k: (0, {"error": "no desk in test"}))
    monkeypatch.setattr("sys.argv", ["shadow_paper_burst.py"])
    rc = mod.main()
    res = json.loads(capsys.readouterr().out)
    if expected:
        assert rc == 2 and res["market_closed"] is False and res["reason"] == "desk API health check failed"
    else:
        assert rc == 0 and res["market_closed"] is True and "America/New_York" in res["reason"]


def test_refresh_if_gap_targets_last_session_bar_over_weekend():
    mod = _load("refresh_if_gap")
    sat = datetime(2026, 10, 24, 5, 0, tzinfo=U)
    assert mod.latest_closed_bar_open(sat) == datetime(2026, 10, 23, 20, 0, tzinfo=U)          # summer
    assert mod.latest_closed_bar_open(datetime(2026, 11, 7, 5, tzinfo=U)) == datetime(2026, 11, 6, 21, 0, tzinfo=U)  # winter
    assert mod.latest_closed_bar_open(datetime(2026, 10, 23, 15, 5, tzinfo=U)) == datetime(2026, 10, 23, 14, 0, tzinfo=U)
    # Sunday just after the summer open: the 21:00Z bar has not closed yet -> still Friday's last bar.
    assert mod.latest_closed_bar_open(datetime(2026, 10, 25, 21, 30, tzinfo=U)) == datetime(2026, 10, 23, 20, 0, tzinfo=U)
    rows = [{"pair": "EURUSD", "last_bar_dhaka": "2026-10-23 23:00:00 Asia/Dhaka"}]  # 17:00Z tip (the Oct 23 case)
    assert mod.gap_pairs(rows, datetime(2026, 10, 23, 21, 5, tzinfo=U))[0]["closed_hours_missing"] == 3
    rows = [{"pair": "EURUSD", "last_bar_dhaka": "2026-10-24 02:00:00 Asia/Dhaka"}]  # 20:00Z tip present
    assert mod.gap_pairs(rows, sat) == []
