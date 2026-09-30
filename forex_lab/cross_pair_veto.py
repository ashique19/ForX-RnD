"""Cross-pair USD-risk disagreement veto (research overlay).

Design note: `_SKETCH_CROSS_PAIR_DISAGREE_VETO_20261001.txt`.

When Active is about to open, peek peer flash sides. Map each to USD-risk
using the same `_USD_RISK_LONG` map as portfolio_brain. If enough peers
disagree (opposite USD-risk sign) at conf >= min_peer_conf, veto -> HOLD.

Default OFF = pure no-op. Not wired into Replay/live until a written A/B.
Does NOT raise min_confidence. Does NOT enable portfolio_brain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from forex_lab.portfolio_brain import _USD_RISK_LONG, normalize_pair

NOTE_DISAGREE = "cross_pair:usd_disagree"
NOTE_FAIL_SOFT = "cross_pair:fail_soft"

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "fail_soft": True,
    "min_peer_conf": 0.70,
    "min_disagree_peers": 2,
    "peers": ["USDJPY", "AUDUSD", "GBPUSD", "NZDUSD", "USDCAD"],
    "skip_pairs": ["BTCUSD"],
}


@dataclass
class CrossPairDecision:
    allowed: bool = True
    blocked_by: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    disagree_peers: list[str] = field(default_factory=list)
    agree_peers: list[str] = field(default_factory=list)
    ignored_peers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "blocked_by": list(self.blocked_by),
            "notes": list(self.notes),
            "disagree_peers": list(self.disagree_peers),
            "agree_peers": list(self.agree_peers),
            "ignored_peers": list(self.ignored_peers),
        }


def cross_pair_veto_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("cross_pair_veto") or {})
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if v is not None})
    return out


def cross_pair_veto_enabled(cfg: dict[str, Any] | None) -> bool:
    return bool(cross_pair_veto_cfg(cfg).get("enabled"))


def usd_risk_for_side(pair: object, side: object) -> int | None:
    """+1 long-USD, -1 short-USD, None if unknown pair/side."""
    p = normalize_pair(pair)
    base = _USD_RISK_LONG.get(p)
    if base is None:
        return None
    s = str(side or "").strip().upper()
    if s in {"BUY", "LONG"}:
        return int(base)
    if s in {"SELL", "SHORT"}:
        return int(-base)
    return None


def evaluate_cross_pair_veto(
    *,
    active_pair: object,
    active_side: object,
    peer_flashes: Sequence[Mapping[str, Any]] | None,
    cfg: dict[str, Any] | None = None,
) -> CrossPairDecision:
    """Return allow/block for Active open given peer flash rows.

    peer_flashes items: {pair, side, confidence} (extra keys ignored).
    HOLD / missing / low-conf peers are ignored (fail-soft).
    """
    decision = CrossPairDecision()
    block = cross_pair_veto_cfg(cfg)
    if not bool(block.get("enabled")):
        return decision

    intent = usd_risk_for_side(active_pair, active_side)
    if intent is None:
        if block.get("fail_soft", True):
            decision.notes.append(f"{NOTE_FAIL_SOFT}: active usd_risk unknown")
            return decision
        decision.allowed = False
        decision.blocked_by.append(NOTE_FAIL_SOFT)
        decision.notes.append(f"{NOTE_FAIL_SOFT}: active usd_risk unknown (strict)")
        return decision

    try:
        min_conf = float(block.get("min_peer_conf") or 0.70)
    except (TypeError, ValueError):
        min_conf = 0.70
    try:
        need = int(block.get("min_disagree_peers") or 2)
    except (TypeError, ValueError):
        need = 2
    need = max(1, need)

    peer_allow = {
        normalize_pair(p)
        for p in (block.get("peers") or DEFAULTS["peers"])
        if p
    }
    skip = {
        normalize_pair(p)
        for p in (block.get("skip_pairs") or DEFAULTS["skip_pairs"])
        if p
    }
    active_u = normalize_pair(active_pair)

    for row in peer_flashes or []:
        if not isinstance(row, Mapping):
            continue
        peer = normalize_pair(row.get("pair"))
        if not peer or peer == active_u:
            continue
        if peer in skip or (peer_allow and peer not in peer_allow):
            decision.ignored_peers.append(peer)
            continue
        side = row.get("side") or row.get("model_signal")
        try:
            conf = float(row.get("confidence") if row.get("confidence") is not None else 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < min_conf:
            decision.ignored_peers.append(peer)
            continue
        risk = usd_risk_for_side(peer, side)
        if risk is None:
            decision.ignored_peers.append(peer)
            continue
        if risk == intent:
            decision.agree_peers.append(peer)
        elif risk == -intent:
            decision.disagree_peers.append(peer)
        else:
            decision.ignored_peers.append(peer)

    if len(decision.disagree_peers) >= need:
        decision.allowed = False
        decision.blocked_by.append(NOTE_DISAGREE)
        decision.notes.append(
            f"{NOTE_DISAGREE}: {len(decision.disagree_peers)} peers opposite "
            f"usd_risk (need>={need}): {','.join(decision.disagree_peers)}"
        )
    return decision


def cross_pair_veto_report_line(cfg: dict[str, Any] | None) -> str:
    block = cross_pair_veto_cfg(cfg)
    on = "on" if bool(block.get("enabled")) else "off"
    return (
        f"cross_pair_veto: {on} | min_peer_conf={float(block.get('min_peer_conf') or 0.7):g} "
        f"| min_disagree_peers={int(block.get('min_disagree_peers') or 2)} "
        f"| peers={len(block.get('peers') or [])}"
    )
