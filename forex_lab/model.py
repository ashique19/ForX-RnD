"""Model training: XGBoost classifier + logistic baseline."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from forex_lab.features import INV_LABEL_MAP, make_dataset
from forex_lab.paths import resolve_under_root


def _xgboost_clf(cfg: dict[str, Any]):
    from xgboost import XGBClassifier

    xcfg = (cfg.get("model") or {}).get("xgboost") or {}
    return XGBClassifier(
        n_estimators=int(xcfg.get("n_estimators", 120)),
        max_depth=int(xcfg.get("max_depth", 4)),
        learning_rate=float(xcfg.get("learning_rate", 0.08)),
        subsample=float(xcfg.get("subsample", 0.9)),
        colsample_bytree=float(xcfg.get("colsample_bytree", 0.9)),
        objective="multi:softprob",
        num_class=3,
        eval_metric="mlogloss",
        random_state=int((cfg.get("model") or {}).get("random_state", 42)),
        n_jobs=2,
        tree_method="hist",
    )


def _logistic_clf(cfg: dict[str, Any]):
    lcfg = (cfg.get("model") or {}).get("logistic") or {}
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    max_iter=int(lcfg.get("max_iter", 500)),
                    C=float(lcfg.get("C", 1.0)),
                    random_state=int((cfg.get("model") or {}).get("random_state", 42)),
                ),
            ),
        ]
    )


def build_model(cfg: dict[str, Any], model_type: str | None = None):
    mtype = (model_type or (cfg.get("model") or {}).get("type") or "xgboost").lower()
    if mtype == "logistic":
        return _logistic_clf(cfg), "logistic"
    return _xgboost_clf(cfg), "xgboost"


def model_dir(cfg: dict[str, Any]) -> Path:
    d = resolve_under_root(cfg.get("paths", {}).get("models_dir", "models"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_paths(pair: str, cfg: dict[str, Any], model_type: str) -> dict[str, Path]:
    base = model_dir(cfg) / f"{pair.upper()}_{model_type}"
    return {
        "model": Path(str(base) + ".joblib"),
        "meta": Path(str(base) + "_meta.json"),
    }


def train_models(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
) -> dict[str, Any]:
    """Train XGBoost (primary) and logistic (baseline comparison) on time-ordered split."""
    X, y, _ = make_dataset(df, cfg)
    if len(X) < 200:
        raise RuntimeError(f"Not enough rows to train ({len(X)})")

    split = int(len(X) * 0.8)
    X_train, X_val = X.iloc[:split], X.iloc[split:]
    y_train, y_val = y.iloc[:split], y.iloc[split:]

    results: dict[str, Any] = {"pair": pair.upper(), "n_train": len(X_train), "n_val": len(X_val)}

    for mtype in ("xgboost", "logistic"):
        model, name = build_model(cfg, mtype)
        model.fit(X_train, y_train)
        pred = model.predict(X_val)
        acc = float(accuracy_score(y_val, pred))
        report = classification_report(
            y_val, pred, target_names=["SELL", "HOLD", "BUY"], zero_division=0, output_dict=True
        )
        paths = model_paths(pair, cfg, name)
        joblib.dump({"model": model, "features": list(X.columns)}, paths["model"])
        meta = {
            "pair": pair.upper(),
            "model_type": name,
            "features": list(X.columns),
            "val_accuracy": acc,
            "classification_report": report,
            "label_map": INV_LABEL_MAP,
            "horizon": cfg.get("horizon"),
            "label_threshold": cfg.get("label_threshold"),
        }
        paths["meta"].write_text(json.dumps(meta, indent=2), encoding="utf-8")
        results[name] = {"val_accuracy": acc, "path": str(paths["model"]), "report": report}
        print(f"[train] {name}: val_accuracy={acc:.4f} -> {paths['model']}")

    return results


def load_model(pair: str, cfg: dict[str, Any], model_type: str | None = None):
    mtype = (model_type or (cfg.get("model") or {}).get("type") or "xgboost").lower()
    paths = model_paths(pair, cfg, mtype)
    if not paths["model"].exists():
        raise FileNotFoundError(f"No model at {paths['model']}. Run train first.")
    bundle = joblib.load(paths["model"])
    return bundle["model"], bundle["features"], mtype
