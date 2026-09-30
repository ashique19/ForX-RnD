"""Unit tests for cross-pair USD disagreement veto (default OFF)."""
from __future__ import annotations

from forex_lab.cross_pair_veto import (
    evaluate_cross_pair_veto,
    usd_risk_for_side,
    cross_pair_veto_report_line,
)


def test_usd_risk_map_matches_portfolio_brain():
    assert usd_risk_for_side("EURUSD", "BUY") == -1
    assert usd_risk_for_side("EURUSD", "SELL") == +1
    assert usd_risk_for_side("USDJPY", "BUY") == +1
    assert usd_risk_for_side("USDJPY", "SELL") == -1
    assert usd_risk_for_side("BTCUSD", "BUY") is None


def test_disabled_is_noop():
    cfg = {"cross_pair_veto": {"enabled": False}}
    peers = [
        {"pair": "USDJPY", "side": "BUY", "confidence": 0.9},
        {"pair": "AUDUSD", "side": "SELL", "confidence": 0.9},
    ]
    d = evaluate_cross_pair_veto(
        active_pair="EURUSD", active_side="BUY", peer_flashes=peers, cfg=cfg
    )
    assert d.allowed is True
    assert d.blocked_by == []


def test_veto_when_two_peers_disagree():
    # EURUSD BUY => usd_risk -1 (short USD). Peers that are long-USD disagree.
    cfg = {
        "cross_pair_veto": {
            "enabled": True,
            "min_peer_conf": 0.70,
            "min_disagree_peers": 2,
        }
    }
    peers = [
        {"pair": "USDJPY", "side": "BUY", "confidence": 0.80},   # +1 disagree
        {"pair": "AUDUSD", "side": "SELL", "confidence": 0.75},  # +1 disagree (AUDUSD SELL = long USD)
        {"pair": "GBPUSD", "side": "BUY", "confidence": 0.90},   # -1 agree
    ]
    d = evaluate_cross_pair_veto(
        active_pair="EURUSD", active_side="BUY", peer_flashes=peers, cfg=cfg
    )
    assert d.allowed is False
    assert "cross_pair:usd_disagree" in d.blocked_by
    assert set(d.disagree_peers) == {"USDJPY", "AUDUSD"}
    assert d.agree_peers == ["GBPUSD"]


def test_low_conf_peers_ignored():
    cfg = {"cross_pair_veto": {"enabled": True, "min_peer_conf": 0.70, "min_disagree_peers": 2}}
    peers = [
        {"pair": "USDJPY", "side": "BUY", "confidence": 0.50},
        {"pair": "AUDUSD", "side": "SELL", "confidence": 0.55},
    ]
    d = evaluate_cross_pair_veto(
        active_pair="EURUSD", active_side="BUY", peer_flashes=peers, cfg=cfg
    )
    assert d.allowed is True
    assert d.disagree_peers == []


def test_report_line_off_by_default():
    line = cross_pair_veto_report_line({})
    assert "cross_pair_veto: off" in line
