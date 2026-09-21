"""Local explanations for research signals.

Describes what the model and config rules did on one bar.
Not a trading recommendation and not evidence of an edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.features import INV_LABEL_MAP, LABEL_MAP, build_features
from forex_lab.model import apply_signal_filters, load_model, predict_proba_aligned

FEATURE_HINTS = {
    "rsi": "RSI",
    "vol_regime": "vol regime vs long-run",
    "vol_pct": "short-vol vs its recent range",
    "vol_shock": "abs return vs vol",
    "atr_pct": "ATR%",
    "sma20_slope": "20-bar SMA slope",
    "mom_mr": "momentum vs stretch",
    "sess_ny": "New York session (UTC)",
    "sess_london": "London session (UTC)",
    "sess_asia": "Asia session (UTC)",
    "sess_ldn_ny": "London/NY overlap (UTC)",
    "macd_norm": "EMA spread / price",
    "tf_4h_sma_slope": "4h SMA slope (completed HTF bars)",
    "tf_4h_sma_ratio": "price vs 4h SMA",
    "tf_4h_ret": "last completed 4h return",
    "tf_1D_sma_slope": "daily SMA slope (completed HTF bars)",
    "tf_1D_sma_ratio": "price vs daily SMA",
    "tf_1D_ret": "last completed daily return",
    "xpair_ret_1": "cross-pair 1-bar return",
    "xpair_ret_6": "cross-pair 6-bar return",
    "xpair_sma20_ratio": "cross-pair vs its SMA20",
    "ta_stoch_k": "stochastic %K (causal)",
    "ta_stoch_d": "stochastic %D (causal)",
    "ta_adx": "ADX trend strength (causal)",
    "ta_plus_di": "+DI (causal)",
    "ta_minus_di": "-DI (causal)",
    "ta_bb_pctb": "Bollinger %B (causal)",
    "ta_bb_bw": "Bollinger bandwidth (causal)",
    "ta_cci": "CCI (causal)",
    "ta_willr": "Williams %R (causal, 0-1)",
    "ta_roc": "rate of change (causal)",
    "ta_kc_pos": "Keltner channel position (causal)",
}


def feature_hint(name: str) -> str:
    if name in FEATURE_HINTS:
        return FEATURE_HINTS[name]
    if str(name).startswith("ta_"):
        return "causal TA pack"
    if str(name).startswith("fred_"):
        return "FRED macro (as-of lagged)"
    return ""


@dataclass
class Driver:
    feature: str
    contribution: float
    value: float | None = None
    hint: str = ""


@dataclass
class RuleCheck:
    name: str
    enabled: bool
    passed: bool | None
    detail: str


@dataclass
class SignalExplanation:
    pair: str
    signal: str
    raw_signal: str
    method: str
    drivers: list[Driver] = field(default_factory=list)
    rules: list[RuleCheck] = field(default_factory=list)
    rationale: str = ""
    target: str = ""
    error: str | None = None

    def compact_drivers(self, n: int = 3) -> str:
        if not self.drivers:
            return ""
        parts = []
        for d in self.drivers[:n]:
            sign = "+" if d.contribution >= 0 else ""
            parts.append(f"{sign}{d.feature} {d.contribution:.3f}")
        return ", ".join(parts)

    def compact_rules(self) -> str:
        bits = []
        for r in self.rules:
            if not r.enabled:
                continue
            if r.passed is True:
                bits.append(f"{r.name} pass")
            elif r.passed is False:
                bits.append(f"{r.name} fail")
        return " · ".join(bits) if bits else "no extra rules"


def _unwrap_model(model):
    if hasattr(model, "calibrated_classifiers_"):
        return getattr(model, "estimator", None) or getattr(model, "base_estimator", None) or model
    return model


def _shap_contribs(model, X: pd.DataFrame, class_id: int) -> np.ndarray | None:
    try:
        import shap  # type: ignore
    except Exception:
        return None
    inner = _unwrap_model(model)
    try:
        explainer = shap.TreeExplainer(inner)
        values = explainer.shap_values(X)
    except Exception:
        return None
    if isinstance(values, list) and len(values) > class_id:
        arr = np.asarray(values[class_id])
        return arr[0] if arr.ndim == 2 else arr
    arr = np.asarray(values)
    if arr.ndim == 3:
        # (n, n_feat, n_class) or (n, n_class, n_feat)
        if arr.shape[-1] == 3 and arr.shape[1] != 3:
            return arr[0, :, class_id]
        if arr.shape[1] == 3:
            return arr[0, class_id, :]
    if arr.ndim == 2:
        return arr[0]
    return None


def _xgb_contribs(model, X: pd.DataFrame, class_id: int) -> np.ndarray | None:
    inner = _unwrap_model(model)
    if not hasattr(inner, "get_booster"):
        return None
    try:
        import xgboost as xgb

        booster = inner.get_booster()
        dm = xgb.DMatrix(X.to_numpy(), feature_names=list(X.columns))
        raw = np.asarray(booster.predict(dm, pred_contribs=True))
    except Exception:
        return None
    # Drop bias term (last feature dim).
    if raw.ndim == 3:
        # (n, n_class, n_feat+1) or (n, n_feat+1, n_class)
        if raw.shape[1] == 3:
            row = raw[0, class_id, :]
        elif raw.shape[2] == 3:
            row = raw[0, :, class_id]
        else:
            row = raw[0, class_id, :]
        return row[:-1] if row.size else None
    if raw.ndim == 2:
        return raw[0, :-1]
    return None


def _linear_contribs(model, X: pd.DataFrame, class_id: int) -> np.ndarray | None:
    inner = _unwrap_model(model)
    clf = inner
    x_use = X.to_numpy()[0]
    if hasattr(inner, "named_steps"):
        clf = inner.named_steps.get("clf")
        scaler = inner.named_steps.get("scaler")
        if scaler is not None:
            try:
                x_use = scaler.transform(X)[0]
            except Exception:
                x_use = X.to_numpy()[0]
    if clf is None or not hasattr(clf, "coef_"):
        return None
    coef = np.asarray(clf.coef_)
    if coef.ndim == 2:
        if class_id >= coef.shape[0]:
            return None
        return coef[class_id] * x_use
    return coef * x_use


def _importance_fallback(model, columns: list[str], X: pd.DataFrame) -> list[Driver]:
    inner = _unwrap_model(model)
    imp = getattr(inner, "feature_importances_", None)
    if imp is None or len(imp) != len(columns):
        return []
    row = X.iloc[0]
    ranked = sorted(zip(columns, imp), key=lambda kv: float(kv[1]), reverse=True)
    out = []
    for name, gain in ranked[:8]:
        val = row[name] if name in row.index else None
        try:
            v = float(val) if val is not None and pd.notna(val) else None
        except (TypeError, ValueError):
            v = None
        out.append(Driver(feature=name, contribution=float(gain), value=v, hint=feature_hint(name)))
    return out


def local_drivers(
    model,
    X: pd.DataFrame,
    columns: list[str],
    class_id: int,
    top_n: int = 5,
) -> tuple[list[Driver], str]:
    """Top local drivers for ``class_id``. Returns (drivers, method)."""
    if X.empty:
        return [], "none"
    x = X[columns] if all(c in X.columns for c in columns) else X
    methods = (
        ("pred_contribs", lambda: _xgb_contribs(model, x, class_id)),
        ("shap", lambda: _shap_contribs(model, x, class_id)),
        ("linear", lambda: _linear_contribs(model, x, class_id)),
    )
    vec = None
    method = "importance"
    for name, fn in methods:
        vec = fn()
        if vec is not None and len(vec) >= min(3, len(columns)):
            method = name
            break
    if vec is None or len(vec) < min(3, len(columns)):
        return _importance_fallback(model, columns, x)[:top_n], "importance"
    n = min(len(columns), len(vec))
    pairs = list(zip(columns[:n], np.asarray(vec[:n], dtype=float)))
    pairs.sort(key=lambda kv: abs(float(kv[1])), reverse=True)
    row = x.iloc[0]
    drivers = []
    for name, contrib in pairs[:top_n]:
        val = row[name] if name in row.index else None
        try:
            v = float(val) if val is not None and pd.notna(val) else None
        except (TypeError, ValueError):
            v = None
        drivers.append(
            Driver(
                feature=name,
                contribution=float(contrib),
                value=v,
                hint=feature_hint(name),
            )
        )
    return drivers, method


def evaluate_signal_rules(
    frame_row: pd.Series | dict[str, Any],
    cfg: dict[str, Any],
    raw_id: int,
) -> list[RuleCheck]:
    """Which config rules would pass/fail on this bar (after model probs)."""
    sig_cfg = cfg.get("signals") or {}
    get = frame_row.get if hasattr(frame_row, "get") else lambda k, d=None: frame_row[k] if k in frame_row else d

    def _f(key: str) -> float | None:
        v = get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rules: list[RuleCheck] = []
    min_conf = float(sig_cfg.get("min_confidence", 0.40))
    conf = _f("confidence")
    raw_dir = int(raw_id) != LABEL_MAP["HOLD"]
    if min_conf > 0:
        ok = conf is not None and conf >= min_conf if raw_dir else True
        rules.append(
            RuleCheck(
                "min_confidence",
                True,
                bool(ok),
                f"conf={conf if conf is not None else 'n/a'} vs min {min_conf:.2f}"
                + ("" if raw_dir else " (raw HOLD; floor applies to BUY/SELL only)"),
            )
        )
    else:
        rules.append(RuleCheck("min_confidence", False, None, "off"))

    min_edge = float(sig_cfg.get("min_dir_edge", 0.0) or 0.0)
    edge = _f("dir_edge")
    if min_edge > 0:
        ok = edge is not None and edge >= min_edge if raw_dir else True
        rules.append(
            RuleCheck("min_dir_edge", True, bool(ok), f"dir_edge={edge if edge is not None else 'n/a'} vs min {min_edge:.2f}")
        )
    else:
        rules.append(RuleCheck("min_dir_edge", False, None, "off (model HOLD class sits out)"))

    sessions = sig_cfg.get("sessions") or []
    if sessions:
        sess_ok = False
        seen = False
        hits = []
        for name in sessions:
            col = f"sess_{str(name).strip().lower()}"
            v = _f(col)
            if v is not None:
                seen = True
                if v > 0.5:
                    sess_ok = True
                    hits.append(str(name))
        if not raw_dir:
            sess_ok = True
        rules.append(
            RuleCheck(
                "sessions",
                True,
                bool(sess_ok) if seen else None,
                f"allow {list(sessions)}; in {hits or 'none'}" if seen else "session columns missing",
            )
        )
    else:
        rules.append(RuleCheck("sessions", False, None, "all UTC hours (allow-list empty)"))

    min_vol = float(sig_cfg.get("min_vol_regime", 0.0) or 0.0)
    vol = _f("vol_regime")
    if min_vol > 0:
        ok = vol is not None and vol >= min_vol if raw_dir else True
        rules.append(
            RuleCheck("min_vol_regime", True, bool(ok), f"vol_regime={vol if vol is not None else 'n/a'} vs min {min_vol}")
        )
    else:
        rules.append(RuleCheck("min_vol_regime", False, None, "off"))

    htf = str(sig_cfg.get("htf_trend_filter") or "").strip()
    if htf and htf.lower() not in ("none", "off", "false", "0"):
        from forex_lab.features import _tf_rule

        parsed = _tf_rule(htf)
        tag = parsed[0] if parsed else htf
        col = f"tf_{tag}_sma_slope"
        slope = _f(col)
        if not raw_dir:
            ok = True
            detail = f"{col} unused (raw HOLD)"
        elif slope is None or not np.isfinite(slope):
            ok = False
            detail = f"{col} missing/NaN"
        elif int(raw_id) == LABEL_MAP["BUY"]:
            ok = slope > 0
            detail = f"BUY requires {col}>0; slope={slope:.5f}"
        elif int(raw_id) == LABEL_MAP["SELL"]:
            ok = slope < 0
            detail = f"SELL requires {col}<0; slope={slope:.5f}"
        else:
            ok = True
            detail = f"{col}={slope:.5f}"
        rules.append(RuleCheck("htf_trend_filter", True, bool(ok), detail))
    else:
        rules.append(
            RuleCheck(
                "htf_trend_filter",
                False,
                None,
                "off (London+NY and similar filters hurt EURUSD in prior screens)",
            )
        )
    try:
        from forex_lab.gates import gates_enabled, gate_min_confidence, no_new_opens_in_event_window, require_mtf_agree

        if gates_enabled(cfg):
            floor = gate_min_confidence(cfg)
            bits = []
            if require_mtf_agree(cfg):
                bits.append("MTF agree")
            if floor:
                bits.append(f"min_conf={floor:.2f}")
            if no_new_opens_in_event_window(cfg):
                bits.append("no-new-opens in event window")
            rules.append(
                RuleCheck(
                    "gates",
                    True,
                    True,
                    "on: " + ", ".join(bits) + " (fail-soft if calendar/MTF missing; not a live edge)",
                )
            )
        else:
            rules.append(
                RuleCheck(
                    "gates",
                    False,
                    None,
                    "off (default; EURUSD WF did not clear PF/return/DD bar — see reports/gate_screen.md)",
                )
            )
    except Exception:
        pass
    return rules


def _try_ollama(prompt: str, cfg: dict[str, Any]) -> str | None:
    block = cfg.get("explain") or {}
    if not bool(block.get("ollama", False)):
        return None
    timeout = float(block.get("ollama_timeout_s", 1.0) or 1.0)
    model = str(block.get("ollama_model") or "llama3.2")
    try:
        import json
        import urllib.request

        payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        text = str(data.get("response") or "").strip()
        return text[:600] if text else None
    except Exception:
        return None


def grounded_narrative(
    *,
    pair: str,
    signal: str,
    raw_signal: str,
    drivers: list[Driver],
    rules: list[RuleCheck],
    target: str,
    method: str,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Template rationale from structured drivers + rules. Does not claim an edge."""
    cfg = cfg or {}
    sig = str(signal).upper()
    raw = str(raw_signal).upper()
    lead = f"{pair} model class is {sig}"
    if raw != sig:
        lead += f" (raw {raw} held back by rules)"
    lead += "."

    if drivers:
        bits = []
        for d in drivers[:4]:
            direction = "pushed toward this class" if d.contribution >= 0 else "pushed against this class"
            label = d.hint or d.feature
            bits.append(f"{label} ({d.feature} {d.contribution:+.3f}) {direction}")
        drv = "Top local drivers: " + "; ".join(bits) + f". Method: {method}."
    else:
        drv = "No local feature drivers available for this model."

    failed = [r for r in rules if r.enabled and r.passed is False]
    passed = [r for r in rules if r.enabled and r.passed is True]
    if failed:
        rule_txt = "Rules that blocked a directional call: " + "; ".join(f"{r.name} ({r.detail})" for r in failed) + "."
    elif passed:
        rule_txt = "Enabled rules that passed: " + ", ".join(r.name for r in passed) + "."
    else:
        rule_txt = "No extra human rules enabled beyond the model's own class."

    tgt = f" Research target (not an order): {target}." if target else ""
    footer = (
        " This describes the fitted model and config rules on one bar; "
        "it is not a broker quote and not evidence the signal is profitable."
    )
    text = f"{lead} {drv} {rule_txt}{tgt}{footer}"
    extra = _try_ollama(text, cfg)
    if extra:
        text += f" Local paraphrase (unverified, Ollama): {extra}"
    return " ".join(text.split())


