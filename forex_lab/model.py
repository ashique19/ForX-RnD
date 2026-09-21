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
from sklearn.utils.class_weight import compute_sample_weight

from forex_lab.features import INV_LABEL_MAP, LABEL_MAP, label_distribution, make_dataset
from forex_lab.paths import resolve_under_root


def _xgboost_clf(cfg: dict[str, Any]):
    from xgboost import XGBClassifier

    xcfg = (cfg.get("model") or {}).get("xgboost") or {}
    return XGBClassifier(
        n_estimators=int(xcfg.get("n_estimators", 160)),
        max_depth=int(xcfg.get("max_depth", 3)),
        learning_rate=float(xcfg.get("learning_rate", 0.05)),
        subsample=float(xcfg.get("subsample", 0.8)),
        colsample_bytree=float(xcfg.get("colsample_bytree", 0.8)),
        min_child_weight=float(xcfg.get("min_child_weight", 10)),
        reg_lambda=float(xcfg.get("reg_lambda", 1.5)),
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


def fit_model(model, X: pd.DataFrame, y: pd.Series, *, balanced: bool = True) -> None:
    """Fit with optional balanced sample weights (all three classes)."""
    sw = None
    if balanced and len(y):
        sw = compute_sample_weight("balanced", y)
    if sw is None:
        model.fit(X, y)
        return
    if isinstance(model, Pipeline):
        model.fit(X, y, clf__sample_weight=sw)
    else:
        model.fit(X, y, sample_weight=sw)


def _model_classes(model) -> list[int]:
    if hasattr(model, "classes_") and model.classes_ is not None:
        return [int(c) for c in model.classes_]
    if isinstance(model, Pipeline):
        clf = model.named_steps.get("clf")
        if clf is not None and hasattr(clf, "classes_"):
            return [int(c) for c in clf.classes_]
    return [0, 1, 2]


def predict_proba_aligned(model, X: pd.DataFrame) -> np.ndarray | None:
    """Return (n, 3) array in LABEL_MAP column order, or None."""
    if not hasattr(model, "predict_proba"):
        return None
    proba = np.asarray(model.predict_proba(X))
    classes = _model_classes(model)
    out = np.zeros((len(X), 3), dtype=float)
    for j, c in enumerate(classes):
        if 0 <= int(c) <= 2:
            out[:, int(c)] = proba[:, j]
    return out


def apply_signal_filters(pred_frame: pd.DataFrame, cfg: dict[str, Any]) -> pd.Series:
    """Force low-confidence / low-edge directional calls to HOLD."""
    sig_cfg = cfg.get("signals") or {}
    min_conf = float(sig_cfg.get("min_confidence", 0.45))
    min_edge = float(sig_cfg.get("min_dir_edge", 0.08))
    raw = (pred_frame["pred_raw"] if "pred_raw" in pred_frame.columns else pred_frame["pred"]).astype(int)
    keep = raw != LABEL_MAP["HOLD"]
    if "confidence" in pred_frame.columns:
        keep = keep & (pred_frame["confidence"] >= min_conf)
    if "dir_edge" in pred_frame.columns:
        keep = keep & (pred_frame["dir_edge"] >= min_edge)
    return raw.where(keep, LABEL_MAP["HOLD"]).astype(int)


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
    balanced = bool((cfg.get("model") or {}).get("class_weight_balanced", True))

    results: dict[str, Any] = {
        "pair": pair.upper(),
        "n_train": len(X_train),
        "n_val": len(X_val),
        "label_scheme": cfg.get("label_scheme", "triple_barrier"),
        "label_distribution": label_distribution(y),
        "features": list(X.columns),
    }

    for mtype in ("xgboost", "logistic"):
        model, name = build_model(cfg, mtype)
        fit_model(model, X_train, y_train, balanced=balanced)
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
            "label_scheme": cfg.get("label_scheme", "triple_barrier"),
            "horizon": cfg.get("horizon"),
            "label_threshold": cfg.get("label_threshold"),
            "barrier": cfg.get("barrier"),
            "entry_timing": cfg.get("entry_timing", "next_open"),
            "label_distribution": label_distribution(y),
        }
        paths["meta"].write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
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
