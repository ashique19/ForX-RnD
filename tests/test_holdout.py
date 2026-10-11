from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from forex_lab import holdout as ho


@pytest.fixture
def cutoff_file(tmp_path, monkeypatch):
    p = tmp_path / "holdout.json"
    p.write_text(json.dumps({"cutoff_utc": "2025-10-01T00:00:00"}), encoding="utf-8")
    monkeypatch.setenv(ho.ENV, str(p))
    return p


def test_repo_holdout_file_is_valid():
    data = json.loads(ho.DEFAULT_FILE.read_text(encoding="utf-8"))
    assert pd.Timestamp(data["cutoff_utc"]) <= pd.Timestamp("2025-10-09")


def test_train_mask_excludes_rows_and_unsettled_labels(cutoff_file):
    idx = pd.date_range("2025-09-30 12:00", periods=24, freq="1h")
    m = ho.train_mask(idx, horizon=8)
    # label of row t settles at t+8h; must be < cutoff (2025-10-01 00:00)
    assert m[pd.Timestamp("2025-09-30 15:00")]
    assert not m[pd.Timestamp("2025-09-30 16:00")]
    assert not m[idx >= pd.Timestamp("2025-10-01")].any()


def test_disabled_and_assert(monkeypatch, cutoff_file):
    idx = pd.date_range("2025-09-30", periods=48, freq="1h")
    with pytest.raises(ho.HoldoutViolation):
        ho.assert_no_holdout(idx)
    monkeypatch.setenv(ho.ENV, "off")
    assert ho.load_cutoff() is None and ho.train_mask(idx).all()


def test_split_report(cutoff_file):
    t = pd.DataFrame({"entry_time": ["2025-09-01 10:00:00 UTC", "2025-09-02 10:00:00 UTC", "2025-10-02 10:00:00 UTC"],
                      "ret": [0.01, -0.005, -0.002]})
    rep = ho.split_report(t, time_col="entry_time", ret_col="ret")
    assert rep["in_sample"]["n"] == 2 and rep["in_sample"]["profit_factor"] == pytest.approx(2.0)
    assert rep["holdout"]["n"] == 1 and rep["holdout"]["max_drawdown_additive"] == pytest.approx(-0.002)


def test_walk_forward_never_trains_on_holdout(cutoff_file, monkeypatch):
    import forex_lab.backtest as bt

    seen = []
    real = bt.fit_predict_bundle

    def spy(cfg, X_tr, y_tr, X_te, **kw):
        seen.append(pd.DatetimeIndex(X_tr.index).max())
        return real(cfg, X_tr, y_tr, X_te, **kw)

    monkeypatch.setattr(bt, "fit_predict_bundle", spy)
    idx = pd.date_range("2025-06-01", periods=3200, freq="1h")
    rng = np.random.default_rng(0)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0005, len(idx)))
    open_ = np.empty(len(idx))
    open_[0] = close[0]
    open_[1:] = close[:-1]
    df = pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) + 0.0006,
            "Low": np.minimum(open_, close) - 0.0006,
            "Close": close,
            "Volume": 1000.0,
        },
        index=idx,
    )
    from forex_lab.config_loader import load_config

    cfg = load_config()
    cfg["model"] = {**(cfg.get("model") or {}), "type": "logistic", "compare_logistic": False}
    result, trades, _ = bt.walk_forward_backtest(df, cfg, "EURUSD", model_type="logistic")
    assert seen and max(seen) < pd.Timestamp("2025-10-01")
    assert result["holdout"]["cutoff_utc"].startswith("2025-10-01")
