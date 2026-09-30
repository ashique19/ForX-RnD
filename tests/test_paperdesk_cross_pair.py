"""Paperdesk cross_pair_veto wire stays OFF by default."""
from __future__ import annotations

from api.paperdesk import _cross_pair_open_check


def test_cross_pair_open_check_noop_when_disabled():
    cfg = {"cross_pair_veto": {"enabled": False}}
    assert _cross_pair_open_check(cfg, pair="EURUSD", side="BUY") is None


def test_cross_pair_open_check_evaluates_when_enabled(monkeypatch):
    cfg = {
        "cross_pair_veto": {
            "enabled": True,
            "min_peer_conf": 0.70,
            "min_disagree_peers": 2,
            "peers": ["USDJPY", "AUDUSD", "GBPUSD"],
        }
    }

    def fake_peers(cfg_arg, active):
        return [
            {"pair": "USDJPY", "side": "BUY", "confidence": 0.80},
            {"pair": "AUDUSD", "side": "SELL", "confidence": 0.75},
        ]

    monkeypatch.setattr("api.paperdesk._peer_flashes_for_veto", fake_peers)
    d = _cross_pair_open_check(cfg, pair="EURUSD", side="BUY")
    assert d is not None
    assert d.allowed is False
    assert "cross_pair:usd_disagree" in d.blocked_by


def test_cross_pair_open_check_allows_when_peers_agree(monkeypatch):
    cfg = {
        "cross_pair_veto": {
            "enabled": True,
            "min_peer_conf": 0.70,
            "min_disagree_peers": 2,
            "peers": ["USDJPY", "AUDUSD"],
        }
    }

    def fake_peers(cfg_arg, active):
        # EURUSD BUY = short USD; peers also short USD => agree
        return [
            {"pair": "USDJPY", "side": "SELL", "confidence": 0.80},
            {"pair": "AUDUSD", "side": "BUY", "confidence": 0.80},
        ]

    monkeypatch.setattr("api.paperdesk._peer_flashes_for_veto", fake_peers)
    d = _cross_pair_open_check(cfg, pair="EURUSD", side="BUY")
    assert d is not None
    assert d.allowed is True
