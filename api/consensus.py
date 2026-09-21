"""External-forecaster adapters for the Decision brief.

PR #18 (``cursor/desk-ux-hierarchy-1f34``) is a Streamlit desk overhaul and is
not merged. It does not ship a consensus module. Until a real adapter writes
``data/consensus_cache.json``, every source is **MISSING**.

This module never invents Buy/Sell calls or price ranges.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forex_lab.paths import resolve_under_root

HORIZONS = ("hourly", "daily")
DIRECTIONS = frozenset({"Buy", "Sell", "Neutral"})
DEFAULT_TTL_S = 6 * 3600
DEFAULT_CACHE = "data/consensus_cache.json"

# Names match the approved Decision mock. They are labels for adapters,
# not scraped results.
FORECASTERS: dict[str, tuple[str, ...]] = {
    "hourly": ("DailyForex", "Investing.com", "FXStreet"),
    "daily": ("DailyForex", "TradingView community", "ActionForex"),
}
RANGES: dict[str, tuple[str, ...]] = {
    "hourly": ("DailyForex", "Investing.com"),
    "daily": ("DailyForex", "ActionForex"),
}

NOTE = (
    "External forecasters are not scraped in this build. "
    "A cache hit is shown only when a real adapter wrote it; otherwise MISSING. "
    "Nothing here is invented."
)


class ConsensusError(ValueError):
    """Bad pair or horizon."""


def cache_file() -> Path:
    override = os.environ.get("FORX_CONSENSUS_CACHE")
    if override:
        return Path(override)
    return resolve_under_root(DEFAULT_CACHE)


def normalize_horizon(raw: str | None) -> str:
    key = str(raw or "").strip().lower()
    if key not in HORIZONS:
        raise ConsensusError("horizon must be hourly or daily")
    return key


def _utc_now(now: datetime | None = None) -> datetime:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc)


def _parse_fetched_at(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def load_cache(path: Path | None = None) -> dict[str, Any]:
    p = path or cache_file()
    if not p.exists() or p.stat().st_size <= 0:
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _ttl_s(cfg: dict[str, Any] | None) -> int:
    block = dict((cfg or {}).get("consensus") or {})
    try:
        ttl = int(block.get("cache_ttl_s") or DEFAULT_TTL_S)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_S
    return max(60, ttl)


def _direction(value: object) -> str | None:
    text = str(value or "").strip().lower()
    mapping = {"buy": "Buy", "sell": "Sell", "neutral": "Neutral"}
    return mapping.get(text)


def _num(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _pair_block(cache: dict[str, Any], pair: str, horizon: str) -> dict[str, Any] | None:
    node = cache.get(pair) or cache.get(pair.upper())
    if not isinstance(node, dict):
        return None
    block = node.get(horizon)
    return block if isinstance(block, dict) else None


def _index_by_source(rows: object) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("source") or row.get("site") or "").strip()
        if name:
            out[name] = row
    return out


def read_consensus(
    pair: str,
    horizon: str,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One horizon of other-forecaster directions and ranges.

    Cache records older than the TTL, or with a direction outside
    Buy/Sell/Neutral, are returned as MISSING.
    """
    pair_u = str(pair or "").strip().upper()
    hz = normalize_horizon(horizon)
    clock = _utc_now(now)
    payload = cache if cache is not None else load_cache()
    block = _pair_block(payload, pair_u, hz)
    fetched = _parse_fetched_at((block or {}).get("fetched_at"))
    age_s = None if fetched is None else (clock - fetched).total_seconds()
    fresh = fetched is not None and age_s is not None and age_s <= _ttl_s(cfg)
    reason = "no cache"
    if block is None:
        reason = "no cache"
    elif fetched is None:
        reason = "cache missing fetched_at"
    elif not fresh:
        reason = "cache stale"

    fc_rows = _index_by_source((block or {}).get("forecasters")) if fresh else {}
    rg_rows = _index_by_source((block or {}).get("ranges")) if fresh else {}

    forecasters: list[dict[str, Any]] = []
    ok_dirs = 0
    for name in FORECASTERS[hz]:
        row = fc_rows.get(name) or {}
        direction = _direction(row.get("direction") or row.get("bias"))
        if fresh and direction:
            ok_dirs += 1
            forecasters.append(
                {"source": name, "direction": direction, "status": "OK", "reason": ""}
            )
        else:
            why = reason if not fresh else "direction missing or not Buy/Sell/Neutral"
            forecasters.append(
                {"source": name, "direction": None, "status": "MISSING", "reason": why}
            )

    ranges: list[dict[str, Any]] = []
    ok_ranges = 0
    for name in RANGES[hz]:
        row = rg_rows.get(name) or {}
        low = _num(row.get("low"))
        high = _num(row.get("high"))
        window = str(row.get("window") or row.get("label") or "").strip()
        if fresh and low is not None and high is not None and high >= low:
            ok_ranges += 1
            ranges.append(
                {
                    "source": name,
                    "low": low,
                    "high": high,
                    "window": window,
                    "status": "OK",
                    "reason": "",
                }
            )
        else:
            why = reason if not fresh else "range missing or invalid"
            ranges.append(
                {
                    "source": name,
                    "low": None,
                    "high": None,
                    "window": None,
                    "status": "MISSING",
                    "reason": why,
                }
            )

    if ok_dirs == 0 and ok_ranges == 0:
        status = "MISSING"
    elif ok_dirs == len(forecasters) and ok_ranges == len(ranges):
        status = "OK"
    else:
        status = "PARTIAL"

    return {
        "pair": pair_u,
        "horizon": hz,
        "status": status,
        "fetched_at": None if fetched is None or not fresh else fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "forecasters": forecasters,
        "ranges": ranges,
        "note": NOTE,
    }


def lean_label(snapshot: dict[str, Any]) -> str:
    """Short bias line. MISSING when no cached OK directions — never a guessed lean."""
    dirs = [
        str(row.get("direction"))
        for row in snapshot.get("forecasters") or []
        if row.get("status") == "OK" and row.get("direction")
    ]
    if not dirs:
        return "external forecasters MISSING"
    buys = sum(1 for d in dirs if d == "Buy")
    sells = sum(1 for d in dirs if d == "Sell")
    if buys > sells and buys > 0:
        return f"consensus lean buy ({buys}/{len(dirs)} cached)"
    if sells > buys and sells > 0:
        return f"consensus lean sell ({sells}/{len(dirs)} cached)"
    return f"external forecasters mixed ({len(dirs)} cached)"
