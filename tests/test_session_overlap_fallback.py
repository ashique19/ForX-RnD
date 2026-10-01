"""Session-gate overlap enforcement: sess_ldn_ny fallback via london&ny."""
from __future__ import annotations

import pandas as pd

from forex_lab.model import apply_signal_filters


def test_session_ldn_ny_fallback_without_sess_ldn_ny_column():
    # LABEL_MAP: SELL=0 HOLD=1 BUY=2
    df = pd.DataFrame(
        {
            "pred": [2, 2, 0, 1],
            "confidence": [0.7, 0.7, 0.7, 0.7],
            "dir_edge": [0.1, 0.1, 0.1, 0.1],
            "sess_london": [1.0, 1.0, 0.0, 1.0],
            "sess_ny": [1.0, 0.0, 1.0, 0.0],
        }
    )
    cfg = {
        "signals": {"min_confidence": 0.6, "sessions": ["ldn_ny"]},
        "gates": {"enabled": False},
    }
    out = apply_signal_filters(df, cfg)
    assert out.tolist() == [2, 1, 1, 1]


def test_session_ldn_ny_uses_explicit_column_when_present():
    df = pd.DataFrame(
        {
            "pred": [2, 2, 0, 1],
            "confidence": [0.7, 0.7, 0.7, 0.7],
            "dir_edge": [0.1, 0.1, 0.1, 0.1],
            "sess_london": [1.0, 1.0, 0.0, 1.0],
            "sess_ny": [1.0, 0.0, 1.0, 0.0],
            "sess_ldn_ny": [1.0, 0.0, 0.0, 0.0],
        }
    )
    cfg = {
        "signals": {"min_confidence": 0.6, "sessions": ["ldn_ny"]},
        "gates": {"enabled": False},
    }
    out = apply_signal_filters(df, cfg)
    assert out.tolist() == [2, 1, 1, 1]
