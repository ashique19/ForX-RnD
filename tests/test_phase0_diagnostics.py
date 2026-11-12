"""Phase 0 diagnostics on synthetic bars. Prices stay in tmp; nothing is written as data."""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

from forex_lab.config_loader import load_config
from forex_lab.features import true_range_atr
from forex_lab.holdout import train_mask
from forex_lab.paths import project_root

from research.phase0.common import (
    DECLARED_RT_PIPS,
    FRESH_HOLDOUT_START,
    judged_rt_pips,
    split_fresh_holdout,
    trade_window_mask,
)
from research.phase0.diagnostics import (
    block_bootstrap_ci,
    calibration,
    cost_vs_move,
    resimulate_sides,
    same_bar_sl_tp,
)
from research.phase0.leakage import (
    fold_bounds,
    label_scan,
    production_source_flags,
    purged_train_positions,
    purged_walk_forward,
    resolve_wf,
    trace_folds,
    unpurged_walk_forward,
)
from research.phase0.run import main


def _idx(n: int = 400, start: str = "2020-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="h")


def _wf(train=100, test=40, step=40, min_train=80) -> dict:
    return {"walk_forward": {"train_bars": train, "test_bars": test, "step_bars": step, "min_train_bars": min_train}}


def synthetic_ohlc(n: int, start: str = "2024-01-02", seed: int = 0, price: float = 1.10) -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="h")
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 0.00015, n)
    for i in range(30, n - 10, 24):
        direction = 1.0 if ((i // 24) % 2 == 0) else -1.0
        steps[i : i + 6] += direction * 0.0008
    close = np.empty(n)
    close[0] = price
    for i in range(1, n):
        close[i] = max(close[i - 1] * (1.0 + steps[i]), 1e-4)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    high = np.maximum(open_, close) + 0.00012
    low = np.minimum(open_, close) - 0.00012
    vol = rng.uniform(100, 500, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def small_cfg() -> dict:
    cfg = copy.deepcopy(load_config())
    cfg["vol_long_window"] = 20
    cfg["vol_window"] = 10
    cfg["sma_windows"] = [5, 10]
    cfg["ema_windows"] = [4, 8]
    cfg["range_windows"] = [10]
    cfg["rsi_period"] = 7
    cfg["atr_period"] = 5
    cfg["horizon"] = 8
    cfg["walk_forward"] = {"train_bars": 80, "test_bars": 30, "step_bars": 30, "min_train_bars": 40}
    cfg["model"]["type"] = "logistic"
    cfg["model"]["compare_logistic"] = False
    cfg["model"]["logistic"]["max_iter"] = 200
    cfg["signals"]["min_confidence"] = 0.0
    cfg["gates"]["enabled"] = False
    return cfg


def test_production_source_has_no_purge_and_slices_at_the_test_start():
    flags = production_source_flags()
    assert flags["slices_train_up_to_test_start"] is True
    assert flags["uses_holdout_train_mask"] is True
    assert flags["mentions_purge_bars"] is False
    assert flags["mentions_embargo"] is False


def test_label_scan_matches_the_eight_bar_forward_window():
    # t reads t+1 .. t+horizon inclusive. The last of those is the ready bar.
    assert label_scan(10, 8, 100) == (11, 18)
    assert label_scan(95, 8, 100) is None  # t+horizon is off the end


def test_contiguous_fold_trains_the_last_horizon_labels_into_the_test():
    idx = _idx()
    trainable = np.ones(len(idx), dtype=bool)
    traced = trace_folds(idx, idx, trainable, horizon=8, cfg=_wf(), cutoff=None)
    assert traced["training_labels_overlap_test"] is True
    fold0 = traced["folds"][0]
    assert fold0["test_iloc"] == [100, 140]
    assert fold0["test_first_bar"] == 100
    assert fold0["n_overlap_trained"] == 8
    assert fold0["n_overlap_removed_by_holdout_mask"] == 0
    rows = traced["example_fold"]["rows"]
    assert [r["bar_loc"] for r in rows] == list(range(92, 100))
    assert all(r["label_last_bar"] == r["bar_loc"] + 8 for r in rows)
    assert rows[0]["label_last_bar"] == 100  # first overlapping label ends on the first test bar
    assert rows[-1]["label_last_bar"] == 107


def test_holdout_mask_removes_overlap_only_at_the_final_cutoff():
    idx = _idx()
    cutoff = idx[250]
    trainable = train_mask(idx, cutoff=cutoff, horizon=8, bar_index=idx).to_numpy()
    traced = trace_folds(
        idx,
        idx,
        trainable,
        horizon=8,
        cfg=_wf(train=100, test=50, step=50, min_train=50),
        cutoff=cutoff,
    )
    by_start = {f["test_iloc"][0]: f for f in traced["folds"]}
    interior = by_start[100]
    assert interior["n_overlap_trained"] == 8
    assert interior["n_overlap_removed_by_holdout_mask"] == 0
    at_cut = by_start[250]
    assert at_cut["n_overlap_in_train_window"] == 8
    assert at_cut["n_overlap_removed_by_holdout_mask"] == 8
    assert at_cut["n_overlap_trained"] == 0
    assert traced["training_labels_overlap_test"] is True


def test_purge_and_embargo_drop_two_horizons_and_leave_no_overlap():
    locs = np.arange(300)
    kept, n_purge, n_embargo, still = purged_train_positions(
        np.arange(0, 100),
        locs,
        horizon=8,
        n_bars=300,
        test_locs=np.arange(100, 140),
        embargo_bars=8,
    )
    assert n_purge == 8
    assert n_embargo == 8
    assert still == 0
    assert list(kept) == list(range(0, 84))


def test_short_sample_walk_forward_matches_the_production_rule():
    train, test, step, min_train = resolve_wf(100, {"walk_forward": {"train_bars": 2000, "test_bars": 250, "step_bars": 250, "min_train_bars": 500}})
    assert (train, test, step, min_train) == (500, 50, 50, 500)
    assert fold_bounds(100, train, test, step, min_train) == []


def test_block_bootstrap_is_deterministic_and_brackets_the_mean():
    rng = np.random.default_rng(0)
    flat = block_bootstrap_ci(np.ones(20), block=5, n_boot=30, rng=rng)
    assert flat["mean"] == 1.0
    assert flat["ci_lo"] == 1.0 and flat["ci_hi"] == 1.0
    values = np.arange(40, dtype=float)
    rng_a = np.random.default_rng(7)
    rng_b = np.random.default_rng(7)
    a = block_bootstrap_ci(values, block=4, n_boot=80, rng=rng_a)
    b = block_bootstrap_ci(values, block=4, n_boot=80, rng=rng_b)
    assert a == b
    assert a["ci_lo"] <= a["mean"] <= a["ci_hi"]


def test_resimulated_sides_keep_decision_times_and_flip_is_opposite():
    df = synthetic_ohlc(80, seed=1)
    cfg = small_cfg()
    decisions = pd.Series(df.index[20:23])
    out = resimulate_sides(df, decisions, np.array(["BUY", "SELL", "BUY"]), cfg, "EURUSD")
    got = pd.DatetimeIndex(pd.to_datetime(out["decision_time"]))
    assert list(got) == list(df.index[20:23])
    flipped = resimulate_sides(df, decisions, np.array(["SELL", "BUY", "SELL"]), cfg, "EURUSD")
    assert list(flipped["side"]) == ["SELL", "BUY", "SELL"]
    assert list(out["side"]) == ["BUY", "SELL", "BUY"]


def test_calibration_deciles_and_exit_buckets():
    idx = pd.date_range("2024-06-04", periods=20, freq="h")
    conf = np.linspace(0.61, 0.95, 20)
    r = np.linspace(-1.0, 1.0, 20)
    trades = pd.DataFrame(
        {
            "decision_time": idx,
            "entry_time": idx,
            "confidence": conf,
            "r_net": r,
            "r_raw": r + 0.05,
            "net_return": r * 0.001,
            "exit_reason": ["sl"] * 7 + ["tp"] * 7 + ["timeout"] * 5 + ["sl_conflict"],
        }
    )
    # sl_conflict is the 21st if I miscount. Keep 20 rows: 7 sl, 7 tp, 5 timeout, 1 sl_conflict = 20.
    trades = trades.iloc[:20]
    preds = pd.DataFrame({"confidence": conf}, index=idx)
    # trades already carry confidence; calibration prefers the prediction frame.
    trades = trades.drop(columns=["confidence"])
    out = calibration(trades, preds)
    assert out["n_with_confidence"] == 20
    assert len(out["deciles"]) >= 2
    assert out["deciles"][0]["conf_max"] <= out["deciles"][-1]["conf_min"] or out["deciles"][0]["mean_confidence"] < out["deciles"][-1]["mean_confidence"]
    exits = {row["exit"]: row["n"] for row in out["by_exit"]}
    assert exits["SL"] == 8  # 7 sl + 1 sl_conflict
    assert exits["TP"] == 7
    assert exits["time"] == 5
    assert sum(row["n"] for row in out["by_exit"]) == 20


def test_same_bar_stop_and_target_counts_the_exit_bar_only():
    idx = pd.date_range("2024-06-04", periods=40, freq="h")
    close = np.full(40, 1.10)
    open_ = close.copy()
    high = close + 0.0002
    low = close - 0.0002
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": 100.0}, index=idx)
    cfg = {"horizon": 8, "atr_period": 5, "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"}}
    atr = float(true_range_atr(df, 5).loc[idx[20]])
    entry = 1.10
    width = 2.0 * atr + 0.0001
    df.loc[idx[21], "High"] = entry + width
    df.loc[idx[21], "Low"] = entry - width
    both = pd.DataFrame(
        [{
            "entry_time": idx[21],
            "decision_time": idx[20],
            "side": "BUY",
            "entry": entry,
            "exit_reason": "sl_conflict",
        }]
    )
    hit = same_bar_sl_tp(df, both, cfg)
    assert hit["trades_affected"] == 1
    assert hit["ambiguous_bars"] == 1
    assert hit["share_of_trades"] == 1.0
    assert hit["sl_conflict_matches_scan"] is True

    df.loc[idx[21], "High"] = entry + 0.00005  # above the open, short of the target
    clean = both.copy()
    clean["exit_reason"] = "sl"
    miss = same_bar_sl_tp(df, clean, cfg)
    assert miss["trades_affected"] == 0
    assert miss["ambiguous_bars"] == 0
    assert miss["simulator_sl_conflict"] == 0


def test_cost_versus_move_by_session_and_fresh_holdout_is_excluded():
    idx = pd.date_range("2024-06-04", periods=48, freq="h")
    close = 1.10 + np.arange(48) * 0.0010
    open_ = np.empty(48)
    open_[0] = close[0]
    open_[1:] = close[:-1]
    df = pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.0002,
            "Low": np.minimum(open_, close) - 0.0002,
            "Close": close,
            "Volume": 100.0,
        },
        index=idx,
    )
    cfg = load_config()
    cfg = copy.deepcopy(cfg)
    table = cost_vs_move(df, cfg, "EURUSD", 1.4)
    all_row = next(r for r in table["by_session"] if r["session"] == "ALL")
    assert all_row["median_abs_pips"] == pytest.approx(80.0)
    assert all_row["cost_over_median"] == pytest.approx(1.4 / 80.0)
    sessions = {r["session"] for r in table["by_session"]}
    assert {"london", "ny", "london+ny", "asia"} <= sessions

    tail_idx = pd.date_range("2026-11-02", periods=12, freq="h")
    tail_close = close[-1] + 5.0 + np.arange(12)  # hundreds of pips, must not enter the median
    tail_open = np.empty(12)
    tail_open[0] = close[-1]
    tail_open[1:] = tail_close[:-1]
    tail = pd.DataFrame(
        {
            "Open": tail_open,
            "High": np.maximum(tail_open, tail_close) + 0.0002,
            "Low": np.minimum(tail_open, tail_close) - 0.0002,
            "Close": tail_close,
            "Volume": 100.0,
        },
        index=tail_idx,
    )
    both = pd.concat([df, tail])
    kept, info = split_fresh_holdout(both, FRESH_HOLDOUT_START)
    assert info["n_excluded"] == 12
    assert info["n_kept"] == 48
    kept_table = cost_vs_move(kept, cfg, "EURUSD", 1.4)
    kept_all = next(r for r in kept_table["by_session"] if r["session"] == "ALL")
    assert kept_all["median_abs_pips"] == pytest.approx(80.0)


