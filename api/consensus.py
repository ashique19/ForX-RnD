"""External-forecaster cache for the Decision brief and the Lab drawer.

Live sources are fetched on a background thread when the cache is older than
the TTL. The read path never waits on the network. A direction is shown only
when a source stated one. Last-success rows stay visible after the TTL, with
age in Asia/Dhaka, instead of being wiped to a blank MISSING.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forex_lab.clock import fmt_display
from forex_lab.paths import resolve_under_root
from forex_lab.ui.watchlist import WatchlistError

from api.consensus_registry import (
    REGISTRY,
    SourceSpec,
    aggregate_consensus,
    live_sources,
    pair_forms,
    range_sources,
)

HORIZONS = ("hourly", "daily")
DIRECTIONS = frozenset({"Buy", "Sell", "Neutral"})
DEFAULT_TTL_S = 30 * 60
DEFAULT_CACHE = "data/consensus_cache.json"

NOTE = (
    "Directions and ranges are copied from each source. "
    "Confidence is agreement among published Buy and Sell calls (at least two). "
    "Nothing is filled in when a source is silent."
)

_fetch_lock = threading.Lock()
_state_lock = threading.Lock()
_queued: set[str] = set()
_queue: list[str] = []
_worker_on = False
_active: str | None = None


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


def reset_consensus_state() -> None:
    """Drop the in-process fetch queue. Tests only."""
    global _worker_on, _active
    with _state_lock:
        _queued.clear()
        _queue.clear()
        _worker_on = False
        _active = None


def consensus_pending(pair: str) -> bool:
    key = str(pair or "").strip().upper()
    with _state_lock:
        return key in _queued


def _spec_by_name() -> dict[str, SourceSpec]:
    return {spec.name: spec for spec in REGISTRY}


def _canonical_pair(pair: str) -> str:
    try:
        return pair_forms(pair)["pair"]
    except WatchlistError:
        return str(pair or "").strip().upper()


def read_consensus(
    pair: str,
    horizon: str,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One horizon of other-forecaster directions and ranges.

    A stale cache still returns the last published direction and range, with
    ``fresh`` false and a Dhaka timestamp. Invalid directions stay MISSING.
    """
    pair_u = _canonical_pair(pair)
    hz = normalize_horizon(horizon)
    clock = _utc_now(now)
    payload = cache if cache is not None else load_cache()
    block = _pair_block(payload, pair_u, hz)
    fetched = _parse_fetched_at((block or {}).get("fetched_at"))
    age_s = None if fetched is None else max(0.0, (clock - fetched).total_seconds())
    fresh = fetched is not None and age_s is not None and age_s <= _ttl_s(cfg)
    have_block = block is not None
    pending = consensus_pending(pair_u)
    if not have_block:
        reason = "fetch in progress" if pending else "no cache yet"
    elif fetched is None:
        reason = "cache missing fetched_at"
    else:
        reason = ""

    fc_rows = _index_by_source((block or {}).get("forecasters")) if have_block else {}
    rg_rows = _index_by_source((block or {}).get("ranges")) if have_block else {}
    specs = _spec_by_name()

    forecasters: list[dict[str, Any]] = []
    for spec in REGISTRY:
        if not spec.live:
            forecasters.append(_skipped_view(spec))
            continue
        row = fc_rows.get(spec.name) or {}
        forecasters.append(
            _forecaster_view(
                spec.name,
                row,
                have_block=have_block,
                empty_reason=reason,
                tier=spec.tier,
                cfg=cfg,
                fetched_fallback=(block or {}).get("fetched_at"),
            )
        )

    ranges: list[dict[str, Any]] = []
    for spec in range_sources():
        row = rg_rows.get(spec.name) or {}
        ranges.append(
            _range_view(
                spec.name,
                row,
                have_block=have_block,
                empty_reason=reason,
                tier=spec.tier,
                cfg=cfg,
                fetched_fallback=(block or {}).get("fetched_at"),
            )
        )

    ok_dirs = sum(1 for row in forecasters if row["status"] == "OK")
    ok_ranges = sum(1 for row in ranges if row["status"] == "OK")
    actionable = [row for row in forecasters if row["status"] not in {"SKIPPED", "RANGE"}]
    if ok_dirs == 0 and ok_ranges == 0:
        status = "MISSING"
    elif actionable and all(row["status"] == "OK" for row in actionable) and all(row["status"] == "OK" for row in ranges):
        status = "OK"
    else:
        status = "PARTIAL"

    aggregate = aggregate_consensus(forecasters, ranges)
    dhaka = None if fetched is None else fmt_display(fetched, cfg, seconds=False)
    return {
        "pair": pair_u,
        "horizon": hz,
        "status": status,
        "fresh": fresh,
        "stale": bool(fetched is not None and not fresh),
        "pending": pending,
        "age_s": None if age_s is None else int(age_s),
        "fetched_at": None if fetched is None else fetched.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fetched_at_dhaka": dhaka,
        "forecasters": forecasters,
        "ranges": ranges,
        "aggregate": aggregate,
        "note": NOTE,
        "sources": len(specs),
    }


