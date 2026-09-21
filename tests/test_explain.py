"""Explanation helpers: rules, narrative, local drivers."""
from __future__ import annotations

import pandas as pd

from forex_lab.data import generate_synthetic_ohlcv
from forex_lab.explain import (
    Driver,
    RuleCheck,
    evaluate_signal_rules,
    grounded_narrative,
    local_drivers,
)
from forex_lab.features import LABEL_MAP, make_dataset
from forex_lab.model import apply_signal_filters


def test_evaluate_rules_confidence_floor():
    cfg = {"signals": {"min_confidence": 0.45, "min_dir_edge": 0.0, "sessions": []}}
    row = pd.Series({"confidence": 0.30, "dir_edge": 0.20, "p_buy": 0.3, "p_sell": 0.2, "p_hold": 0.5})
    rules = evaluate_signal_rules(row, cfg, LABEL_MAP["BUY"])
    conf = next(r for r in rules if r.name == "min_confidence")
    assert conf.enabled and conf.passed is False
    sess = next(r for r in rules if r.name == "sessions")
    assert sess.enabled is False


def test_evaluate_rules_match_apply_signal_filters():
    cfg = {"signals": {"min_confidence": 0.45, "min_dir_edge": 0.08, "sessions": []}}
    frame = pd.DataFrame(
        {
            "pred_raw": [2, 2, 0, 1],
            "confidence": [0.70, 0.40, 0.60, 0.90],
            "dir_edge": [0.20, 0.20, 0.02, 0.00],
        }
    )
    filtered = apply_signal_filters(frame, cfg)
    for i, raw in enumerate(frame["pred_raw"]):
        rules = evaluate_signal_rules(frame.iloc[i], cfg, int(raw))
        blocked = any(r.enabled and r.passed is False for r in rules)
        if int(raw) == LABEL_MAP["HOLD"]:
            assert int(filtered.iloc[i]) == LABEL_MAP["HOLD"]
        elif blocked:
            assert int(filtered.iloc[i]) == LABEL_MAP["HOLD"]
        else:
            assert int(filtered.iloc[i]) == int(raw)


def test_narrative_is_grounded_and_not_a_sales_pitch():
    text = grounded_narrative(
        pair="EURUSD",
        signal="SELL",
        raw_signal="SELL",
        drivers=[Driver("rsi", -0.08, 0.4, "RSI")],
        rules=[RuleCheck("min_confidence", True, True, "conf=0.65 vs min 0.40")],
        target="TP 1.14 / SL 1.15",
        method="pred_contribs",
        cfg={"explain": {"ollama": False}},
    )
    low = text.lower()
    assert "eurusd" in low and "sell" in low
    assert "rsi" in low
    assert "min_confidence" in low
    assert "not evidence" in low or "not a broker" in low
    assert "guaranteed profit" not in low
    assert "ollama" not in low


def test_local_drivers_tiny_xgboost():
    from xgboost import XGBClassifier

    df = generate_synthetic_ohlcv(bars=350, seed=4)
    cfg = {
        "sma_windows": [10, 20],
        "ema_windows": [12, 26],
        "rsi_period": 14,
        "atr_period": 14,
        "vol_window": 20,
        "vol_long_window": 50,
        "range_windows": [20],
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "entry_timing": "next_open",
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
        "feature_extras": {"higher_tf": [], "session_overlap": False, "vol_percentile_window": 0},
    }
    X, y, _ = make_dataset(df, cfg)
    model = XGBClassifier(
        n_estimators=8,
        max_depth=2,
        objective="multi:softprob",
        num_class=3,
        n_jobs=1,
        verbosity=0,
    )
    model.fit(X.iloc[:180], y.iloc[:180])
    drivers, method = local_drivers(model, X.iloc[[-1]], list(X.columns), int(y.iloc[-1]), top_n=5)
    assert method in {"pred_contribs", "shap", "importance", "linear"}
    assert drivers
    assert all(d.feature for d in drivers)