def test_entry_windows_follow_the_published_and_fresh_cuts():
    entries = [
        "2025-09-30 12:00:00",
        "2025-10-01 00:00:00",
        "2026-10-12 18:00:00",
        "2026-10-12 19:00:00",
        "2026-11-01 00:00:00",
    ]
    trades = pd.DataFrame({"entry_time": entries, "net_return": [0.1, 0.1, 0.1, 0.1, 0.1]})
    from research.phase0.common import PRIOR_HOLDOUT_START, PUBLISHED_SCORE_LAST_BAR

    before = trade_window_mask(trades, None, PRIOR_HOLDOUT_START)
    published = trade_window_mask(trades, PRIOR_HOLDOUT_START, PUBLISHED_SCORE_LAST_BAR + pd.Timedelta(hours=1))
    tail = trade_window_mask(trades, PUBLISHED_SCORE_LAST_BAR + pd.Timedelta(hours=1), FRESH_HOLDOUT_START)
    fresh = trade_window_mask(trades, FRESH_HOLDOUT_START, None)
    assert list(before) == [True, False, False, False, False]
    assert list(published) == [False, True, True, False, False]
    assert list(tail) == [False, False, False, True, False]
    assert list(fresh) == [False, False, False, False, True]


def test_declared_costs_match_the_measured_table():
    assert DECLARED_RT_PIPS["EURUSD"] == 1.4
    assert DECLARED_RT_PIPS["USDJPY"] == 2.4
    for pair, pips in DECLARED_RT_PIPS.items():
        got = judged_rt_pips(pair)
        assert got["rt_pips"] == pips
        assert got["table_matches_declared"] is True


