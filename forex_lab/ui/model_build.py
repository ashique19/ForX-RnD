"""Decision-desk status for the Active pair's Core AI joblib.

Separate from price freshness. A STALE bar does not mean the model is old,
and an old joblib does not mean the last close is stale.

Nothing here trains, fetches, or starts the retrain gate. Callers that want
the gate use ``forex_lab.retrain.run_retrain_gate`` on an explicit action.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from forex_lab.clock import fmt_display
from forex_lab.config_loader import load_config
from forex_lab.features import build_features
from forex_lab.retrain import (
    HONEST_NOTE,
    challenger_meta_path,
    load_champion,
    models_champion_pointer,
)
from forex_lab.ui.pipeline import artifact_status

STATUS_OK = "OK"
STATUS_NEED_TRAIN = "need Train"
STATUS_NEED_FETCH = "need Fetch"
STATUS_MISMATCH = "schema/config mismatch"
STATUS_RETRAIN = "retrain suggested"

_PENDING = frozenset({"pending", "challenger_pending", "awaiting"})
_LOST = frozenset({"lost", "challenger_lost", "null", "rejected"})
_CONTRACT_CACHE: dict[str, frozenset[str]] = {}


def model_build_status(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Status of one pair's configured model artifact.

    Priority (first match wins): missing/empty joblib → need Train;
    missing OHLCV cache → need Fetch; feature or label contract differs
    from the saved meta → schema/config mismatch; champion/challenger meta
    says the challenger is pending or lost → retrain suggested; else OK.
    ``reason`` is never empty.
    """
    from forex_lab.ui.watchlist import normalize_pair

    cfg = cfg if cfg is not None else load_config()
    symbol = normalize_pair(pair)
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    else:
        clock = clock.astimezone(timezone.utc)

    mtype = str((cfg.get("model") or {}).get("type") or "xgboost").lower()
    art = artifact_status(symbol, cfg)
    model_file: Path = art["model_file"]
    interval = str(art["interval"])
    data_exists = bool(art["data_exists"])
    exists, empty, mtime = _joblib_facts(model_file)
    age_hours = _age_hours(mtime, clock) if mtime is not None else None
    mtime_dhaka = fmt_display(mtime, cfg, seconds=True) if mtime is not None else None
    champion = _champion_view(symbol, cfg)

    base = {
        "pair": symbol,
        "model_type": mtype,
        "interval": interval,
        "joblib_exists": exists and not empty,
        "joblib_mtime_dhaka": mtime_dhaka,
        "age_hours": age_hours,
        "data_exists": data_exists,
        "champion": champion,
    }

    if not exists or empty:
        if empty:
            reason = f"{mtype} joblib for {symbol} is empty — Train required."
        else:
            reason = f"no {mtype} joblib for {symbol} — Train required."
        if not data_exists:
            reason += f" OHLCV cache for {interval} is also missing; Fetch before Train."
        return {**base, "status": STATUS_NEED_TRAIN, "reason": reason}

    if not data_exists:
        reason = (
            f"OHLCV cache missing for {symbol} {interval} — Fetch required "
            f"before this {mtype} joblib can be scored. Separate from price STALE."
        )
        return {**base, "status": STATUS_NEED_FETCH, "reason": reason}

    meta = _read_json(Path(str(model_file).removesuffix(".joblib") + "_meta.json"))
    saved_features = _feature_list(meta)
    if meta is None or saved_features is None:
        reason = (
            f"{mtype} joblib for {symbol} has no feature meta, so the schema "
            "cannot be checked against config. Retrain writes that meta. Not a price STALE alert."
        )
        return {**base, "status": STATUS_MISMATCH, "reason": reason}

    detail = _schema_detail(symbol, cfg, meta, saved_features, volume_varies=_volume_varies(art["data_csv"]))
    if detail:
        reason = (
            f"schema/config mismatch — {detail}. "
            "The joblib was built for a different feature or label contract. Not a price STALE alert."
        )
        return {**base, "status": STATUS_MISMATCH, "reason": reason}

    state = (champion or {}).get("challenger_state")
    if state == "pending":
        reason = (
            f"Champion meta marks a {mtype} challenger as pending for {symbol}. "
            "The retrain gate has not promoted it. Research only — not a live edge."
        )
        return {**base, "status": STATUS_RETRAIN, "reason": reason}
    if state == "lost":
        reason = (
            f"Last {mtype} challenger lost (verdict null) — champion kept for {symbol}. "
            "Another gate is optional. Research compare only — not a price STALE alert and not a live edge."
        )
        return {**base, "status": STATUS_RETRAIN, "reason": reason}

    reason = (
        f"{mtype} joblib matches this config. "
        "File age is not a price freshness alert."
    )
    return {**base, "status": STATUS_OK, "reason": reason}


