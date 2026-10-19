"""Mirrored explore trades: paper exit decided by paper, booked at the real MT5 demo close fill (mock MT5)."""
import importlib.util
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_exec as mx
from test_explore_broker_truth import DealMT5, _open, _srv  # noqa: F401  (fixture-free helpers)

_spec = importlib.util.spec_from_file_location(
    "explore_broker_reconcile", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "explore_broker_reconcile.py")
REC = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(REC)


class FillMT5(DealMT5):
    """Closing orders also land in deal history (reason EXPERT), like the real terminal."""

    fail_close = False

    def order_send(self, req):
        if "position" in req and req.get("action") != 6 and self.fail_close:
            self.sent.append(req)
            return NS(retcode=10019, order=0, deal=0, price=0.0, comment="no money")
        res = super().order_send(req)
        if "position" in req and req.get("action") != 6:
            t = req["position"]
            self.deals[t] = (
                NS(entry=0, volume=0.01, price=1.10014, time=_srv(datetime(2026, 10, 14, 10)), time_msc=0, reason=3,
                   ticket=1, profit=0.0, commission=0.0, swap=0.0, fee=0.0),
                NS(entry=1, volume=0.01, price=res.price, time=_srv(datetime(2026, 10, 14, 19, 0, 30)), time_msc=0,
                   reason=3, ticket=res.deal, profit=-0.15, commission=0.0, swap=0.0, fee=0.0),
            )
        return res


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "explore.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")


NOW = datetime(2026, 10, 14, 19, tzinfo=timezone.utc)


def _setup():
    _open()
    fake = FillMT5()
    mx.mirror(["exp_abc"], mt5=fake)  # fill 1.10014, broker sl 1.09808
    rec = mx.reconcile_broker(mt5=fake)
    out = eb.run_pass([{"pair": "EURUSD", "last": 1.1001}], now=NOW, hold_ids=rec["hold"], broker_exit_ids=rec["live"])
    return fake, out


def test_paper_decides_but_does_not_book_mirrored_exit(journal):
    fake, out = _setup()
    assert out["closes"] == [] and [p["paper_exit_reason"] for p in out["pending_broker_exits"]] == ["duration"]
    assert not [e for e in eb.load() if e["event"] == "close"] and fake.positions


def test_demo_close_same_pass_books_real_fill(journal):
    fake, out = _setup()
    res = mx.close_and_book(out["pending_broker_exits"], mt5=fake, now=NOW)
    assert len(res["booked"]) == 1 and not res["errors"] and not fake.positions
    c = [e for e in eb.load() if e["event"] == "close"][0]
    assert c["exit_basis"] == "mt5_fill" and c["exit_reason"] == "duration"
    assert c["exit_mid"] == pytest.approx(1.09999) and c["paper_exit_price"] == pytest.approx(1.1001)
    assert c["exit_time_utc"] == "2026-10-14T19:00:30Z"
    assert c["pnl_r"] == pytest.approx((1.09999 - 1.10014) / 0.00206, abs=1e-3) and c["mt5_profit_usd"] == -0.15
    n = len(fake.sent)
    assert mx.mirror([], mt5=fake)["closed"] == [] and len(fake.sent) == n  # no second close
    s = eb.sum_r()
    assert s["broker_truth"] == 1 and s["pips_net_after_costs"] == pytest.approx(-1.5, abs=0.01)  # no assumed cost on real fills


def test_failed_demo_close_books_nothing(journal):
    fake, out = _setup()
    fake.fail_close = True
    res = mx.close_and_book(out["pending_broker_exits"], mt5=fake, now=NOW)
    assert res["errors"] and not res["booked"]
    assert "exp_abc" in eb.open_positions(eb.load())  # retried next pass, never a paper-only close
    assert [e for e in eb.load() if e["event"] == "mt5_close"][0]["ok"] is False


def test_broker_closed_between_reconcile_and_close(journal):
    fake, out = _setup()
    fake.broker_hit(1001, 1.09808, 4, _srv(datetime(2026, 10, 14, 18, 59)))
    res = mx.close_and_book(out["pending_broker_exits"], mt5=fake, now=NOW)
    c = [e for e in eb.load() if e["event"] == "close"][0]
    assert res["booked"][0]["exit_basis"] == "mt5_broker" and c["exit_reason"] == "sl"
    assert c["pnl_r"] == pytest.approx(-1.0, abs=1e-3)


def test_exec_off_close_and_book_disabled(journal, monkeypatch):
    monkeypatch.setenv("FORX_MT5_EXEC", "off")
    assert mx.close_and_book([{"position_id": "x"}])["enabled"] is False


def test_backfill_demo_fill_annotation(journal):
    _open()
    fake = FillMT5()
    mx.mirror(["exp_abc"], mt5=fake)
    eb.run_pass([{"pair": "EURUSD", "last": 1.1001}], now=NOW)  # legacy paper close at mark
    mx.mirror([], mt5=fake)                                     # demo closed later at market
    ev = eb.load()
    rows, skipped = REC.demo_fill_annotations(ev, fake)
    assert len(rows) == 1 and not skipped and rows[0]["annotation_type"] == "demo_fill"
    assert rows[0]["corrected_exit_mid"] == pytest.approx(1.09999)
    for r in rows:
        eb.append(r)
    assert REC.demo_fill_annotations(eb.load(), fake)[0] == []  # idempotent
    assert eb.sum_r()["sum_r"] == pytest.approx(rows[0]["corrected_pnl_r"])
    assert eb.sum_r(ignore_types=("demo_fill",))["sum_r"] != eb.sum_r()["sum_r"]