def test_prereg_freezes_november_2026_and_readme_has_the_windows_command():
    root = project_root()
    prereg = (root / "research" / "phase0" / "PREREG.md").read_text(encoding="utf-8")
    readme = (root / "research" / "phase0" / "README.md").read_text(encoding="utf-8")
    assert "2026-11-01" in prereg
    assert "2026-11-01T00:00:00" in prereg or "2026-11-01 00:00" in prereg
    assert "no Phase 0" in prereg
    assert "2025-10-01" in prereg
    assert "2026-10-12" in prereg
    assert str(FRESH_HOLDOUT_START)[:10] == "2026-11-01"
    assert r".venv\Scripts\python.exe -m research.phase0.run --pairs EURUSD USDJPY" in readme


def test_production_fit_overlaps_and_purged_fit_does_not(monkeypatch):
    import forex_lab.backtest as bt
    import research.phase0.leakage as leak

    df = synthetic_ohlc(360, seed=2)
    cfg = small_cfg()
    real = bt.fit_predict_bundle
    seen = {"prod": [], "purged": []}

    def _grab(bucket):
        def spy(cfg, x_tr, y_tr, x_te, **kw):
            seen[bucket].append((pd.DatetimeIndex(x_tr.index).copy(), pd.DatetimeIndex(x_te.index).copy()))
            return real(cfg, x_tr, y_tr, x_te, **kw)

        return spy

    monkeypatch.setattr(bt, "fit_predict_bundle", _grab("prod"))
    monkeypatch.setattr(leak, "fit_predict_bundle", _grab("purged"))
    unpurged = unpurged_walk_forward(df, cfg, "EURUSD", model_type="logistic")
    purged = purged_walk_forward(df, cfg, "EURUSD", model_type="logistic", embargo_bars=8)
    assert seen["prod"] and seen["purged"]
    horizon = 8
    for train_idx, test_idx in seen["prod"]:
        train_locs = [int(df.index.get_loc(ts)) for ts in train_idx]
        test_first = min(int(df.index.get_loc(ts)) for ts in test_idx)
        # The last trained label reads at least the first test bar.
        assert max(train_locs) + horizon >= test_first
    for train_idx, test_idx in seen["purged"]:
        train_locs = [int(df.index.get_loc(ts)) for ts in train_idx]
        test_locs = [int(df.index.get_loc(ts)) for ts in test_idx]
        test_first = min(test_locs)
        test_last = max(test_locs)
        for loc in train_locs:
            span = label_scan(loc, horizon, len(df))
            assert span is not None
            assert not (span[0] <= test_last and span[1] >= test_first)
            assert span[1] < test_first - 8
    assert purged["overlap_rows_after_purge"] == 0
    assert purged["folds_fit"] >= 1
    assert unpurged["folds"] >= 1
    assert unpurged["production_pf_matches_rescore"] is True
    for arm in (unpurged, purged):
        assert "profit_factor" in arm["net_return"]
        assert "expectancy" in arm["r_net"]
        assert "expectancy" in arm["r_raw"]


