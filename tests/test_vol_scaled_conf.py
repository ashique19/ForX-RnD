"""Unit tests for signals.vol_scaled_conf (threshold scaling, not size)."""
from __future__ import annotations

import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.features import LABEL_MAP
from forex_lab.model import apply_signal_filters
from forex_lab.vol_scaled_conf import (
    apply_replay_vol_scaled_conf,
    effective_min_confidence_series,
    vol_scaled_conf_enabled,
)


def _cfg_on() -> dict:
    cfg = load_config()
    sig = dict(cfg.get("signals") or {})
    vsc = dict(sig.get("vol_scaled_conf") or {})
    vsc["enabled"] = True
    sig["vol_scaled_conf"] = vsc
    cfg["signals"] = sig
    gates = dict(cfg.get("gates") or {})
    gates["enabled"] = False
    cfg["gates"] = gates
    apply_replay_vol_scaled_conf(cfg)
    return cfg


def test_default_disabled():
    cfg = load_config()
    assert not vol_scaled_conf_enabled(cfg)


def test_effective_clip_range():
    cfg = _cfg_on()
    df = pd.DataFrame(
        {"confidence": [0.55, 0.62, 0.70], "atr_pctile": [0.10, 0.50, 0.95]}
    )
    eff = effective_min_confidence_series(df, cfg, base_min_conf=0.60)
    assert eff is not None
    assert abs(float(eff.iloc[0]) - 0.54) < 1e-9
    assert abs(float(eff.iloc[1]) - 0.60) < 1e-9
    assert abs(float(eff.iloc[2]) - 0.69) < 1e-9


def test_filter_eases_quiet_tightens_loud():
    cfg = _cfg_on()
    pred = pd.DataFrame(
        {
            "pred": [LABEL_MAP["BUY"]] * 3,
            "pred_raw": [LABEL_MAP["BUY"]] * 3,
            "confidence": [0.55, 0.62, 0.65],
            "atr_pctile": [0.10, 0.50, 0.95],
            "dir_edge": [0.2, 0.2, 0.2],
        }
    )
    out = apply_signal_filters(pred, cfg)
    assert list(out) == [LABEL_MAP["BUY"], LABEL_MAP["BUY"], LABEL_MAP["HOLD"]]
    # static floor would kill 0.55
    cfg_off = load_config()
    g = dict(cfg_off.get("gates") or {})
    g["enabled"] = False
    cfg_off["gates"] = g
    out_off = apply_signal_filters(pred, cfg_off)
    assert out_off.iloc[0] == LABEL_MAP["HOLD"]
