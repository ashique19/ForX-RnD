"""Portfolio brain stub — acceptance criteria from design 2026-10-01."""
from __future__ import annotations

import numpy as np
import pandas as pd

from forex_lab.portfolio_brain import (
    NOTE_CORR_CLUSTER,
    NOTE_DD_BUDGET,
    NOTE_FAIL_SOFT,
    NOTE_MAX_CONCURRENT,
    book_drawdown_pct,
    check_open_intent,
    overlay_flash,
    portfolio_brain_enabled,
    rolling_return_corr,
    status_payload,
    usd_risk_direction,
)


def _cfg(**over):
    block = {
        "enabled": True,
        "fail_soft": True,
        "max_concurrent_opens": 2,
        "corr_bars": 60,
        "corr_threshold": 0.70,
        "corr_skip_pairs": ["BTCUSD"],
        "dd_budget_pct": -0.05,
        "dd_scale": False,
        "max_book_units": 2.0,
        "min_size": 0.25,
        "apply_to_paper": True,
        "apply_to_flash": True,
        "apply_to_replay": False,
    }
    block.update(over)
    return {"portfolio_brain": block, "broker": {"default_size": 1.0}}


def _closes(n: int = 200, seed: int = 0, drift: float = 0.0, vol: float = 0.001) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    rets = drift + vol * rng.standard_normal(n)
    price = 1.10 * np.exp(np.cumsum(rets))
    return pd.Series(price, index=idx, name="Close")


def test_usd_risk_direction_map():
    assert usd_risk_direction("EURUSD", "BUY") == -1
    assert usd_risk_direction("GBPUSD", "BUY") == -1
    assert usd_risk_direction("AUDUSD", "BUY") == -1
    assert usd_risk_direction("USDJPY", "BUY") == +1
    assert usd_risk_direction("USDCAD", "BUY") == +1
    # Invert for SELL
    assert usd_risk_direction("EURUSD", "SELL") == +1
    assert usd_risk_direction("USDJPY", "SELL") == -1
    # Unknown / bad
    assert usd_risk_direction("BTCUSD", "BUY") is None
    assert usd_risk_direction("EURUSD", "HOLD") is None


def test_enabled_false_is_noop():
    """AC1: enabled false => Decision/paper bit-identical (allow, no blocks)."""
    assert portfolio_brain_enabled({}) is False
    assert portfolio_brain_enabled({"portfolio_brain": {"enabled": False}}) is False
    opens = [
        {"pair": "EURUSD", "side": "BUY", "size": 1.0},
        {"pair": "USDJPY", "side": "BUY", "size": 1.0},
    ]
    d = check_open_intent(
        pair="GBPUSD",
        side="BUY",
        opens=opens,
        cfg={"portfolio_brain": {"enabled": False}},
        peer_closes={},
    )
    assert d.allowed is True
    assert d.scale == 1.0
    assert d.blocked_by == []
    assert d.notes == []
    assert overlay_flash("BUY", d, {"portfolio_brain": {"enabled": False}}) == "BUY"


def test_max_concurrent_blocks_third_open():
    """AC2: two opens already => third blocked; model flash overlay -> HOLD."""
    cfg = _cfg()
    opens = [
        {"pair": "EURUSD", "side": "BUY", "size": 1.0},
        {"pair": "USDJPY", "side": "SELL", "size": 1.0},
    ]
    d = check_open_intent(pair="GBPUSD", side="BUY", opens=opens, cfg=cfg, peer_closes={})
    assert d.allowed is False
    assert NOTE_MAX_CONCURRENT in d.blocked_by
    assert d.open_count == 2
    assert overlay_flash("BUY", d, cfg) == "HOLD"
    # Under cap still allowed (no corr peers forced)
    d2 = check_open_intent(
        pair="GBPUSD",
        side="BUY",
        opens=opens[:1],
        cfg=cfg,
        peer_closes={},
    )
    assert d2.allowed is True
    assert NOTE_MAX_CONCURRENT not in d2.blocked_by


def test_corr_cluster_same_usd_risk_blocks_opposite_allows():
    """AC3: long EURUSD + long GBPUSD high corr => block; short GBPUSD => allow."""
    cfg = _cfg(max_concurrent_opens=5)
    # Shared latent factor => high corr between EUR and GBP.
    base = _closes(n=200, seed=7, vol=0.0015)
    noise_e = _closes(n=200, seed=11, vol=0.0002)
    noise_g = _closes(n=200, seed=13, vol=0.0002)
    eurusd = base * (1.0 + 0.01 * (noise_e / noise_e.iloc[0] - 1.0))
    gbpusd = base * (1.0 + 0.01 * (noise_g / noise_g.iloc[0] - 1.0))
    corr = rolling_return_corr(eurusd, gbpusd, bars=60)
    assert corr is not None and abs(corr) >= 0.70

    opens = [{"pair": "EURUSD", "side": "BUY", "size": 1.0}]
    peers = {"EURUSD": eurusd, "GBPUSD": gbpusd}

    same = check_open_intent(
        pair="GBPUSD", side="BUY", opens=opens, cfg=cfg, peer_closes=peers
    )
    assert same.allowed is False
    assert NOTE_CORR_CLUSTER in same.blocked_by

    opposite = check_open_intent(
        pair="GBPUSD", side="SELL", opens=opens, cfg=cfg, peer_closes=peers
    )
    assert opposite.allowed is True
    assert NOTE_CORR_CLUSTER not in opposite.blocked_by


def test_missing_peer_csv_fail_soft_allows():
    """AC4: missing peer closes => allow + fail_soft note; never raises."""
    cfg = _cfg(max_concurrent_opens=5)
    opens = [{"pair": "EURUSD", "side": "BUY", "size": 1.0}]
    d = check_open_intent(
        pair="GBPUSD",
        side="BUY",
        opens=opens,
        cfg=cfg,
        peer_closes={"EURUSD": _closes(), "GBPUSD": None},  # type: ignore[dict-item]
    )
    # peer_closes with None value — treat as missing by omitting
    d = check_open_intent(
        pair="GBPUSD",
        side="BUY",
        opens=opens,
        cfg=cfg,
        peer_closes={"EURUSD": _closes()},  # GBPUSD missing
    )
    assert d.allowed is True
    assert NOTE_CORR_CLUSTER not in d.blocked_by
    assert any(NOTE_FAIL_SOFT in n for n in d.notes)


def test_dd_budget_hard_block():
    """AC5: book DD through budget => block new opens."""
    cfg = _cfg(max_concurrent_opens=5, dd_budget_pct=-0.05)
    opens = [
        {"pair": "EURUSD", "side": "BUY", "size": 1.0, "unrealized": -0.06},
    ]
    dd = book_drawdown_pct(opens, session_start_equity=1.0)
    assert dd is not None and dd <= -0.05
    d = check_open_intent(
        pair="USDJPY", side="BUY", opens=opens, cfg=cfg, peer_closes={}
    )
    assert d.allowed is False
    assert NOTE_DD_BUDGET in d.blocked_by


def test_status_payload_tooltip_and_defaults():
    payload = status_payload(cfg=_cfg(enabled=False), opens=[])
    assert payload["enabled"] is False
    assert "does not change Buy/Sell model" in payload["tooltip"]
    assert payload["apply_to_replay"] is False
    assert payload["open_count"] == 0
