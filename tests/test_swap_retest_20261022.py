import importlib.util, sys
from pathlib import Path
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("swr", ROOT / "research" / "lowfreq" / "swap_retest_20261022.py")
swr = importlib.util.module_from_spec(spec); sys.modules["swr"] = swr; spec.loader.exec_module(swr)


def test_week_is_seven_nights_with_wednesday_triple():
    ro = swr.lf.rollovers(pd.Timestamp("2020-01-05 22:00"), pd.Timestamp("2020-01-12 22:00"))  # Sun open -> next Sun
    assert list(ro.dayofweek) == [0, 1, 2, 3, 4] and swr.nights(ro).sum() == 7


def test_swap_fin_sign_and_scale():
    fin = swr.make_swap_fin({"EURUSD": (-3.51, 1.49)})
    t0, t1 = pd.Timestamp("2020-01-07 22:00"), pd.Timestamp("2020-01-08 22:00")  # one Wednesday rollover
    acc_l, mk, n = fin("EURUSD", 1, 1.12, t0, t1, None)
    acc_s, _, _ = fin("EURUSD", -1, 1.12, t0, t1, None)
    assert n == 1 and mk == 0 and acc_l == pytest.approx(3 * -3.51 / 100 / 360) and acc_s == pytest.approx(3 * 1.49 / 100 / 360)


def test_hist_plus_markup_equals_swap_when_rates_constant():
    idx = pd.date_range("2019-01-01", "2026-12-31", freq="D")
    rates = pd.DataFrame({"EUR": 0.5, "USD": 2.0}, index=idx)
    sw = {"EURUSD": (-3.51, 1.49)}
    a = swr.make_hist_plus_markup_fin(sw, rates)("EURUSD", -1, 1.1, pd.Timestamp("2020-01-06 22:00"), pd.Timestamp("2020-01-10 22:00"), None)
    b = swr.make_swap_fin(sw)("EURUSD", -1, 1.1, pd.Timestamp("2020-01-06 22:00"), pd.Timestamp("2020-01-10 22:00"), None)
    assert a[0] == pytest.approx(b[0])
