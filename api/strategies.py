"""Paper strategies. Each one only restates a number the desk already has.

Brief uses the model suggestion. Consensus uses the share of cached
forecasters on the majority side. MTF agree reuses that same model
confidence when the higher-timeframe badge agrees with the side.
No slope, vote, or confidence is invented.
"""
from __future__ import annotations

from typing import Any

from forex_lab.mtf import MTF_AGREE
from forex_lab.ui.board import conf_label

BRIEF = "brief"
CONSENSUS = "consensus"
MTF = "mtf"

STRATEGIES: tuple[dict[str, str], ...] = (
    {"id": BRIEF, "name": "Brief"},
    {"id": CONSENSUS, "name": "Consensus"},
    {"id": MTF, "name": "MTF agree"},
)
STRATEGY_IDS = {item["id"] for item in STRATEGIES}


def strategy_name(strategy_id: str | None) -> str:
    sid = str(strategy_id or BRIEF)
    for item in STRATEGIES:
        if item["id"] == sid:
            return item["name"]
    return "Brief"


def normalize_champion(value: object) -> str:
    sid = str(value or BRIEF).strip().lower()
    return sid if sid in STRATEGY_IDS else BRIEF


def book_id(row: dict[str, Any] | None) -> str:
    """Rows written before strategies belong to the Brief book."""
    if not isinstance(row, dict):
        return BRIEF
    sid = str(row.get("strategy_id") or BRIEF)
    return sid if sid in STRATEGY_IDS else BRIEF


def strategy_views(row: Any, cfg: dict[str, Any], suggestion: dict[str, Any]) -> list[dict[str, Any]]:
    signal = str(suggestion.get("signal") or "").upper()
    live = signal if signal in {"BUY", "SELL"} else None
    model_conf = suggestion.get("confidence")
    if model_conf is None:
        model_conf = getattr(row, "confidence", None)
    pair = str(getattr(row, "pair", "") or "")
    return [
        _view(BRIEF, live, model_conf),
        _view(CONSENSUS, *_consensus_side(pair, cfg)),
        _view(MTF, *_mtf_side(row, live, model_conf)),
    ]


def champion_view(
    row: Any,
    cfg: dict[str, Any],
    suggestion: dict[str, Any],
    champion_id: str,
) -> dict[str, Any]:
    sid = normalize_champion(champion_id)
    for view in strategy_views(row, cfg, suggestion):
        if view["id"] == sid:
            return view
    return _view(BRIEF, None, None)


def _view(strategy_id: str, signal: str | None, confidence: object) -> dict[str, Any]:
    side = signal if signal in {"BUY", "SELL"} else None
    try:
        conf = None if confidence is None else float(confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        conf = None
    if conf is not None and conf != conf:
        conf = None
    return {
        "id": strategy_id,
        "name": strategy_name(strategy_id),
        "signal": side,
        "confidence": conf,
        "confidence_text": conf_label(conf),
    }


def _consensus_side(pair: str, cfg: dict[str, Any]) -> tuple[str | None, float | None]:
    """Majority of cached OK directions. Confidence is that share, not a model score."""
    from api.consensus import read_consensus

    try:
        snap = read_consensus(pair, "hourly", cfg)
    except Exception:
        return None, None
    dirs = [
        str(row.get("direction"))
        for row in snap.get("forecasters") or []
        if row.get("status") == "OK" and row.get("direction")
    ]
    if not dirs:
        return None, None
    buys = sum(1 for item in dirs if item == "Buy")
    sells = sum(1 for item in dirs if item == "Sell")
    total = len(dirs)
    if buys > sells and buys * 2 > total:
        return "BUY", buys / total
    if sells > buys and sells * 2 > total:
        return "SELL", sells / total
    return None, None


def _mtf_side(row: Any, live: str | None, model_conf: object) -> tuple[str | None, float | None]:
    """Model side and its confidence, only when the HTF badge agrees. Otherwise no call."""
    mtf = getattr(row, "mtf", None)
    if mtf is None or getattr(mtf, "status", None) != MTF_AGREE or live not in {"BUY", "SELL"}:
        return None, None
    direction = str(getattr(mtf, "direction", "") or "")
    if live == "BUY" and direction == "up":
        return "BUY", model_conf
    if live == "SELL" and direction == "down":
        return "SELL", model_conf
    return None, None