def expected_model_features(
    cfg: dict[str, Any],
    pair: str,
    *,
    volume_varies: bool,
) -> list[str]:
    """Column names a fresh train would persist for this config.

    ``vol_z`` is included only when cached volume actually varies. Flat FX
    volume (typical yfinance) never writes that column.
    """
    names = set(_contract(cfg, pair))
    if not volume_varies:
        names.discard("vol_z")
    return sorted(names)


def _schema_detail(
    pair: str,
    cfg: dict[str, Any],
    meta: dict[str, Any],
    saved_features: list[str],
    *,
    volume_varies: bool,
) -> str:
    bits: list[str] = []
    saved_type = str(meta.get("model_type") or "").strip().lower()
    current_type = str((cfg.get("model") or {}).get("type") or "xgboost").lower()
    if saved_type and saved_type != current_type:
        bits.append(f"meta model_type is {saved_type}, config model.type is {current_type}")

    expected = set(expected_model_features(cfg, pair, volume_varies=volume_varies))
    # A model may list vol_z only when the CSV that trained it had volume.
    # If this cache is flat, vol_z in the joblib is a column scoring cannot build.
    saved = set(saved_features)
    missing = sorted(expected - saved)
    extra = sorted(saved - expected)
    if missing:
        bits.append("config features missing from joblib: " + _name_list(missing))
    if extra:
        bits.append("joblib features not produced by current config: " + _name_list(extra))

    label_bits = _label_deltas(meta, cfg)
    if label_bits:
        bits.append("label settings differ: " + "; ".join(label_bits))
    return "; ".join(bits)


def _label_deltas(meta: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if "label_scheme" in meta:
        saved = str(meta.get("label_scheme") or "")
        current = str(cfg.get("label_scheme") or "")
        if saved != current:
            out.append(f"label_scheme {saved} vs {current}")
    if "horizon" in meta and meta.get("horizon") is not None:
        try:
            saved_h = int(meta["horizon"])
            current_h = int(cfg.get("horizon"))
        except (TypeError, ValueError):
            saved_h, current_h = None, None
        if saved_h is not None and current_h is not None and saved_h != current_h:
            out.append(f"horizon {saved_h} vs {current_h}")
    if "entry_timing" in meta:
        saved_e = str(meta.get("entry_timing") or "")
        current_e = str(cfg.get("entry_timing") or "next_open")
        if saved_e != current_e:
            out.append(f"entry_timing {saved_e} vs {current_e}")
    if "label_threshold" in meta and str(cfg.get("label_scheme") or "") == "forward_return":
        if not _same_num(meta.get("label_threshold"), cfg.get("label_threshold")):
            out.append("label_threshold differs")
    saved_barrier = meta.get("barrier") if isinstance(meta.get("barrier"), dict) else None
    if saved_barrier is not None:
        current_barrier = dict(cfg.get("barrier") or {})
        for key in ("tp_atr", "sl_atr", "path", "timeout_label", "cost_aware"):
            if key not in saved_barrier:
                continue
            if not _same_value(saved_barrier.get(key), current_barrier.get(key)):
                out.append(f"barrier.{key} {saved_barrier.get(key)} vs {current_barrier.get(key)}")
    model_cfg = dict(cfg.get("model") or {})
    if "calibrate" in meta and not _same_value(meta.get("calibrate"), model_cfg.get("calibrate")):
        out.append(f"calibrate {meta.get('calibrate')!r} vs {model_cfg.get('calibrate')!r}")
    if "prune_bottom_frac" in meta and not _same_num(
        meta.get("prune_bottom_frac"), model_cfg.get("prune_bottom_frac") or 0.0
    ):
        out.append("prune_bottom_frac differs")
    return out


def _contract(cfg: dict[str, Any], pair: str) -> frozenset[str]:
    key = json.dumps(
        {
            "pair": str(pair).upper(),
            "sma_windows": cfg.get("sma_windows"),
            "ema_windows": cfg.get("ema_windows"),
            "rsi_period": cfg.get("rsi_period"),
            "atr_period": cfg.get("atr_period"),
            "vol_window": cfg.get("vol_window"),
            "vol_long_window": cfg.get("vol_long_window"),
            "range_windows": cfg.get("range_windows"),
            "feature_extras": cfg.get("feature_extras"),
            "interval": cfg.get("interval"),
        },
        sort_keys=True,
        default=str,
    )
    cached = _CONTRACT_CACHE.get(key)
    if cached is not None:
        return cached
    idx = pd.date_range("2024-01-02", periods=400, freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    close = 1.1 + np.cumsum(rng.normal(0.0, 0.0004, len(idx)))
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.0008,
            "Low": close - 0.0008,
            "Close": close,
            "Volume": rng.normal(1000.0, 50.0, len(idx)),
        },
        index=idx,
    )
    cols = frozenset(str(c) for c in build_features(frame, cfg, pair=pair).columns)
    _CONTRACT_CACHE[key] = cols
    return cols


