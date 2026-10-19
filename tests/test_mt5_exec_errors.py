"""MT5 demo refusals (e.g. retcode 10027 AutoTrading disabled) must surface as burst errors (exit 4)."""
import importlib.util
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_exec as mx
from test_explore_broker_truth import _open  # noqa: F401
from test_explore_fill_booking import FillMT5

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("epb_errs", ROOT / "scripts" / "explore_paper_burst.py")
BURST = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(BURST)
NOW = datetime(2026, 10, 14, 19, tzinfo=timezone.utc)


class AlgoOffMT5(FillMT5):
    """Terminal with the Algo Trading button off: every order_send is refused with 10027."""

    algo_on = True

    def terminal_info(self):
        return NS(trade_allowed=self.algo_on, connected=True)

    def order_send(self, req):
        if not self.algo_on:
            self.sent.append(req)
            return NS(retcode=10027, order=0, deal=0, price=0.0, comment="AutoTrading disabled by client")
        return super().order_send(req)


@pytest.fixture()
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "explore.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")


def _pending(fake):
    _open()
    mx.mirror(["exp_abc"], mt5=fake)
    rec = mx.reconcile_broker(mt5=fake)
    out = eb.run_pass([{"pair": "EURUSD", "last": 1.1001}], now=NOW, hold_ids=rec["hold"], broker_exit_ids=rec["live"])
    return out["pending_broker_exits"]


def test_trade_allowed_precheck():
    assert mx.trade_allowed_error(NS()) is None  # mock without terminal_info
    assert mx.trade_allowed_error(NS(terminal_info=lambda: NS(trade_allowed=True))) is None
    err = mx.trade_allowed_error(NS(terminal_info=lambda: NS(trade_allowed=False)))
    assert err["precheck"] == "trade_allowed" and "trade_allowed=False" in err["error"]
    err = mx.trade_allowed_error(NS(terminal_info=lambda: None, last_error=lambda: (1, "x")))
    assert err["precheck"] == "terminal_info"


def test_close_refused_10027_is_an_error_with_retcode_and_ticket(journal):
    fake = AlgoOffMT5()
    pending = _pending(fake)
    fake.algo_on = False
    res = mx.close_and_book(pending, mt5=fake, now=NOW)
    assert not res["booked"]
    assert res["precheck"]["precheck"] == "trade_allowed"
    refused = [e for e in res["errors"] if e.get("retcode") == 10027]
    assert refused and refused[0]["ticket"] and refused[0]["error"] == "AutoTrading disabled by client"
    assert "exp_abc" in eb.open_positions(eb.load())  # nothing booked; retried next pass


def test_open_refused_10027_and_precheck_in_mirror(journal):
    _open()
    fake = AlgoOffMT5(); fake.algo_on = False
    res = mx.mirror(["exp_abc"], mt5=fake)
    assert res["precheck"]["precheck"] == "trade_allowed"
    assert any(e.get("retcode") == 10027 for e in res["errors"])


def test_burst_records_close_refusals_in_mt5_exec_errors_and_step_errors():
    result = {
        "broker_reconcile": {"enabled": True, "precheck": {"precheck": "trade_allowed", "error": "algo off"}},
        "broker_exits": {"enabled": True, "booked": [], "errors": [
            {"position_id": "p1", "pair": "EURUSD", "event": "mt5_close", "ticket": 43778885, "retcode": 10027,
             "error": "AutoTrading disabled by client"},
            {"position_id": "p2", "pair": "GBPUSD", "event": "mt5_close", "ticket": 43778886, "retcode": 10027,
             "error": "AutoTrading disabled by client"}]},
        "mt5_exec": {"enabled": True, "opened": [], "closed": [], "errors": []},
    }
    fails = BURST.record_exec_failures(result)
    assert len(fails) == 3  # two closes + the precheck
    tickets = {e.get("ticket") for e in result["mt5_exec"]["errors"]}
    assert {43778885, 43778886} <= tickets
    steps = [s["step"] for s in result["step_errors"]]
    assert steps.count("mt5_close") == 2 and "mt5_precheck" in steps
    assert any("retcode 10027" in s["error"] and "43778885" in s["error"] for s in result["step_errors"])
    assert not (not result.get("step_errors"))  # -> main() sets ok=False and returns 4


def test_burst_open_failure_and_connect_refusal_are_errors():
    result = {"broker_exits": {"enabled": True, "booked": [], "errors": []},
              "mt5_exec": {"enabled": True, "refused": True, "error": "refusing: account trade_mode=2 is not DEMO",
                           "opened": [], "errors": []}}
    fails = BURST.record_exec_failures(result)
    assert fails and result["step_errors"][0]["step"] == "mt5_open"
    result = {"mt5_exec": {"enabled": True, "errors": [{"event": "mt5_open", "position_id": "p", "retcode": 10027,
                                                        "error": "AutoTrading disabled by client"}]}}
    BURST.record_exec_failures(result)
    assert len(result["mt5_exec"]["errors"]) == 1 and "retcode 10027" in result["step_errors"][0]["error"]


def test_burst_clean_pass_has_no_errors():
    result = {"broker_reconcile": {"enabled": True, "hold": []},
              "broker_exits": {"enabled": True, "booked": [{"position_id": "p"}], "errors": []},
              "mt5_exec": {"enabled": True, "opened": [], "errors": []}}
    assert BURST.record_exec_failures(result) == [] and "step_errors" not in result
    assert BURST.record_exec_failures({"mt5_exec": {"enabled": False, "reason": "FORX_MT5_EXEC=off"}}) == []