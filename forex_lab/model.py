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

from forex_lab.console import safe_print
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
    """Force low-confidence / filtered directional calls to HOLD."""
    sig_cfg = cfg.get("signals") or {}
    min_conf = float(sig_cfg.get("min_confidence", 0.40))
    min_edge = float(sig_cfg.get("min_dir_edge", 0.0))
    raw = (pred_frame["pred_raw"] if "pred_raw" in pred_frame.columns else pred_frame["pred"]).astype(int)
    keep = raw != LABEL_MAP["HOLD"]
    if "confidence" in pred_frame.columns:
        keep = keep & (pred_frame["confidence"] >= min_conf)
    if "dir_edge" in pred_frame.columns:
        keep = keep & (pred_frame["dir_edge"] >= min_edge)
    sessions = sig_cfg.get("sessions") or []
    if sessions:
        sess_keep = pd.Series(False, index=pred_frame.index)
        have_col = False
        for name in sessions:
            col = f"sess_{str(name).strip().lower()}"
            if col in pred_frame.columns:
                have_col = True
                sess_keep = sess_keep | (pred_frame[col].astype(float) > 0.5)
        if have_col:
            keep = keep & sess_keep
    min_vol = float(sig_cfg.get("min_vol_regime", 0.0) or 0.0)
    if min_vol > 0 and "vol_regime" in pred_frame.columns:
        keep = keep & (pred_frame["vol_regime"].astype(float) >= min_vol)
    min_tp = float(sig_cfg.get("min_tp_pips", 0.0) or 0.0)
    if min_tp > 0 and "tp_pips" in pred_frame.columns:
        keep = keep & (pred_frame["tp_pips"].astype(float) >= min_tp)
    return raw.where(keep, LABEL_MAP["HOLD"]).astype(int)


def _calibrate_method(cfg: dict[str, Any]) -> str | None:
    raw = (cfg.get("model") or {}).get("calibrate")
    if raw is None or raw is False:
        return None
    method = str(raw).strip().lower()
    if method in ("", "none", "false", "0"):
        return None
    if method not in ("isotonic", "sigmoid"):
        return None
    return method


def prune_feature_columns(model, columns: list[str], frac: float) -> list[str]:
    """Drop the lowest-gain fraction of features (train-fold importance only)."""
    if frac is None or float(frac) <= 0:
        return list(columns)
    imp = getattr(model, "feature_importances_", None)
    if imp is None or len(imp) != len(columns):
        return list(columns)
    n_drop = int(round(len(columns) * float(frac)))
    if n_drop <= 0:
        return list(columns)
    order = np.argsort(np.asarray(imp, dtype=float))
    drop = set(int(i) for i in order[:n_drop])
    kept = [c for i, c in enumerate(columns) if i not in drop]
    return kept if kept else list(columns)


def feature_importance_table(model, columns: list[str], top_n: int = 12) -> list[dict[str, Any]]:
    imp = getattr(model, "feature_importances_", None)
    if imp is None or len(imp) != len(columns):
        return []
    rows = [{"feature": c, "importance": float(v)} for c, v in zip(columns, imp)]
    rows.sort(key=lambda r: r["importance"], reverse=True)
    return rows[:top_n]


