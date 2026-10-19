"""Read-only stop/payoff analysis helpers (scripts/stop_payoff_analysis.py)."""
import importlib.util
import pathlib

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("spa", ROOT / "scripts" / "stop_payoff_analysis.py")
SPA = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(SPA)


def _bars(rows, start="2026-10-14 10:00"):
    idx = pd.date_range(start, periods=len(rows), freq="1h")
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def test_window_skips_entry_bar_and_stops_at_horizon():
    b = _bars([(1, 1, 1, 1)] * 20, start="2026-10-14 09:00")
    w = SPA.window(b, pd.Timestamp("2026-10-14 09:35"), 8)
    assert w.index[0] == pd.Timestamp("2026-10-14 10:00") and w.index[-1] == pd.Timestamp("2026-10-14 17:00")


def test_simulate_sl_wins_same_bar_tie_and_tp_and_duration():
    both = _bars([(1.1, 1.103, 1.097, 1.1)])
    assert SPA.simulate("BUY", 1.1, 0.002, 0.002, both)["reason"] == "sl"
    tp = _bars([(1.1, 1.1025, 1.0995, 1.102)])
    assert SPA.simulate("BUY", 1.1, 0.002, 0.002, tp) == {"reason": "tp", "exit": pytest.approx(1.102), "bar": tp.index[0]}
    flat = _bars([(1.1, 1.1005, 1.0995, 1.1003)] * 3)
    s = SPA.simulate("SELL", 1.1, 0.002, 0.002, flat)
    assert s["reason"] == "duration" and s["exit"] == pytest.approx(1.1003)
    assert SPA.simulate("BUY", 1.1, 0.002, 0.002, flat.iloc[:0]) is None


def test_breakeven_moves_stop_from_next_bar_only():
    b = _bars([(1.1, 1.1011, 1.0999, 1.101), (1.101, 1.1012, 1.0995, 1.1)])
    s = SPA.simulate("BUY", 1.1, 0.002, 0.004, b, be_trigger=0.5)
    assert s["reason"] == "be" and s["exit"] == pytest.approx(1.1)
    assert SPA.simulate("BUY", 1.1, 0.002, 0.004, b)["reason"] == "duration"


def test_excursions_and_summary():
    b = _bars([(1.1, 1.103, 1.099, 1.1)])
    mae, mfe = SPA.excursions("SELL", 1.1, b)
    assert mae == pytest.approx(0.003) and mfe == pytest.approx(0.001)
    s = SPA.summarize([1.0, 1.0, -1.0, -0.5], [20, 20, -20, -10])
    assert s["win_rate"] == 0.5 and s["payoff"] == pytest.approx(1.333, abs=1e-3) and s["pf"] == pytest.approx(1.333, abs=1e-3)
    assert s["breakeven_win_rate"] == pytest.approx(0.429, abs=1e-3)


def test_variants_tp_fraction_and_atr():
    t = {"pair": "EURUSD", "entry": 1.1, "sl": 1.098, "tp": 1.102, "atr_pips": 10.0}
    v = SPA.variants(t)
    assert v["SL 1 x TP distance"][0] == pytest.approx(0.002) and v["SL 0.5 x TP distance"][0] == pytest.approx(0.001)
    assert v["SL 1.5 ATR"][0] == pytest.approx(0.0015) and v["current + breakeven at +1R"] == (pytest.approx(0.002), 1.0)


def test_script_has_no_write_calls():
    src = (ROOT / "scripts" / "stop_payoff_analysis.py").read_text(encoding="utf-8")
    for bad in ("eb.append(", "append_event(", "order_send(", ".to_csv(", "write_text(", "/pipeline/", "/model/train"):
        assert bad not in src