"""Broker-truth exits for the exploration book (mock MT5; never touches a real terminal)."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_exec as mx
from test_mt5_exec import FakeMT5


class DealMT5(FakeMT5):
    """FakeMT5 plus a deal history."""

    def __init__(self, mode=0):
        super().__init__(mode)
        self.deals = {}

    def history_deals_get(self, position=None):
        return tuple(self.deals.get(position, ()))

    def broker_hit(self, ticket, price, reason, server_epoch, profit=-1.5):
        self.positions.pop(ticket, None)
        self.deals[ticket] = (
            NS(entry=0, volume=0.01, price=1.10014, time=server_epoch - 3600, time_msc=0, reason=3, ticket=1, profit=0.0,
               commission=0.0, swap=0.0, fee=0.0),
            NS(entry=1, volume=0.01, price=price, time=server_epoch, time_msc=0, reason=reason, ticket=777, profit=profit,
               commission=0.0, swap=0.0, fee=0.0),
        )


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    p = tmp_path / "explore.jsonl"
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(p))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    return p


def _open(pid="exp_abc", side="BUY"):
    sl, tp = (1.0980, 1.1040) if side == "BUY" else (1.1040, 1.0980)
    eb.append({"event": "open", "pair": "EURUSD", "side": side, "position_id": pid, "entry_mid": 1.10006,
               "sl": sl, "tp": tp, "ts_utc": "2026-10-14T10:00:00Z", "horizon_bars": 8,
               "assumed_cost_pips": 1.4})


def _srv(utc: datetime, off_h=3) -> float:
    return (utc.replace(tzinfo=None) - datetime(1970, 1, 1)).total_seconds() + off_h * 3600


def test_server_to_utc_summer_and_winter():
    assert mx.server_to_utc(_srv(datetime(2026, 10, 16, 3, 47), 3)) == datetime(2026, 10, 16, 3, 47, tzinfo=timezone.utc)
    assert mx.server_to_utc(_srv(datetime(2026, 12, 1, 10, 0), 2)) == datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)


def test_broker_closed_first_books_real_exit(journal):
    _open()
    fake = DealMT5()
    mx.mirror(["exp_abc"], mt5=fake)  # ticket 1001, fill 1.10014, sl 1.09808
    fake.broker_hit(1001, 1.10408, 5, _srv(datetime(2026, 10, 14, 13, 30)), profit=3.94)
    rec = mx.reconcile_broker(mt5=fake)
    assert [c["position_id"] for c in rec["closed"]] == ["exp_abc"] and rec["hold"] == []
    close = [e for e in eb.load() if e["event"] == "close"][0]
    assert close["exit_basis"] == "mt5_broker" and close["exit_reason"] == "tp"
    assert close["exit_mid"] == pytest.approx(1.10408) and close["exit_time_utc"] == "2026-10-14T13:30:00Z"
    assert close["pnl_r"] == pytest.approx(0.00394 / 0.00206, abs=1e-3) and close["mt5_profit_usd"] == 3.94
    # paper may not exit it again and mirror must not try to close it at the broker
    n_sent = len(fake.sent)
    out = eb.run_pass([], now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    assert out["closes"] == []
    assert mx.mirror([], mt5=fake)["closed"] == [] and len(fake.sent) == n_sent
    s = eb.sum_r()
    assert s["broker_truth"] == 1 and s["pips_net_after_costs"] == pytest.approx(39.4, abs=0.1)


def test_live_at_broker_paper_exits_and_closes_mt5(journal):
    _open(side="SELL")
    fake = DealMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    rec = mx.reconcile_broker(mt5=fake)
    assert rec["live"] == ["exp_abc"] and not rec["closed"]
    out = eb.run_pass([{"pair": "EURUSD", "last": 1.1001}], now=datetime(2026, 10, 14, 19, tzinfo=timezone.utc),
                      hold_ids=rec["hold"])
    assert out["closes"][0]["exit_reason"] == "duration"
    m = mx.mirror([], mt5=fake)
    assert len(m["closed"]) == 1 and not fake.positions and fake.sent[-1]["position"] == 1001


def test_unknown_broker_state_holds_paper_exit(journal):
    _open()
    fake = DealMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    fake.history_deals_get = lambda position=None: None
    fake.positions.clear()
    rec = mx.reconcile_broker(mt5=fake)
    assert rec["hold"] == ["exp_abc"]
    out = eb.run_pass([{"pair": "EURUSD", "last": 1.0}], now=datetime(2026, 10, 15, tzinfo=timezone.utc), hold_ids=rec["hold"])
    assert out["closes"] == [] and "exp_abc" in eb.open_positions(eb.load())


def test_connect_failure_holds_all(journal, monkeypatch):
    monkeypatch.delenv("FORX_MT5_EXEC", raising=False)
    _open()
    eb.append({"event": "mt5_open", "position_id": "exp_abc", "pair": "EURUSD", "ok": True, "ticket": 5, "fill_price": 1.1})

    def boom(**_):
        raise RuntimeError("terminal not running")

    rec = mx.reconcile_broker(connect=boom)
    assert rec["hold"] == ["exp_abc"] and "terminal" in rec["error"]


def test_exec_off_keeps_paper_logic(journal, monkeypatch):
    monkeypatch.setenv("FORX_MT5_EXEC", "off")
    _open()
    assert mx.reconcile_broker()["enabled"] is False
    out = eb.run_pass([{"pair": "EURUSD", "last": 1.0979}], now=datetime(2026, 10, 14, 11, tzinfo=timezone.utc))
    assert out["closes"][0]["exit_reason"] == "sl"


def test_race_broker_closed_before_mirror_close_annotates(journal):
    _open()
    fake = DealMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    eb.run_pass([{"pair": "EURUSD", "last": 1.1001}], now=datetime(2026, 10, 14, 19, tzinfo=timezone.utc))
    fake.broker_hit(1001, 1.09808, 4, _srv(datetime(2026, 10, 14, 18, 30)))
    mx.mirror([], mt5=fake)
    ann = [e for e in eb.load() if e["event"] == "annotation"]
    assert len(ann) == 1 and ann[0]["annotation_type"] == "broker_reconcile" and ann[0]["corrected_exit_reason"] == "sl"
    assert ann[0]["corrected_pnl_r"] == pytest.approx(-1.0, abs=1e-3)
    assert eb.sum_r()["sum_r"] == pytest.approx(-1.0, abs=1e-3)
    assert eb.sum_r(use_annotations=False)["sum_r"] != eb.sum_r()["sum_r"]


def _bars(times, hi, lo, cl):
    return pd.DataFrame({"High": hi, "Low": lo, "Close": cl}, index=pd.DatetimeIndex(times, tz="UTC"))


def test_paper_only_intrabar_sl_tp():
    pos = {"ts_utc": "2026-10-14T10:05:00Z", "side": "BUY", "sl": 1.098, "tp": 1.104, "horizon_bars": 8}
    bars = _bars(["2026-10-14T11:00", "2026-10-14T12:00"], [1.101, 1.1045], [1.099, 1.100], [1.100, 1.103])
    hit = eb.path_exit(pos, bars, datetime(2026, 10, 14, 14, tzinfo=timezone.utc), eb.EXPLORE_CONFIG)
    assert hit["reason"] == "tp" and hit["price"] == 1.104


def test_missing_bars_defer_exit():
    pos = {"ts_utc": "2026-10-14T10:05:00Z", "side": "BUY", "sl": 1.098, "tp": 1.104, "horizon_bars": 2}
    bars = _bars(["2026-10-14T11:00", "2026-10-14T14:00"], [1.101, 1.101], [1.099, 1.099], [1.100, 1.100])
    hit = eb.path_exit(pos, bars, datetime(2026, 10, 14, 16, tzinfo=timezone.utc), eb.EXPLORE_CONFIG)
    assert hit["defer"] is True
    # weekend hole is not a gap
    pos2 = {"ts_utc": "2026-10-16T19:05:00Z", "side": "BUY", "sl": 1.098, "tp": 1.104, "horizon_bars": 2}
    bars2 = _bars(["2026-10-16T20:00", "2026-10-18T21:00"], [1.101, 1.101], [1.099, 1.099], [1.100, 1.1005])
    hit2 = eb.path_exit(pos2, bars2, datetime(2026, 10, 18, 23, tzinfo=timezone.utc), eb.EXPLORE_CONFIG)
    assert hit2["reason"] == "duration"


def test_duplicate_annotation_excludes_even_with_correction():
    ev = [{"event": "open", "position_id": "a", "pair": "EURUSD", "assumed_cost_pips": 1.0},
          {"event": "close", "position_id": "a", "pair": "EURUSD", "side": "BUY", "pnl_r": 1.0, "pnl_price": 0.002},
          {"event": "annotation", "annotation_type": "duplicate_entry", "ref_position_id": "a", "exclude_from_explore": True},
          {"event": "annotation", "annotation_type": "broker_reconcile", "ref_position_id": "a", "corrected_pnl_r": 2.0}]
    assert eb.sum_r(ev)["closed_counted"] == 0
    assert eb.sum_r(ev, ignore_types=eb.RECONCILE_TYPES)["sum_r"] == 1.0
    raw = eb.sum_r(ev, use_annotations=False)
    assert raw["pips_gross"] == 20.0 and raw["pips_net_after_costs"] == 19.0
