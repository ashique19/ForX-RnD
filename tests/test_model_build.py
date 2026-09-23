"""Core AI joblib status — separate from price STALE."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from forex_lab.config_loader import load_config
from forex_lab.ui.model_build import (
    STATUS_MISMATCH,
    STATUS_NEED_FETCH,
    STATUS_NEED_TRAIN,
    STATUS_OK,
    STATUS_RETRAIN,
    expected_model_features,
    model_build_status,
)


def _cfg(tmp_path: Path) -> dict:
    cfg = load_config()
    paths = dict(cfg.get("paths") or {})
    paths["models_dir"] = str(tmp_path / "models")
    paths["data_dir"] = str(tmp_path / "data")
    cfg["paths"] = paths
    retrain = dict(cfg.get("retrain") or {})
    retrain["store"] = str(tmp_path / "champion")
    cfg["retrain"] = retrain
    return cfg


def _csv(cfg: dict, pair: str = "EURUSD") -> None:
    path = Path(cfg["paths"]["data_dir"]) / f"{pair}_1h.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Datetime,Open,High,Low,Close,Volume\n2024-01-01 00:00:00,1.1,1.2,1.0,1.1,0\n",
        encoding="utf-8",
    )


def _joblib(cfg: dict, pair: str = "EURUSD", payload: bytes = b"xgboost-bytes") -> Path:
    path = Path(cfg["paths"]["models_dir"]) / f"{pair}_xgboost.joblib"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _meta(cfg: dict, features: list[str], pair: str = "EURUSD") -> None:
    path = Path(cfg["paths"]["models_dir"]) / f"{pair}_xgboost_meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "pair": pair,
        "model_type": "xgboost",
        "features": features,
        "label_scheme": cfg.get("label_scheme"),
        "horizon": cfg.get("horizon"),
        "entry_timing": cfg.get("entry_timing"),
        "label_threshold": cfg.get("label_threshold"),
        "barrier": cfg.get("barrier"),
        "calibrate": (cfg.get("model") or {}).get("calibrate"),
        "prune_bottom_frac": (cfg.get("model") or {}).get("prune_bottom_frac", 0.0),
    }
    path.write_text(json.dumps(body), encoding="utf-8")


def test_missing_joblib_needs_train_with_reason(tmp_path: Path):
    cfg = _cfg(tmp_path)
    status = model_build_status("GBPUSD", cfg, now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    assert status["status"] == STATUS_NEED_TRAIN
    assert status["model_type"] == "xgboost"
    assert status["joblib_mtime_dhaka"] is None
    assert status["age_hours"] is None
    reason = status["reason"]
    assert reason.strip()
    assert "joblib" in reason.lower()
    assert "Train" in reason
    assert "STALE" not in status["status"]


def test_empty_joblib_needs_train(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _csv(cfg)
    _joblib(cfg, payload=b"")
    status = model_build_status("EURUSD", cfg)
    assert status["status"] == STATUS_NEED_TRAIN
    assert "empty" in status["reason"].lower()
    assert "Train" in status["reason"]


def test_joblib_without_cache_needs_fetch(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _joblib(cfg)
    status = model_build_status("EURUSD", cfg, now=datetime(2026, 9, 23, 12, tzinfo=timezone.utc))
    assert status["status"] == STATUS_NEED_FETCH
    assert "Fetch" in status["reason"]
    assert status["joblib_mtime_dhaka"]
    assert "Asia/Dhaka" in status["joblib_mtime_dhaka"]
    assert status["age_hours"] is not None
    assert status["age_hours"] >= 0


def test_feature_mismatch_is_schema_not_stale(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _csv(cfg)
    _joblib(cfg)
    _meta(cfg, ["ret_1"])
    status = model_build_status("EURUSD", cfg)
    assert status["status"] == STATUS_MISMATCH
    assert status["reason"].strip()
    assert "schema/config mismatch" in status["reason"]
    assert "STALE" not in status["status"]
    assert "ret_1" not in status["reason"] or "missing" in status["reason"].lower()


def test_matching_joblib_is_ok(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _csv(cfg)
    _joblib(cfg)
    names = expected_model_features(cfg, "EURUSD", volume_varies=False)
    _meta(cfg, names)
    status = model_build_status("EURUSD", cfg)
    assert status["status"] == STATUS_OK
    assert "matches" in status["reason"]
    assert status["champion"] is None


def test_challenger_lost_suggests_retrain_honestly(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _csv(cfg)
    _joblib(cfg)
    names = expected_model_features(cfg, "EURUSD", volume_varies=False)
    _meta(cfg, names)
    store = Path(cfg["retrain"]["store"])
    store.mkdir(parents=True, exist_ok=True)
    (store / "EURUSD.json").write_text(
        json.dumps(
            {
                "pair": "EURUSD",
                "status": "champion",
                "verdict": "promote",
                "metrics": {"profit_factor": 1.1, "total_return": 0.02, "max_drawdown": -0.04},
                "promoted_at": "2026-09-01T12:00:00Z",
                "honest_note": "not a live edge",
            }
        ),
        encoding="utf-8",
    )
    (store / "EURUSD_challenger.json").write_text(
        json.dumps({"pair": "EURUSD", "verdict": "null", "promote": False}),
        encoding="utf-8",
    )
    status = model_build_status("EURUSD", cfg)
    assert status["status"] == STATUS_RETRAIN
    assert "lost" in status["reason"].lower()
    assert "not a live edge" in status["reason"].lower() or "not a price" in status["reason"].lower()
    assert status["champion"]["challenger_state"] == "lost"
    assert "PF 1.10" in status["champion"]["summary"]
    assert "not a live edge" in status["champion"]["summary"]
    assert "Asia/Dhaka" in (status["champion"]["promoted_at_dhaka"] or "")


def test_challenger_pending_on_champion_meta(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _csv(cfg)
    _joblib(cfg)
    names = expected_model_features(cfg, "EURUSD", volume_varies=False)
    _meta(cfg, names)
    store = Path(cfg["retrain"]["store"])
    store.mkdir(parents=True, exist_ok=True)
    (store / "EURUSD.json").write_text(
        json.dumps({"pair": "EURUSD", "verdict": "seed", "challenger_status": "pending"}),
        encoding="utf-8",
    )
    status = model_build_status("EURUSD", cfg)
    assert status["status"] == STATUS_RETRAIN
    assert "pending" in status["reason"].lower()
    assert status["champion"]["challenger_state"] == "pending"