def _public_reason(status: str, raw: object) -> str:
    """Non-OK rows always carry a reason. A blank MISSING is an empty parse."""
    text = " ".join(str(raw or "").split())
    if status == "OK":
        return text
    if not text:
        return {"ERROR": "fetch failed", "SKIPPED": "not requested", "RANGE": "range only"}.get(status, "empty parse")
    low = text.lower()
    if "blocked by bot check" in low:
        return "bot check"
    return text


def _last_ok_fields(
    status: str,
    row: dict[str, Any],
    fetched: object,
    cfg: dict[str, Any] | None,
    *,
    fallback: object = None,
) -> tuple[str | None, str | None]:
    stamp = row.get("last_ok_at")
    if status == "OK" and not stamp:
        stamp = fetched or fallback
    parsed = _parse_fetched_at(stamp)
    if parsed is None:
        return None, None
    iso = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
    return iso, fmt_display(parsed, cfg, seconds=False)


def _skipped_view(spec: SourceSpec) -> dict[str, Any]:
    return {
        "source": spec.name,
        "direction": None,
        "status": "SKIPPED",
        "reason": _public_reason("SKIPPED", spec.skip_reason),
        "url": "",
        "entry": None,
        "fetched_at": None,
        "last_ok_at": None,
        "last_ok_at_dhaka": None,
        "tier": spec.tier,
    }


def _forecaster_view(
    name: str,
    row: dict[str, Any],
    *,
    have_block: bool,
    empty_reason: str,
    tier: str,
    cfg: dict[str, Any] | None = None,
    fetched_fallback: object = None,
) -> dict[str, Any]:
    explicit = str(row.get("status") or "").upper()
    direction = _direction(row.get("direction") or row.get("bias"))
    url = str(row.get("url") or "")
    fetched = row.get("fetched_at")
    entry = _num(row.get("entry"))
    if not have_block or not row:
        status, why, direction = "MISSING", empty_reason or "not in last fetch", None
    elif explicit == "ERROR":
        status, why, direction = "ERROR", str(row.get("reason") or "fetch failed"), None
    elif explicit == "RANGE":
        status, why, direction = "RANGE", str(row.get("reason") or "range only"), None
    elif explicit == "SKIPPED":
        status, why, direction = "SKIPPED", str(row.get("reason") or "skipped"), None
    elif direction and explicit in {"", "OK"}:
        status, why = "OK", str(row.get("reason") or "")
    elif explicit == "MISSING":
        status, why, direction = "MISSING", str(row.get("reason") or ""), None
    else:
        status, why, direction = "MISSING", "direction missing or not Buy/Sell/Neutral", None
    why = _public_reason(status, why)
    last_ok_at, last_ok_dhaka = _last_ok_fields(status, row, fetched, cfg, fallback=fetched_fallback)
    return {
        "source": name,
        "direction": direction,
        "status": status,
        "reason": why,
        "url": url,
        "entry": entry if status == "OK" else None,
        "fetched_at": None if not have_block else (str(fetched) if fetched else None),
        "last_ok_at": last_ok_at,
        "last_ok_at_dhaka": last_ok_dhaka,
        "tier": tier,
    }