def explain_latest_signal(
    pair: str,
    ohlcv: pd.DataFrame,
    cfg: dict[str, Any],
    last: pd.Series | dict[str, Any] | None = None,
    *,
    target: str = "",
) -> SignalExplanation:
    """Explain the latest bar for ``pair``. Never invents a trade."""
    pair = str(pair).upper()
    get = (last.get if last is not None and hasattr(last, "get") else None)

    def _g(key: str, default: Any = None) -> Any:
        if get is None:
            return default
        return get(key, default)

    signal = str(_g("signal") or "n/a").upper()
    raw_signal = str(_g("raw_signal") or signal).upper()
    try:
        model, feature_cols, _mtype = load_model(pair, cfg)
        feats = build_features(ohlcv, cfg, pair=pair)
        missing = [c for c in feature_cols if c not in feats.columns]
        if missing:
            raise KeyError(f"missing features {missing[:6]}")
        X_all = feats[feature_cols].dropna()
        if X_all.empty:
            raise RuntimeError("no valid feature rows")
        ts = _g("datetime")
        if ts is not None and ts in X_all.index:
            X_row = X_all.loc[[ts]]
        else:
            parsed = pd.to_datetime(ts, errors="coerce") if ts is not None else pd.NaT
            if pd.notna(parsed) and parsed in X_all.index:
                X_row = X_all.loc[[parsed]]
            else:
                X_row = X_all.iloc[[-1]]
        proba = predict_proba_aligned(model, X_row)
        pred_id = int(model.predict(X_row)[0])
        frame = pd.DataFrame({"pred_raw": [pred_id]}, index=X_row.index)
        if proba is not None:
            frame["p_sell"] = proba[:, LABEL_MAP["SELL"]]
            frame["p_hold"] = proba[:, LABEL_MAP["HOLD"]]
            frame["p_buy"] = proba[:, LABEL_MAP["BUY"]]
            frame["confidence"] = proba.max(axis=1)
            frame["dir_edge"] = (frame["p_buy"] - frame["p_sell"]).abs()
        from forex_lab.backtest import _attach_policy_columns

        frame = _attach_policy_columns(frame, feats, ohlcv, cfg, pair)
        filtered = int(apply_signal_filters(frame, cfg).iloc[0])
        signal = INV_LABEL_MAP.get(filtered, signal)
        raw_signal = INV_LABEL_MAP.get(pred_id, raw_signal)
        class_id = pred_id if pred_id in (0, 1, 2) else LABEL_MAP.get(raw_signal, 1)
        top_n = int((cfg.get("explain") or {}).get("top_n", 5) or 5)
        drivers, method = local_drivers(model, X_row, feature_cols, class_id, top_n=top_n)
        row = frame.iloc[0]
        # Prefer CSV probs when present so the UI matches the table.
        if last is not None:
            for k in ("confidence", "dir_edge", "p_buy", "p_sell", "p_hold"):
                v = _g(k)
                if v is not None:
                    row = row.copy()
                    row[k] = v
        rules = evaluate_signal_rules(row, cfg, pred_id)
        rationale = grounded_narrative(
            pair=pair,
            signal=signal,
            raw_signal=raw_signal,
            drivers=drivers,
            rules=rules,
            target=target,
            method=method,
            cfg=cfg,
        )
        return SignalExplanation(
            pair=pair,
            signal=signal,
            raw_signal=raw_signal,
            method=method,
            drivers=drivers,
            rules=rules,
            rationale=rationale,
            target=target,
        )
    except Exception as exc:  # noqa: BLE001 — board must still render
        return SignalExplanation(
            pair=pair,
            signal=signal,
            raw_signal=raw_signal,
            method="unavailable",
            rationale="Explanation unavailable for this row (need a trained model and cached OHLCV). "
            "This is not a hidden trade.",
            target=target,
            error=str(exc),
        )