def test_cli_writes_results_outside_data_and_leaves_yaml_untouched(tmp_path):
    yaml_path = project_root() / "config" / "default.yaml"
    before = yaml_path.read_bytes()
    hist = tmp_path / "history"
    hist.mkdir()
    df = synthetic_ohlc(900, seed=3)
    tail_idx = pd.date_range("2026-11-02", periods=6, freq="h")
    tail = pd.DataFrame(
        {
            "Open": 1.1,
            "High": 1.101,
            "Low": 1.099,
            "Close": 1.1,
            "Volume": 10.0,
        },
        index=tail_idx,
    )
    pd.concat([df, tail]).to_csv(hist / "EURUSD_1h.csv")
    out = tmp_path / "out"
    code = main(
        [
            "--pairs",
            "EURUSD",
            "--history-dir",
            str(hist),
            "--out-dir",
            str(out),
            "--model-type",
            "logistic",
            "--seeds",
            "2",
            "--n-boot",
            "8",
            "--block",
            "4",
            "--date",
            "20261107",
        ]
    )
    assert yaml_path.read_bytes() == before
    md = out / "RESULTS_20261107.md"
    js = out / "RESULTS_20261107.json"
    assert md.exists() and js.exists()
    payload = json.loads(js.read_text(encoding="utf-8"))
    body = payload["pairs"]["EURUSD"]
    assert code == 0, body.get("error")
    assert body["fresh_holdout_exclusion"]["n_excluded"] == 6
    assert body["leakage"]["training_labels_overlap_test"] is True
    assert body["purge_comparison"]["overlap_rows_after_purge"] == 0
    text = md.read_text(encoding="utf-8")
    assert "Yes." in text
    assert "2026-11-01" in text
    written = list(out.rglob("*"))
    assert {p.name for p in written} == {"RESULTS_20261107.md", "RESULTS_20261107.json"}


def test_missing_history_is_an_error_and_output_under_data_is_refused(tmp_path):
    from research.phase0.common import assert_output_dir

    with pytest.raises(SystemExit):
        assert_output_dir(project_root() / "data" / "phase0")
    out = tmp_path / "out"
    code = main(
        [
            "--pairs",
            "USDJPY",
            "--history-dir",
            str(tmp_path / "empty"),
            "--out-dir",
            str(out),
            "--date",
            "20261107",
            "--model-type",
            "logistic",
            "--seeds",
            "1",
            "--n-boot",
            "1",
        ]
    )
    assert code == 1
    payload = json.loads((out / "RESULTS_20261107.json").read_text(encoding="utf-8"))
    assert "missing history" in payload["pairs"]["USDJPY"]["error"]
    assert "does not synthesize" in payload["pairs"]["USDJPY"]["error"]