def _champion_view(pair: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    record = load_champion(pair, cfg)
    pointer = _read_json(models_champion_pointer(pair, cfg))
    challenger = _read_json(challenger_meta_path(pair, cfg))
    primary = record or pointer
    if primary is None and challenger is None:
        return None
    state = _challenger_state(record, pointer, challenger)
    source = primary or challenger or {}
    promoted = source.get("promoted_at_display") or source.get("promoted_at")
    promoted_dhaka = promoted if isinstance(promoted, str) and "Dhaka" in promoted else None
    if promoted_dhaka is None and promoted:
        promoted_dhaka = fmt_display(promoted, cfg, seconds=True)
        if promoted_dhaka == "n/a":
            promoted_dhaka = None
    summary = _champion_summary(source, state, promoted_dhaka)
    note = str(source.get("honest_note") or (challenger or {}).get("honest_note") or HONEST_NOTE)
    verdict = source.get("verdict") or source.get("status")
    chal_verdict = None
    if challenger and challenger.get("verdict"):
        chal_verdict = str(challenger.get("verdict"))
    elif record and record.get("challenger_verdict"):
        chal_verdict = str(record.get("challenger_verdict"))
    return {
        "verdict": None if verdict is None else str(verdict),
        "promoted_at_dhaka": promoted_dhaka,
        "summary": summary,
        "challenger_verdict": chal_verdict,
        "challenger_state": state,
        "honest_note": note,
    }


def _challenger_state(
    record: dict[str, Any] | None,
    pointer: dict[str, Any] | None,
    challenger: dict[str, Any] | None,
) -> str | None:
    blobs: list[dict[str, Any]] = []
    for item in (record, pointer):
        if not item:
            continue
        blobs.append(item)
        nested = item.get("challenger")
        if isinstance(nested, dict):
            blobs.append(nested)
    for blob in blobs:
        for key in ("challenger_status", "challenger_state", "challenger_verdict"):
            state = _state_token(blob.get(key))
            if state:
                return state
    if challenger:
        for key in ("challenger_status", "challenger_state", "status", "verdict"):
            # The gate's own status field is "champion" only on the champion file.
            # A sidecar status/verdict of null is a lost compare.
            state = _state_token(challenger.get(key))
            if state:
                return state
    return None


def _state_token(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if text in _PENDING:
        return "pending"
    if text in _LOST:
        return "lost"
    return None


def _champion_summary(source: dict[str, Any], state: str | None, promoted_dhaka: str | None) -> str:
    parts: list[str] = []
    verdict = source.get("verdict") or source.get("status")
    if verdict:
        parts.append(str(verdict))
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    pf = metrics.get("profit_factor")
    ret = metrics.get("total_return")
    dd = metrics.get("max_drawdown")
    if isinstance(pf, (int, float)):
        parts.append(f"PF {float(pf):.2f}")
    if isinstance(ret, (int, float)):
        parts.append(f"ret {float(ret):.3f}")
    if isinstance(dd, (int, float)):
        parts.append(f"DD {float(dd):.3f}")
    if promoted_dhaka:
        parts.append(promoted_dhaka)
    if state == "pending":
        parts.append("challenger pending")
    elif state == "lost":
        parts.append("challenger lost")
    parts.append("not a live edge")
    return " · ".join(parts)


def _feature_list(meta: dict[str, Any] | None) -> list[str] | None:
    if not meta:
        return None
    raw = meta.get("features")
    if not isinstance(raw, list) or not raw:
        return None
    names = [str(item) for item in raw if str(item).strip()]
    return names or None


def _joblib_facts(path: Path) -> tuple[bool, bool, datetime | None]:
    try:
        if not path.exists():
            return False, False, None
        size = path.stat().st_size
        if size <= 0:
            return True, True, None
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return True, False, mtime
    except OSError:
        return False, False, None


def _age_hours(mtime: datetime, now: datetime) -> float:
    delta = (now - mtime).total_seconds() / 3600.0
    if delta < 0:
        delta = 0.0
    return round(delta, 2)


def _volume_varies(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return False
        frame = pd.read_csv(path, usecols=lambda col: str(col).strip().lower() == "volume")
    except (OSError, ValueError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return False
    if frame.empty or frame.shape[1] < 1:
        return False
    series = pd.to_numeric(frame.iloc[:, 0], errors="coerce")
    std = series.std()
    if std is None or pd.isna(std):
        return False
    return float(std) > 0.0


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _name_list(names: list[str]) -> str:
    shown = ", ".join(names[:4])
    if len(names) > 4:
        return f"{shown} (+{len(names) - 4} more)"
    return shown


def _same_num(left: object, right: object) -> bool:
    try:
        a = float(left) if left is not None else 0.0
        b = float(right) if right is not None else 0.0
    except (TypeError, ValueError):
        return False
    return abs(a - b) <= 1e-9


def _same_value(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        if left is None or right is None:
            return left is right
        return _same_num(left, right)
    return str(left or "").strip().lower() == str(right or "").strip().lower()