def _range_view(
    name: str,
    row: dict[str, Any],
    *,
    have_block: bool,
    empty_reason: str,
    tier: str,
    cfg: dict[str, Any] | None = None,
    fetched_fallback: object = None,
) -> dict[str, Any]:
    explicit = str(row.get("status") or "").upper()
    low = _num(row.get("low"))
    high = _num(row.get("high"))
    window = str(row.get("window") or row.get("label") or "").strip()
    valid = low is not None and high is not None and high >= low
    if not have_block or not row:
        status, why, low, high, window = "MISSING", empty_reason or "not in last fetch", None, None, None
    elif explicit == "ERROR":
        status, why, low, high, window = "ERROR", str(row.get("reason") or "fetch failed"), None, None, None
    elif valid and explicit in {"", "OK"}:
        status, why = "OK", ""
    elif explicit == "MISSING":
        status, why, low, high = "MISSING", str(row.get("reason") or ""), None, None
        window = None
    else:
        status, why, low, high, window = "MISSING", "range missing or invalid", None, None, None
    why = _public_reason(status, why)
    _last_ok_at, last_ok_dhaka = _last_ok_fields(
        status, row, row.get("fetched_at"), cfg, fallback=fetched_fallback
    )
    return {
        "source": name,
        "low": low,
        "high": high,
        "window": window,
        "status": status,
        "reason": why,
        "last_ok_at_dhaka": last_ok_dhaka,
        "tier": tier,
    }


def _pair_is_fresh(pair_u: str, cfg: dict[str, Any] | None, clock: datetime) -> bool:
    with _fetch_lock:
        payload = load_cache()
    node = payload.get(pair_u) if isinstance(payload.get(pair_u), dict) else {}
    return _horizon_fresh(node.get("hourly"), cfg, clock) and _horizon_fresh(node.get("daily"), cfg, clock)


def _error_payload(stamp: str) -> dict[str, Any]:
    fetched: dict[str, Any] = {}
    for hz in HORIZONS:
        fetched[hz] = {
            "fetched_at": stamp,
            "forecasters": [
                {
                    "source": spec.name,
                    "direction": None,
                    "status": "ERROR",
                    "reason": "consensus fetch failed",
                    "url": "",
                    "entry": None,
                    "fetched_at": stamp,
                }
                for spec in live_sources()
            ],
            "ranges": [
                {
                    "source": spec.name,
                    "low": None,
                    "high": None,
                    "window": "",
                    "status": "ERROR",
                    "reason": "consensus fetch failed",
                    "fetched_at": stamp,
                }
                for spec in range_sources()
            ],
        }
    return fetched


def _prior_ok_stamp(prior: dict[str, Any] | None) -> str | None:
    if not isinstance(prior, dict):
        return None
    if str(prior.get("status") or "").upper() == "OK" and prior.get("fetched_at"):
        return str(prior.get("fetched_at"))
    if prior.get("last_ok_at"):
        return str(prior.get("last_ok_at"))
    return None


def _apply_last_ok(row: dict[str, Any], prior: dict[str, Any] | None) -> None:
    status = str(row.get("status") or "").upper()
    if status == "OK" and row.get("fetched_at"):
        row["last_ok_at"] = str(row.get("fetched_at"))
        return
    row["last_ok_at"] = _prior_ok_stamp(prior)


def carry_last_success(previous: dict[str, Any] | None, fetched: dict[str, Any]) -> None:
    """Keep each source's last OK time when a later fetch fails. Does not keep the old side."""
    prev = previous if isinstance(previous, dict) else {}
    for hz in HORIZONS:
        old_node = prev.get(hz) if isinstance(prev.get(hz), dict) else {}
        new_node = fetched.get(hz) if isinstance(fetched.get(hz), dict) else {}
        old_fc = _index_by_source(old_node.get("forecasters"))
        for row in new_node.get("forecasters") or []:
            if isinstance(row, dict):
                _apply_last_ok(row, old_fc.get(str(row.get("source") or "")))
        old_rg = _index_by_source(old_node.get("ranges"))
        for row in new_node.get("ranges") or []:
            if isinstance(row, dict):
                _apply_last_ok(row, old_rg.get(str(row.get("source") or "")))


