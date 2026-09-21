"""Generate latest BUY/SELL/HOLD signals CSV (manual trade trigger only)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from forex_lab.features import INV_LABEL_MAP, build_features
from forex_lab.model import load_model
from forex_lab.paths import resolve_under_root


def generate_signals(df: pd.DataFrame, cfg: dict[str, Any], pair: str) -> pd.DataFrame:
    model, feature_cols, mtype = load_model(pair, cfg)
    feats = build_features(df, cfg)
    # Drop rows with NaN features (warm-up)
    valid = feats.dropna()
    X = valid[feature_cols]
    pred = model.predict(X)
    conf = None
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        conf = proba.max(axis=1)
        # Map classes — sklearn/xgb use 0,1,2
        class_ids = list(getattr(model, "classes_", [0, 1, 2]))
    else:
        class_ids = [0, 1, 2]

    min_conf = float((cfg.get("signals") or {}).get("min_confidence", 0.40))
    lookback = int((cfg.get("signals") or {}).get("lookback_bars", 64))

    rows = []
    for i, ts in enumerate(X.index):
        label_id = int(pred[i])
        name = INV_LABEL_MAP.get(label_id, "HOLD")
        c = float(conf[i]) if conf is not None else 1.0
        if name != "HOLD" and c < min_conf:
            name = "HOLD"
        rows.append(
            {
                "datetime": ts,
                "pair": pair.upper(),
                "close": float(df.loc[ts, "Close"]),
                "signal": name,
                "confidence": round(c, 4),
                "model": mtype,
            }
        )
    out = pd.DataFrame(rows).tail(lookback)
    # Highlight actionable last row
    return out


def write_signals(signals: pd.DataFrame, cfg: dict[str, Any]) -> str:
    sig_dir = resolve_under_root(cfg.get("paths", {}).get("signals_dir", "signals"))
    sig_dir.mkdir(parents=True, exist_ok=True)
    path = sig_dir / "latest_signals.csv"
    signals.to_csv(path, index=False)
    print(f"[signals] wrote {path} ({len(signals)} rows)")
    if len(signals):
        last = signals.iloc[-1]
        print(
            f"[signals] latest: {last['datetime']} {last['pair']} "
            f"{last['signal']} conf={last['confidence']} close={last['close']}"
        )
    return str(path)