def fit_predict_bundle(
    cfg: dict[str, Any],
    X_tr: pd.DataFrame,
    y_tr: pd.Series,
    X_te: pd.DataFrame,
    *,
    model_type: str | None = None,
    balanced: bool = True,
) -> tuple[Any, pd.DataFrame, list[str]]:
    """Fit (optional prune + calibrate) on train only; predict test. No test leakage."""
    model, _name = build_model(cfg, model_type)
    cols = list(X_tr.columns)
    prune_frac = float((cfg.get("model") or {}).get("prune_bottom_frac", 0.0) or 0.0)
    method = _calibrate_method(cfg)
    cal_frac = float((cfg.get("model") or {}).get("calibrate_frac", 0.2) or 0.2)

    n = len(X_tr)
    n_cal = 0
    if method:
        n_cal = max(100, int(n * cal_frac))
        n_cal = min(n_cal, max(0, n - 50))
        if n_cal < 50:
            n_cal = 0
            method = None

    X_fit, y_fit = (X_tr.iloc[: n - n_cal], y_tr.iloc[: n - n_cal]) if n_cal else (X_tr, y_tr)
    X_cal, y_cal = (X_tr.iloc[n - n_cal :], y_tr.iloc[n - n_cal :]) if n_cal else (None, None)

    fit_model(model, X_fit[cols], y_fit, balanced=balanced)
    if prune_frac > 0:
        cols = prune_feature_columns(model, cols, prune_frac)
        model, _name = build_model(cfg, model_type)
        fit_model(model, X_fit[cols], y_fit, balanced=balanced)

    predictor = model
    if method and X_cal is not None:
        try:
            from sklearn.calibration import CalibratedClassifierCV

            try:
                cal = CalibratedClassifierCV(estimator=model, method=method, cv="prefit")
            except TypeError:
                cal = CalibratedClassifierCV(base_estimator=model, method=method, cv="prefit")
            cal.fit(X_cal[cols], y_cal)
            predictor = cal
        except Exception:
            predictor = model

    proba = predict_proba_aligned(predictor, X_te[cols])
    if method and proba is not None:
        pred = proba.argmax(axis=1)
    else:
        pred = predictor.predict(X_te[cols])
    frame = pd.DataFrame({"pred_raw": pred}, index=X_te.index)
    if proba is not None:
        frame["p_sell"] = proba[:, LABEL_MAP["SELL"]]
        frame["p_hold"] = proba[:, LABEL_MAP["HOLD"]]
        frame["p_buy"] = proba[:, LABEL_MAP["BUY"]]
        frame["confidence"] = proba.max(axis=1)
        frame["dir_edge"] = np.abs(proba[:, LABEL_MAP["BUY"]] - proba[:, LABEL_MAP["SELL"]])
    for extra in ("sess_asia", "sess_london", "sess_ny", "vol_regime", "atr_pct"):
        if extra in X_te.columns:
            frame[extra] = X_te[extra].to_numpy()
    return predictor, frame, cols


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
        model, frame, used_cols = fit_predict_bundle(
            cfg, X_train, y_train, X_val, model_type=mtype, balanced=balanced
        )
        pred = frame["pred_raw"].to_numpy()
        acc = float(accuracy_score(y_val, pred))
        report = classification_report(
            y_val, pred, target_names=["SELL", "HOLD", "BUY"], zero_division=0, output_dict=True
        )
        name = mtype
        paths = model_paths(pair, cfg, name)
        joblib.dump({"model": model, "features": used_cols}, paths["model"])
        inner = model
        if hasattr(model, "calibrated_classifiers_"):
            inner = getattr(model, "estimator", None) or getattr(model, "base_estimator", None) or model
        imp = feature_importance_table(inner, used_cols)
        meta = {
            "pair": pair.upper(),
            "model_type": name,
            "features": used_cols,
            "val_accuracy": acc,
            "classification_report": report,
            "label_map": INV_LABEL_MAP,
            "label_scheme": cfg.get("label_scheme", "triple_barrier"),
            "horizon": cfg.get("horizon"),
            "label_threshold": cfg.get("label_threshold"),
            "barrier": cfg.get("barrier"),
            "entry_timing": cfg.get("entry_timing", "next_open"),
            "label_distribution": label_distribution(y),
            "feature_importance": imp,
            "calibrate": (cfg.get("model") or {}).get("calibrate"),
            "prune_bottom_frac": (cfg.get("model") or {}).get("prune_bottom_frac", 0.0),
        }
        paths["meta"].write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        results[name] = {"val_accuracy": acc, "path": str(paths["model"]), "report": report, "feature_importance": imp}
        safe_print(f"[train] {name}: val_accuracy={acc:.4f} -> {paths['model']}")

    return results


def load_model(pair: str, cfg: dict[str, Any], model_type: str | None = None):
    mtype = (model_type or (cfg.get("model") or {}).get("type") or "xgboost").lower()
    paths = model_paths(pair, cfg, mtype)
    if not paths["model"].exists():
        raise FileNotFoundError(f"No model at {paths['model']}. Run train first.")
    bundle = joblib.load(paths["model"])
    return bundle["model"], bundle["features"], mtype
