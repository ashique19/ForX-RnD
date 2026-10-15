from types import SimpleNamespace as NS

import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_exec as mx


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, mode=0):
        self.mode = mode
        self.sent = []
        self.positions = {}
        self.n = 1000
        self.modified = []

    def account_info(self):
        return NS(trade_mode=self.mode, server="DEMO-SRV")

    def last_error(self):
        return (0, "ok")

    def symbol_info(self, s):
        return NS(digits=5, filling_mode=1)

    def symbol_info_tick(self, s):
        return NS(bid=1.10000, ask=1.10012)

    def order_send(self, req):
        self.sent.append(req)
        self.n += 1
        if req.get("action") == 6:  # TRADE_ACTION_SLTP
            self.modified.append(req)
            return NS(retcode=10009, order=0, deal=0, price=0.0, comment="done")
        if "position" in req:
            self.positions.pop(req["position"], None)
            return NS(retcode=10009, order=self.n, deal=self.n + 5000, price=req["price"] - 0.00001, comment="done")
        self.positions[self.n] = NS(ticket=self.n, magic=req["magic"], comment=req["comment"], volume=req["volume"])
        return NS(retcode=10009, order=self.n, deal=self.n + 5000, price=req["price"] + 0.00002, comment="done")

    def positions_get(self, symbol=None, ticket=None):
        if ticket is not None:
            return tuple(p for t, p in self.positions.items() if t == ticket)
        return tuple(self.positions.values())

    def shutdown(self):
        pass


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    p = tmp_path / "explore.jsonl"
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(p))
    return p


def _open(pid="exp_abc", side="BUY"):
    eb.append({"event": "open", "pair": "EURUSD", "side": side, "position_id": pid, "entry_mid": 1.10006,
               "sl": 1.0980, "tp": 1.1040, "ts_utc": "2026-10-14T10:00:00Z"})


def test_refuses_non_demo(journal):
    _open()
    fake = FakeMT5(mode=2)
    out = mx.mirror(["exp_abc"], mt5=fake)
    assert out["refused"] and fake.sent == []
    rows = [e for e in eb.load() if e["event"] == "mt5_open"]
    assert rows and rows[0]["ok"] is False and rows[0]["refused"] is True


def test_assert_demo_raises_on_real():
    with pytest.raises(RuntimeError, match="not DEMO"):
        mx.assert_demo(FakeMT5(mode=2))


def test_open_logs_ticket_fill_slippage_spread(journal):
    _open()
    fake = FakeMT5()
    out = mx.mirror(["exp_abc"], mt5=fake)
    assert len(out["opened"]) == 1 and not out["errors"]
    req = fake.sent[0]
    # paper distances (-0.00206 / +0.00394) anchored on the ask we trade at
    assert req["volume"] == 0.01 and req["sl"] == 1.09806 and req["tp"] == 1.10406 and req["type"] == fake.ORDER_TYPE_BUY
    mod = fake.modified[0]  # then moved onto the actual fill (1.10014)
    assert mod["sl"] == 1.09808 and mod["tp"] == 1.10408 and mod["position"] == 1001
    reb = [e for e in eb.load() if e["event"] == "rebase"][0]
    assert reb["entry_mid"] == pytest.approx(1.10014) and reb["sl"] == 1.09808 and reb["tp"] == 1.10408
    pos = eb.open_positions(eb.load())["exp_abc"]
    assert pos["entry_mid"] == pytest.approx(1.10014) and pos["sl"] == 1.09808
    row = [e for e in eb.load() if e["event"] == "mt5_open"][0]
    assert row["ticket"] == 1001 and row["fill_price"] == pytest.approx(1.10014)
    assert row["slippage_pips"] == pytest.approx(0.2) and row["spread_pips"] == pytest.approx(1.2)


def test_no_double_open(journal):
    _open()
    fake = FakeMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    mx.mirror(["exp_abc"], mt5=fake)
    assert len([r for r in fake.sent if r.get("action") != 6]) == 1


def test_close_when_paper_closes(journal):
    _open(side="SELL")
    fake = FakeMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    eb.append({"event": "close", "pair": "EURUSD", "position_id": "exp_abc"})
    out = mx.mirror([], mt5=fake)
    assert len(out["closed"]) == 1
    close_req = fake.sent[-1]
    assert close_req["position"] == 1001 and close_req["type"] == fake.ORDER_TYPE_BUY
    assert not fake.positions


def test_close_already_closed_at_broker(journal):
    _open()
    fake = FakeMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    fake.positions.clear()
    eb.append({"event": "close", "pair": "EURUSD", "position_id": "exp_abc"})
    out = mx.mirror([], mt5=fake)
    assert out["closed"][0]["already_closed"] is True and len([r for r in fake.sent if r.get("action") != 6]) == 1


def test_disabled_by_env(journal, monkeypatch):
    monkeypatch.setenv("FORX_MT5_EXEC", "off")
    assert mx.mirror(["x"])["enabled"] is False


def test_explore_relaxations():
    cfg = eb.EXPLORE_CONFIG
    assert cfg["min_conf"] == 0.45 and cfg["max_positions_per_pair"] == 3 and cfg["drop_weekday_mute"]
    row = {"pair": "EURUSD", "raw_signal": "BUY", "confidence": 0.47, "muted": True, "validity": "OK", "status": "ready",
           "gate_reason": "conf=0.47 < min 0.60 | muted Monday (UTC weekday gate) - lifts Tue 00:00 UTC",
           "last_signal_dhaka": "a", "last_bar_dhaka": "a", "last": 1.1}
    assert eb.explore_eligible(row, cfg)[0]
    assert not eb.explore_eligible({**row, "gate_reason": row["gate_reason"] + " | news blackout"}, cfg)[0]
    assert not eb.explore_eligible(row, {**cfg, "drop_weekday_mute": False})[0]
