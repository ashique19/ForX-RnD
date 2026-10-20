"""Tests for scripts/splice_history_segment.py (synthetic data only)."""
import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("splice", ROOT / "scripts" / "splice_history_segment.py")
S = importlib.util.module_from_spec(spec); spec.loader.exec_module(S)


def _hist(idx, base=1.1):
    n = len(idx)
    return pd.DataFrame({"Open": [base + i * 1e-4 for i in range(n)], "High": [base + i * 1e-4 + 2e-4 for i in range(n)],
                         "Low": [base + i * 1e-4 - 2e-4 for i in range(n)], "Close": [base + (i + 1) * 1e-4 for i in range(n)],
                         "Volume": [100.0 + i for i in range(n)]}, index=idx)


def test_splice_keeps_tail_text_and_uses_history_values():
    idx = pd.date_range("2025-01-06", periods=48, freq="h")
    seam = idx[30]
    desk_lines = ["Datetime,Open,High,Low,Close,Volume"] + [f"{t:%Y-%m-%d %H:%M:%S},1.2000000476837158,1.3,1.1,1.25,7.0" for t in idx[:30]] \
        + [f"{t:%Y-%m-%d %H:%M:%S},1.10301,1.10333,1.10290,1.10311,55.0" for t in idx[30:]]
    hist = _hist(pd.date_range("2025-01-01", periods=400, freq="h"))
    out, info = S.build_spliced_text("\n".join(desk_lines) + "\n", hist, seam)
    lines = out.splitlines()
    assert lines[-18:] == desk_lines[-18:]  # kept rows byte-identical
    df = pd.read_csv(pd.io.common.StringIO(out), index_col=0, parse_dates=True)
    assert df.index.min() == idx[0] and not df.index.duplicated().any()
    pd.testing.assert_series_equal(df.loc[:idx[29], "Close"], hist.loc[idx[0]:idx[29], "Close"], check_names=False, check_freq=False)
    assert info["old_rows_replaced"] == 30 and info["history_rows_in"] == 30


def test_check_frame_weekend_vs_hole():
    idx = pd.date_range("2025-01-06", "2025-01-17 21:00", freq="h")  # Mon..Fri next week
    idx = idx[~((idx.dayofweek == 4) & (idx.hour > 21)) & ~(idx.dayofweek == 5) & ~((idx.dayofweek == 6) & (idx.hour < 22))]
    idx = idx.drop(pd.Timestamp("2025-01-08 10:00"))  # a mid-week hole
    df = _hist(idx)
    r = S.check_frame(df, "EURUSD", {}, idx[5])
    assert r["weekend_gaps"] == 1 and r["n_non_weekend_gaps"] == 1 and r["duplicates"] == 0