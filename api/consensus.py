"""External-forecaster adapters for the Decision brief.

DailyForex, Investing.com, and FXStreet are fetched when the cache is stale.
Only a parsed bias and numeric levels are kept. Article text is not stored,
and a direction is never invented from pivots or an idle default.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forex_lab.paths import resolve_under_root

HORIZONS = ("hourly", "daily")
DIRECTIONS = frozenset({"Buy", "Sell", "Neutral"})
DEFAULT_TTL_S = 30 * 60
DEFAULT_CACHE = "data/consensus_cache.json"

FORECASTERS: dict[str, tuple[str, ...]] = {
    "hourly": ("DailyForex", "Investing.com", "FXStreet"),
    "daily": ("DailyForex", "Investing.com", "FXStreet"),
}
RANGES: dict[str, tuple[str, ...]] = {
    "hourly": ("DailyForex", "Investing.com"),
    "daily": ("DailyForex", "Investing.com"),
}

NOTE = (
    "Public pages are fetched for DailyForex, Investing.com, and FXStreet. "
    "A direction is shown only when that page states one. Nothing is invented."
)

_fetch_lock = threading.Lock()


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


def network_enabled() -> bool:
    raw = os.environ.get("FORX_CONSENSUS_NETWORK", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def save_cache(payload: dict[str, Any], path: Path | None = None) -> None:
    p = path or cache_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)


def _horizon_fresh(block: dict[str, Any] | None, cfg: dict[str, Any] | None, clock: datetime) -> bool:
    if not isinstance(block, dict):
        return False
    fetched = _parse_fetched_at(block.get("fetched_at"))
    if fetched is None:
        return False
    return (clock - fetched).total_seconds() <= _ttl_s(cfg)


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
        forecasters.append(_forecaster_view(name, row, fresh=fresh, stale_reason=reason))
        if forecasters[-1]["status"] == "OK":
            ok_dirs += 1

    ranges: list[dict[str, Any]] = []
    ok_ranges = 0
    for name in RANGES[hz]:
        row = rg_rows.get(name) or {}
        ranges.append(_range_view(name, row, fresh=fresh, stale_reason=reason))
        if ranges[-1]["status"] == "OK":
            ok_ranges += 1

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


def _forecaster_view(name: str, row: dict[str, Any], *, fresh: bool, stale_reason: str) -> dict[str, Any]:
    explicit = str(row.get("status") or "").upper()
    direction = _direction(row.get("direction") or row.get("bias"))
    url = str(row.get("url") or "")
    fetched = row.get("fetched_at")
    entry = _num(row.get("entry"))
    if not fresh:
        status, why, direction = "MISSING", stale_reason, None
    elif explicit == "ERROR":
        status, why, direction = "ERROR", str(row.get("reason") or "fetch failed"), None
    elif direction and explicit in {"", "OK"}:
        status, why = "OK", ""
    elif explicit == "MISSING":
        status, why, direction = "MISSING", str(row.get("reason") or "empty parse"), None
    else:
        status, why, direction = "MISSING", "direction missing or not Buy/Sell/Neutral", None
    return {
        "source": name,
        "direction": direction,
        "status": status,
        "reason": why,
        "url": url,
        "entry": entry if status == "OK" else None,
        "fetched_at": None if not fresh else (str(fetched) if fetched else None),
    }


def _range_view(name: str, row: dict[str, Any], *, fresh: bool, stale_reason: str) -> dict[str, Any]:
    explicit = str(row.get("status") or "").upper()
    low = _num(row.get("low"))
    high = _num(row.get("high"))
    window = str(row.get("window") or row.get("label") or "").strip()
    valid = low is not None and high is not None and high >= low
    if not fresh:
        status, why, low, high, window = "MISSING", stale_reason, None, None, None
    elif explicit == "ERROR":
        status, why, low, high, window = "ERROR", str(row.get("reason") or "fetch failed"), None, None, None
    elif valid and explicit in {"", "OK"}:
        status, why = "OK", ""
    elif explicit == "MISSING":
        status, why, low, high = "MISSING", str(row.get("reason") or "range missing or invalid"), None, None
        window = None
    else:
        status, why, low, high, window = "MISSING", "range missing or invalid", None, None, None
    return {
        "source": name,
        "low": low,
        "high": high,
        "window": window,
        "status": status,
        "reason": why,
    }


def ensure_consensus(pair: str, cfg: dict[str, Any] | None = None, *, now: datetime | None = None) -> None:
    """Refresh both horizons when the cache is older than the TTL. No-op if network is off."""
    if not network_enabled():
        return
    pair_u = str(pair or "").strip().upper()
    if not pair_u:
        return
    clock = _utc_now(now)
    with _fetch_lock:
        payload = load_cache()
        node = payload.get(pair_u) if isinstance(payload.get(pair_u), dict) else {}
        if _horizon_fresh(node.get("hourly"), cfg, clock) and _horizon_fresh(node.get("daily"), cfg, clock):
            return
        from api.consensus_fetch import fetch_pair_consensus

        try:
            fetched = fetch_pair_consensus(pair_u, now=clock)
        except Exception:
            stamp = clock.strftime("%Y-%m-%dT%H:%M:%SZ")
            fetched = {}
            for hz in HORIZONS:
                fetched[hz] = {
                    "fetched_at": stamp,
                    "forecasters": [
                        {
                            "source": name,
                            "direction": None,
                            "status": "ERROR",
                            "reason": "consensus fetch failed",
                            "url": "",
                            "entry": None,
                            "fetched_at": stamp,
                        }
                        for name in FORECASTERS[hz]
                    ],
                    "ranges": [
                        {
                            "source": name,
                            "low": None,
                            "high": None,
                            "window": "",
                            "status": "ERROR",
                            "reason": "consensus fetch failed",
                            "fetched_at": stamp,
                        }
                        for name in RANGES[hz]
                    ],
                }
        payload[pair_u] = fetched
        save_cache(payload)


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
