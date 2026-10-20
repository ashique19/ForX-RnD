"""Stops pre-check before order_send (2026-10-21 NZDUSD exp_99d6a7d32c6b 10016 'Invalid stops')."""
from types import SimpleNamespace as NS

import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_exec as mx
from tests.test_mt5_exec import FakeMT5


class QuoteMT5(FakeMT5):
    def __init__(self, bid, ask, stops_level=5, freeze_level=0):
        super().__init__()
        self.q = (bid, ask); self.lv = (stops_level, freeze_level)

    def symbol_info(self, s):
        return NS(digits=5, point=0.00001, filling_mode=1, trade_stops_level=self.lv[0], trade_freeze_level=self.lv[1])

    def symbol_info_tick(self, s):
        return NS(bid=self.q[0], ask=self.q[1])


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    p = tmp_path / "explore.jsonl"
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(p))
    return p


# The real journaled paper fill (board last mid) and the real quote at send (journal mt5_open row).
INCIDENT = {"event": "open", "pair": "NZDUSD", "side": "SELL", "position_id": "exp_99d6a7d32c6b", "entry_mid": 0.56847,
            "sl": 0.5697366868573608, "tp": 0.5672033131426393, "ts_utc": "2026-10-20T21:15:48Z"}


def test_incident_replay_refused_before_send(journal):
    eb.append(dict(INCIDENT))
    fake = QuoteMT5(bid=0.56733, ask=0.56908)  # 17.5-pip rollover spread > 12.67-pip SL distance
    out = mx.mirror(["exp_99d6a7d32c6b"], mt5=fake)
    assert fake.sent == [] and not out["opened"] and len(out["errors"]) == 1
    row = [e for e in eb.load() if e["event"] == "mt5_open"][0]
    assert row["ok"] is False and row["sent"] is False and row["precheck"] == "invalid_stops"
    assert row["invalid"] == ["sl"] and row["stops_ref"] == "ask" and row["stops_level_points"] == 5
    assert row["sl_req"] == 0.5686 and row["sl_req"] < row["ask"]  # SL inside the spread
    assert row["error"].startswith("invalid_stops_precheck")
    # paper untouched: no rebase, same SL/TP, still one attempt only
    assert not [e for e in eb.load() if e["event"] == "rebase"]
    pos = eb.open_positions(eb.load())["exp_99d6a7d32c6b"]
    assert pos["sl"] == INCIDENT["sl"] and pos["tp"] == INCIDENT["tp"] and pos["entry_mid"] == INCIDENT["entry_mid"]
    mx.mirror(["exp_99d6a7d32c6b"], mt5=fake)
    assert fake.sent == []


def test_normal_spread_sends_same_distances(journal):
    eb.append(dict(INCIDENT))
    fake = QuoteMT5(bid=0.56840, ask=0.56852)  # 1.2-pip spread
    out = mx.mirror(["exp_99d6a7d32c6b"], mt5=fake)
    assert len(out["opened"]) == 1
    req = fake.sent[0]
    sl_d, tp_d = INCIDENT["sl"] - INCIDENT["entry_mid"], INCIDENT["tp"] - INCIDENT["entry_mid"]
    assert req["sl"] == round(0.56840 + sl_d, 5) and req["tp"] == round(0.56840 + tp_d, 5)  # never widened


def _check(side, bid, ask, sl, tp, level=5):
    info = NS(digits=5, point=0.00001, trade_stops_level=level, trade_freeze_level=0)
    return mx.stops_precheck(info, NS(bid=bid, ask=ask), side, sl, tp)


def test_sell_rules_vs_ask():
    assert _check("SELL", 1.10000, 1.10010, 1.10015, 1.09900) is None       # 5 pts above ask: ok
    assert _check("SELL", 1.10000, 1.10010, 1.10014, 1.09900)["invalid"] == ["sl"]  # 4 pts < stops_level
    assert _check("SELL", 1.10000, 1.10010, 1.10100, 1.10006)["invalid"] == ["tp"]  # TP within 5 pts of ask


def test_buy_rules_vs_bid():
    assert _check("BUY", 1.10000, 1.10010, 1.09995, 1.10100) is None
    bad = _check("BUY", 1.10000, 1.10010, 1.09996, 1.10004)
    assert bad["invalid"] == ["sl", "tp"] and bad["stops_ref"] == "bid"


def test_freeze_level_and_zero_level():
    info = NS(digits=5, point=0.00001, trade_stops_level=0, trade_freeze_level=10)
    assert mx.stops_precheck(info, NS(bid=1.1, ask=1.1001), "BUY", 1.09995, 1.1010)["invalid"] == ["sl"]
    info0 = NS(digits=5, trade_stops_level=0, trade_freeze_level=0)  # no point attr -> 10**-digits
    assert mx.stops_precheck(info0, NS(bid=1.1, ask=1.1001), "SELL", 1.1001, 1.0990)["invalid"] == ["sl"]  # SL == ask
    assert mx.stops_precheck(info0, NS(bid=1.1, ask=1.1001), "SELL", 1.10011, 1.0990) is None