def _refresh_one(pair_u: str) -> None:
    clock = _utc_now(None)
    stamp = clock.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        from api.consensus_registry import fetch_registered

        fetched = fetch_registered(pair_u, now=clock)
    except Exception:
        fetched = _error_payload(stamp)
    with _fetch_lock:
        payload = load_cache()
        carry_last_success(payload.get(pair_u) if isinstance(payload.get(pair_u), dict) else None, fetched)
        payload[pair_u] = fetched
        save_cache(payload)


def _drain_queue() -> None:
    global _worker_on
    while True:
        with _state_lock:
            if not _queue:
                _worker_on = False
                return
            pair_u = _queue.pop(0)
            if pair_u != _active:
                _queued.discard(pair_u)
                continue
        try:
            _refresh_one(pair_u)
        finally:
            with _state_lock:
                _queued.discard(pair_u)


def ensure_consensus(pair: str, cfg: dict[str, Any] | None = None, *, now: datetime | None = None) -> None:
    """Queue a background refresh for this Active pair. Returns immediately.

    The latest call replaces any pair still waiting, so a watchlist of many
    symbols does not turn into a scrape of every symbol. A fetch already in
    flight is left to finish; the next start is only the Active pair. A fresh
    cache is left alone. Network off (``FORX_CONSENSUS_NETWORK=0``) is a no-op.
    """
    global _worker_on, _active
    if not network_enabled():
        return
    pair_u = _canonical_pair(pair)
    if not pair_u:
        return
    clock = _utc_now(now)
    if _pair_is_fresh(pair_u, cfg, clock):
        with _state_lock:
            _active = pair_u
            for waiting in list(_queue):
                _queue.remove(waiting)
                _queued.discard(waiting)
        return
    start = False
    with _state_lock:
        _active = pair_u
        for waiting in list(_queue):
            if waiting == pair_u:
                continue
            _queue.remove(waiting)
            _queued.discard(waiting)
        if pair_u not in _queued:
            _queued.add(pair_u)
            _queue.append(pair_u)
            if not _worker_on:
                _worker_on = True
                start = True
    if start:
        threading.Thread(target=_drain_queue, name="consensus-refresh", daemon=True).start()


def lean_label(snapshot: dict[str, Any]) -> str:
    """Short bias line. MISSING when no cached OK directions — never a guessed lean."""
    agg = snapshot.get("aggregate") if isinstance(snapshot.get("aggregate"), dict) else {}
    counts = agg.get("counts") if isinstance(agg.get("counts"), dict) else None
    if counts:
        buys = int(counts.get("Buy") or 0)
        sells = int(counts.get("Sell") or 0)
        neutrals = int(counts.get("Neutral") or 0)
        total = buys + sells + neutrals
    else:
        dirs = [
            str(row.get("direction"))
            for row in snapshot.get("forecasters") or []
            if row.get("status") == "OK" and row.get("direction")
        ]
        buys = sum(1 for d in dirs if d == "Buy")
        sells = sum(1 for d in dirs if d == "Sell")
        neutrals = sum(1 for d in dirs if d == "Neutral")
        total = len(dirs)
    if total == 0:
        return "external forecasters MISSING"
    conf = agg.get("confidence") if isinstance(agg, dict) else None
    agree = f", {round(float(conf) * 100)}% agree" if isinstance(conf, (int, float)) else ""
    if buys > sells and buys > 0:
        return f"consensus lean buy ({buys}/{total} cached{agree})"
    if sells > buys and sells > 0:
        return f"consensus lean sell ({sells}/{total} cached{agree})"
    if neutrals and buys == sells:
        return f"external forecasters neutral ({total} cached)"
    return f"external forecasters mixed ({total} cached)"
