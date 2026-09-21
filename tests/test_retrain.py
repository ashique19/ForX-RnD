"""Champion/challenger promotion decision — walk-forward gate, not a live edge."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from forex_lab.retrain import (
    HONEST_NOTE,
    MODE_IMPROVE,
    MODE_NON_REGRESSION,
    VERDICT_NULL,
    VERDICT_PROMOTE,
    VERDICT_SEED,
    extract_model_metrics,
    format_retrain_text,
    load_champion,
    promotion_decision,
    run_retrain_gate,
    save_champion,
    should_promote,
)


def _m(*, pf, ret, dd, n=100, wr=0.5):
    return {
        "n_trades": n,
        "win_rate": wr,
        "profit_factor": pf,
        "total_return": ret,
        "max_drawdown": dd,
    }


def test_seed_when_no_champion():
    d = promotion_decision(None, _m(pf=0.9, ret=-0.02, dd=-0.08))
    assert d.verdict == VERDICT_SEED
    assert d.promote is False
    assert "not a promotion" in d.reasons[0]


def test_promote_when_pf_return_and_dd_all_improve():
    champ = _m(pf=0.90, ret=-0.05, dd=-0.10)
    chal = _m(pf=0.95, ret=-0.02, dd=-0.07)
    d = promotion_decision(champ, chal)
    assert d.mode == MODE_IMPROVE
    assert d.promote is True
    assert d.verdict == VERDICT_PROMOTE
    assert d.pf_ok and d.return_ok and d.dd_ok
    assert d.deltas["profit_factor"] > 0
    assert d.deltas["total_return"] > 0
    assert d.deltas["max_drawdown"] > 0
    assert should_promote(champ, chal) is True


def test_null_when_pf_ticks_but_dd_worsens():
    champ = _m(pf=0.90, ret=-0.05, dd=-0.08)
    chal = _m(pf=0.92, ret=-0.03, dd=-0.12)
    d = promotion_decision(champ, chal)
    assert d.promote is False
    assert d.verdict == VERDICT_NULL
    assert d.dd_ok is False
    assert any("max DD" in r for r in d.reasons)
    assert any("null" in r.lower() for r in d.reasons)


def test_null_when_all_equal():
    m = _m(pf=0.978, ret=-0.0206, dd=-0.077)
    d = promotion_decision(m, dict(m))
    assert d.promote is False
    assert d.verdict == VERDICT_NULL


def test_null_when_challenger_missing_or_no_trades():
    champ = _m(pf=0.9, ret=-0.02, dd=-0.08)
    assert promotion_decision(champ, None).verdict == VERDICT_NULL
    d = promotion_decision(champ, _m(pf=1.2, ret=0.1, dd=-0.01, n=0), {"retrain": {"min_trades": 1}})
    assert d.verdict == VERDICT_NULL
    assert "n_trades" in d.reasons[0]


def test_non_regression_requires_one_strict_improve():
    cfg = {"retrain": {"mode": MODE_NON_REGRESSION}}
    champ = _m(pf=0.90, ret=-0.05, dd=-0.10)
    # Tiny PF tick inside 0.05, return slightly better, DD unchanged -> promote
    chal_ok = _m(pf=0.91, ret=-0.049, dd=-0.10)
    d = promotion_decision(champ, chal_ok, cfg)
    assert d.mode == MODE_NON_REGRESSION
    assert d.promote is True
    # Equal within eps and no improve -> null
    chal_flat = _m(pf=0.90, ret=-0.05, dd=-0.10)
    d2 = promotion_decision(champ, chal_flat, cfg)
    assert d2.promote is False
    assert d2.verdict == VERDICT_NULL
    # PF crash beyond 0.05 bar
    chal_bad = _m(pf=0.80, ret=-0.04, dd=-0.09)
    d3 = promotion_decision(champ, chal_bad, cfg)
    assert d3.promote is False
    assert d3.pf_ok is False


def test_extract_model_metrics_from_latest_metrics_shape():
    payload = {
        "pair": "EURUSD",
        "model_type": "xgboost",
        "model": _m(pf=0.978, ret=-0.0206, dd=-0.077, n=1066),
        "baseline_sma_crossover": _m(pf=0.92, ret=-0.08, dd=-0.15),
    }
    got = extract_model_metrics(payload)
    assert got["profit_factor"] == 0.978
    assert got["n_trades"] == 1066
    assert got["pair"] == "EURUSD"


def test_run_retrain_gate_promote_and_null(tmp_path):
    cfg = {
        "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
        "retrain": {
            "store": str(tmp_path / "champion"),
            "mode": "improve",
            "train_on_promote": True,
            "fail_soft": True,
            "seed_from_metrics": False,
        },
        "model": {"type": "xgboost"},
        "paths": {"models_dir": str(tmp_path / "models"), "data_dir": str(tmp_path / "data")},
    }
    champ = {
        "pair": "EURUSD",
        "metrics": _m(pf=0.90, ret=-0.05, dd=-0.10),
        "verdict": "seed",
    }
    save_champion("EURUSD", cfg, champ)
    loaded = load_champion("EURUSD", cfg)
    assert loaded is not None
    assert loaded["metrics"]["profit_factor"] == 0.90

    trained = {"n": 0}

    def _wf(_df, _cfg, _pair):
        return (
            {"pair": "EURUSD", "model": _m(pf=0.96, ret=-0.01, dd=-0.06, n=40)},
            None,
            None,
        )

    def _train(_df, _cfg, _pair):
        trained["n"] += 1
        return {"xgboost": {"val_accuracy": 0.5}}

    promoted = run_retrain_gate(
        "EURUSD",
        cfg,
        ohlcv=object(),
        walk_forward_fn=_wf,
        train_fn=_train,
        persist=True,
    )
    assert promoted["verdict"] == VERDICT_PROMOTE
    assert promoted["promote"] is True
    assert promoted["champion_written"] is True
    assert promoted["trained"] is True
    assert trained["n"] == 1
    assert promoted["live_edge"] is False
    assert "not a live edge" in promoted["honest_note"].lower()
    stored = load_champion("EURUSD", cfg)
    assert stored["metrics"]["profit_factor"] == 0.96
    text = format_retrain_text(promoted)
    assert "verdict=promote" in text
    assert HONEST_NOTE.split("—")[0].strip()[:12] in text or "Walk-forward" in text

    def _wf_worse(_df, _cfg, _pair):
        return (
            {"pair": "EURUSD", "model": _m(pf=0.80, ret=-0.09, dd=-0.15, n=40)},
            None,
        )

    trained["n"] = 0
    kept = run_retrain_gate(
        "EURUSD",
        cfg,
        ohlcv=object(),
        walk_forward_fn=_wf_worse,
        train_fn=_train,
        persist=True,
    )
    assert kept["verdict"] == VERDICT_NULL
    assert kept["promote"] is False
    assert kept["champion_written"] is False
    assert kept["trained"] is False
    assert trained["n"] == 0
    still = load_champion("EURUSD", cfg)
    assert still["metrics"]["profit_factor"] == 0.96
    chal_path = tmp_path / "champion" / "EURUSD_challenger.json"
    assert chal_path.exists()
    chal = json.loads(chal_path.read_text(encoding="utf-8"))
    assert chal["verdict"] == "null"


def test_run_retrain_gate_seeds_without_claiming_improve(tmp_path):
    cfg = {
        "ui": {"timezone": "Asia/Dhaka"},
        "retrain": {
            "store": str(tmp_path / "champion"),
            "train_on_promote": False,
            "fail_soft": True,
        },
        "model": {"type": "xgboost"},
        "paths": {"models_dir": str(tmp_path / "models")},
    }
    seeded = run_retrain_gate(
        "EURUSD",
        cfg,
        challenger_metrics=_m(pf=0.978, ret=-0.0206, dd=-0.077, n=1066),
        dry_run=False,
        persist=True,
    )
    # dry_run False + no champion + metrics provided -> seed, no train
    assert seeded["verdict"] == VERDICT_SEED
    assert seeded["promote"] is False
    assert seeded["champion_written"] is True
    stored = load_champion("EURUSD", cfg)
    assert stored is not None
    assert stored["source"] == "seed"
    assert stored["live_edge"] is False


def test_dry_run_does_not_write_champion(tmp_path):
    cfg = {
        "ui": {"timezone": "Asia/Dhaka"},
        "retrain": {
            "store": str(tmp_path / "champion"),
            "train_on_promote": True,
            "fail_soft": True,
            "seed_from_metrics": True,
        },
        "model": {"type": "xgboost"},
        "paths": {"models_dir": str(tmp_path / "models")},
    }
    preview = run_retrain_gate(
        "EURUSD",
        cfg,
        challenger_metrics=_m(pf=0.978, ret=-0.0206, dd=-0.077, n=10),
        dry_run=True,
        persist=True,
    )
    assert preview["verdict"] == VERDICT_SEED
    assert preview["promote"] is False
    assert preview["champion_written"] is False
    assert preview["trained"] is False
    assert load_champion("EURUSD", cfg) is None
    text = format_retrain_text(preview)
    assert "dry-run" in text.lower()
    assert "not written" in text.lower()
    text.encode("ascii")

    save_champion("EURUSD", cfg, {"pair": "EURUSD", "metrics": _m(pf=0.90, ret=-0.05, dd=-0.10)})
    better = run_retrain_gate(
        "EURUSD",
        cfg,
        challenger_metrics=_m(pf=1.20, ret=0.05, dd=-0.04, n=40),
        dry_run=True,
        persist=True,
    )
    assert better["promote"] is True
    assert better["verdict"] == VERDICT_PROMOTE
    assert better["champion_written"] is False
    assert better["trained"] is False
    still = load_champion("EURUSD", cfg)
    assert still["metrics"]["profit_factor"] == 0.90
    assert "dry-run" in format_retrain_text(better).lower()


def test_fail_soft_walk_forward_error_keeps_champion(tmp_path):
    cfg = {
        "retrain": {
            "store": str(tmp_path / "champion"),
            "fail_soft": True,
            "train_on_promote": True,
        },
        "paths": {"models_dir": str(tmp_path / "models"), "data_dir": str(tmp_path / "data")},
        "model": {"type": "xgboost"},
    }
    save_champion("EURUSD", cfg, {"pair": "EURUSD", "metrics": _m(pf=0.9, ret=-0.02, dd=-0.08)})

    def _boom(*_a, **_k):
        raise RuntimeError("no folds")

    out = run_retrain_gate("EURUSD", cfg, ohlcv=object(), walk_forward_fn=_boom, persist=True)
    assert out["ok"] is False
    assert out["promote"] is False
    assert "walk-forward" in str(out["error"]).lower() or "no folds" in str(out["error"]).lower()
    assert load_champion("EURUSD", cfg)["metrics"]["profit_factor"] == 0.9


def test_retrain_module_does_not_import_broker():
    tree = ast.parse(Path("forex_lab/retrain.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any("broker" in name.split(".") for name in imported)
    from forex_lab.broker import BrokerPort

    assert sorted(BrokerPort.__abstractmethods__) == sorted(
        {"submit", "close", "list_positions", "list_fills"}
    )
