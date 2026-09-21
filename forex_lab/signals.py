"""Generate latest BUY/SELL/HOLD signals CSV (manual trade trigger only)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from forex_lab.backtest import _attach_policy_columns
from forex_lab.console import safe_print
from forex_lab.features import INV_LABEL_MAP, LABEL_MAP, build_features
from forex_lab.model import apply_signal_filters, load_model, predict_proba_aligned
from forex_lab.paths import resolve_under_root


def generate_signals(df: pd.DataFrame, cfg: dict[str, Any], pair: str) -> pd.DataFrame:
    model, feature_cols, mtype = load_model(pair, cfg)
    feats = build_features(df, cfg, pair=pair)
    missing = [c for c in feature_cols if c not in feats.columns]
    if missing:
        raise KeyError(f"Model features missing from current feature set: {missing[:8]}")
    X = feats[feature_cols].dropna()
    pred = model.predict(X)
    proba = predict_proba_aligned(model, X)

    pred_frame = pd.DataFrame({"pred_raw": pred}, index=X.index)
    if proba is not None:
        pred_frame["p_sell"] = proba[:, LABEL_MAP["SELL"]]
        pred_frame["p_hold"] = proba[:, LABEL_MAP["HOLD"]]
        pred_frame["p_buy"] = proba[:, LABEL_MAP["BUY"]]
        pred_frame["confidence"] = proba.max(axis=1)
        pred_frame["dir_edge"] = (pred_frame["p_buy"] - pred_frame["p_sell"]).abs()
    pred_frame = _attach_policy_columns(pred_frame, feats, df, cfg, pair)
    pred_frame["pred"] = apply_signal_filters(pred_frame, cfg)

    lookback = int((cfg.get("signals") or {}).get("lookback_bars", 64))
    rows = []
    for ts, row in pred_frame.iterrows():
        label_id = int(row["pred"])
        raw_id = int(row["pred_raw"])
        name = INV_LABEL_MAP.get(label_id, "HOLD")
        rows.append(
            {
                "datetime": ts,
                "pair": pair.upper(),
                "close": float(df.loc[ts, "Close"]),
                "signal": name,
                "raw_signal": INV_LABEL_MAP.get(raw_id, "HOLD"),
                "confidence": round(float(row["confidence"]) if "confidence" in row else 1.0, 4),
                "dir_edge": round(float(row["dir_edge"]) if "dir_edge" in row else 0.0, 4),
                "p_buy": round(float(row["p_buy"]) if "p_buy" in row else float("nan"), 4),
                "p_sell": round(float(row["p_sell"]) if "p_sell" in row else float("nan"), 4),
                "p_hold": round(float(row["p_hold"]) if "p_hold" in row else float("nan"), 4),
                "model": mtype,
            }
        )
    return pd.DataFrame(rows).tail(lookback)


def write_signals(signals: pd.DataFrame, cfg: dict[str, Any]) -> str:
    sig_dir = resolve_under_root(cfg.get("paths", {}).get("signals_dir", "signals"))
    sig_dir.mkdir(parents=True, exist_ok=True)
    path = sig_dir / "latest_signals.csv"
    signals.to_csv(path, index=False)
    safe_print(f"[signals] wrote {path} ({len(signals)} rows)")
    if len(signals):
        last = signals.iloc[-1]
        safe_print(
            f"[signals] latest: {last['datetime']} {last['pair']} "
            f"{last['signal']} conf={last['confidence']} close={last['close']}"
        )
    return str(path)
