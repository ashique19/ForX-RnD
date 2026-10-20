"""Unit checks for the pre-registered entry-edge research script (06dc692)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "entry_edge"))
import entry_edge_20261020 as E  # noqa: E402


def _t(pair, et, xb, conf=0.7):
    return {"pair": pair, "entry_time": pd.Timestamp(et), "exit_bar_time": pd.Timestamp(xb), "confidence": conf, "r": 1.0}


def test_usd_dedup_skips_overlapping_usd_and_keeps_crosses():
    t = pd.DataFrame([
        _t("EURUSD", "2020-01-01 10:00", "2020-01-01 12:00"),
        _t("GBPUSD", "2020-01-01 12:00", "2020-01-01 14:00"),  # inside EURUSD (open until 13:00) -> skip
        _t("EURJPY", "2020-01-01 11:00", "2020-01-01 12:00"),  # cross, kept
        _t("USDJPY", "2020-01-01 13:00", "2020-01-01 15:00"),  # EURUSD over at 13:00 -> kept
    ])
    kept = E.usd_dedup(t)
    assert sorted(kept["pair"]) == ["EURJPY", "EURUSD", "USDJPY"]


def test_usd_dedup_simultaneous_prefers_higher_confidence():
    t = pd.DataFrame([_t("EURUSD", "2020-01-01 10:00", "2020-01-01 11:00", 0.61),
                      _t("AUDUSD", "2020-01-01 10:00", "2020-01-01 11:00", 0.80)])
    assert list(E.usd_dedup(t)["pair"]) == ["AUDUSD"]


def test_h1_gate_holds_when_cost_exceeds_tenth_of_r():
    idx = pd.date_range("2020-01-01", periods=2, freq="h")
    pred = pd.Series([E.BUY, E.SELL], index=idx)
    atr = pd.Series([0.0010, 0.0005], index=idx)  # 1.4 pip RT: 0.07R kept, 0.14R held
    out = E.h1_gate(pred, "EURUSD", 1.4, atr)
    assert list(out) == [E.BUY, E.HOLD]


def test_strength_is_causal_and_signed():
    idx = pd.date_range("2020-01-01", periods=30, freq="h")
    up = pd.DataFrame({"Close": np.exp(np.arange(30) * 0.001)}, index=idx)
    flat = pd.DataFrame({"Close": np.ones(30)}, index=idx)
    st = E.strength_table({"EURUSD": up, "USDJPY": flat})
    assert np.isclose(st.loc[idx[5], "S_EUR_1"], 0.001)
    assert np.isclose(st.loc[idx[5], "S_USD_1"], (-0.001 + 0.0) / 2)
    # changing a future close does not change strength at t
    up2 = up.copy(); up2.iloc[20:] *= 2
    st2 = E.strength_table({"EURUSD": up2, "USDJPY": flat})
    assert np.allclose(st.loc[: idx[19]].fillna(0), st2.loc[: idx[19]].fillna(0))
