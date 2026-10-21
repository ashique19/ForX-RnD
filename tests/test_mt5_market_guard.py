"""Broker market-open guard (2026-10-22 NZDUSD exp_6876c3254d4a 10018 'Market closed' at a session break)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_exec as mx
from tests.test_mt5_exec import FakeMT5

NOW = datetime(2026, 10, 21, 18, 2, 7, tzinfo=timezone.utc)  # the real 10018 send time


def _server_epoch(utc_dt):
    """Inverse of mx.server_to_utc for a summer date (server = UTC+3)."""
    wall = utc_dt.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
    return (wall - datetime(1970, 1, 1)).total_seconds()


class TickMT5(FakeMT5):
    def __init__(self, last_tick_utc, trade_mode=4):
        super().__init__()
        self.t = _server_epoch(last_tick_utc); self.tm = trade_mode

    def symbol_info(self, s):
        return NS(digits=5, point=0.00001, filling_mode=1, trade_mode=self.tm, trade_stops_level=5, trade_freeze_level=0)

    def symbol_info_tick(self, s):
        return NS(bid=0.57012, ask=0.57028, time=self.t)


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "explore.jsonl"))


OPEN = {"event": "open", "pair": "NZDUSD", "side": "SELL", "position_id": "exp_6876c3254d4a", "entry_mid": 0.5702,
        "sl": 0.5715105734091961, "tp": 0.568889426590804, "ts_utc": "2026-10-21T18:02:04Z"}


def test_server_epoch_helper_roundtrip():
    assert mx.server_to_utc(_server_epoch(NOW)) == NOW


def test_incident_replay_session_break_not_sent(journal):
    eb.append(dict(OPEN))
    fake = TickMT5(datetime(2026, 10, 21, 17, 58, 58, tzinfo=timezone.utc))  # last tick before the 17:59-18:05 UTC break
    out = mx.mirror(["exp_6876c3254d4a"], mt5=fake, now=NOW)
    assert fake.sent == [] and not out["opened"] and len(out["errors"]) == 1
    row = [e for e in eb.load() if e["event"] == "mt5_open"][0]
    assert row["ok"] is False and row["sent"] is False and row["precheck"] == "market_closed"
    assert "stale tick" in row["error"]
    mx.mirror(["exp_6876c3254d4a"], mt5=fake, now=NOW)  # one attempt only
    assert fake.sent == []


def test_fresh_tick_sends(journal):
    eb.append(dict(OPEN))
    fake = TickMT5(NOW - timedelta(seconds=3))
    out = mx.mirror(["exp_6876c3254d4a"], mt5=fake, now=NOW)
    assert len(out["opened"]) == 1 and fake.sent


@pytest.mark.parametrize("tm,side,closed", [(4, "SELL", False), (3, "SELL", True), (0, "BUY", True), (1, "BUY", False),
                                            (1, "SELL", True), (2, "SELL", False)])
def test_trade_mode(tm, side, closed):
    fake = TickMT5(NOW - timedelta(seconds=1), trade_mode=tm)
    assert (mx.market_closed_reason(fake, "NZDUSD", side, NOW) is not None) == closed


def test_threshold_boundary():
    assert mx.market_closed_reason(TickMT5(NOW - timedelta(seconds=119)), "X", "BUY", NOW) is None
    assert mx.market_closed_reason(TickMT5(NOW - timedelta(seconds=121)), "X", "BUY", NOW) is not None


def test_market_status_lists_only_closed():
    class Two(TickMT5):
        def symbol_info_tick(self, s):
            return NS(bid=1.0, ask=1.0001, time=self.t if s == "NZDUSD" else _server_epoch(NOW))
    fake = Two(NOW - timedelta(minutes=3))
    st = mx.market_status(["NZDUSD", "EURUSD"], mt5=fake, resolve=lambda ps: {p: p for p in ps}, now=NOW)
    assert list(st) == ["NZDUSD"]


def test_run_pass_entry_blocked_books_no_fill(journal):
    row = {"pair": "NZDUSD", "raw_signal": "SELL", "signal": "HOLD", "confidence": 0.51, "validity": "OK", "status": "ready",
           "gate_reason": "", "last_signal_dhaka": "2026-10-21 23:00:00", "last_bar_dhaka": "2026-10-21 23:00:00", "last": 0.5702}
    row.update({"stop": 0.5715, "target": 0.5689, "horizon_bars": 8})
    out = eb.run_pass([row], bars_fn=lambda pair: None, entry_blocked={"NZDUSD": "stale tick"})
    # control: same row without the block books a fill
    assert not [e for e in eb.load() if e["event"] == "open"]
    dec = [e for e in eb.load() if e["event"] == "decision"]
    assert dec and dec[-1]["take"] is False and dec[-1]["reason"].startswith("broker_market_closed")


def test_run_pass_unblocked_control_books_fill(journal):
    row = {"pair": "NZDUSD", "raw_signal": "SELL", "signal": "HOLD", "confidence": 0.51, "validity": "OK", "status": "ready",
           "gate_reason": "", "last_signal_dhaka": "2026-10-21 23:00:00", "last_bar_dhaka": "2026-10-21 23:00:00", "last": 0.5702,
           "stop": 0.5715, "target": 0.5689, "horizon_bars": 8}
    eb.run_pass([row], bars_fn=lambda pair: None, entry_blocked={"EURUSD": "stale tick"})
    assert [e for e in eb.load() if e["event"] == "open"]
