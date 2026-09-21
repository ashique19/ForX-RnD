"""Persist the Streamlit watchlist to a local YAML file.

Survives Streamlit reruns. Not a broker watchlist; research UI only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from forex_lab.config_loader import load_config
from forex_lab.data import data_path
from forex_lab.paths import project_root, resolve_under_root

DEFAULT_WATCHLIST_REL = "config/watchlist.yaml"
DEFAULT_REFRESH_SECONDS = 60
KNOWN_INTERVALS = ("1h", "1d", "4h", "15m")


class WatchlistError(ValueError):
    """Invalid pair or watchlist payload."""


@dataclass
class WatchItem:
    pair: str
    interval: str | None = None  # None = use watchlist/lab default

    def resolved_interval(self, default: str) -> str:
        return str(self.interval or default)


@dataclass
class Watchlist:
    pairs: list[WatchItem] = field(default_factory=list)
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    interval: str | None = None  # None = config/default.yaml interval
    path: str | None = None

    def pair_symbols(self) -> list[str]:
        return [p.pair for p in self.pairs]

    def contains(self, pair: str) -> bool:
        key = str(pair).upper()
        return any(p.pair == key for p in self.pairs)

    def lab_interval(self, cfg: dict[str, Any] | None = None) -> str:
        if self.interval:
            return str(self.interval)
        cfg = cfg if cfg is not None else load_config()
        return str(cfg.get("interval") or "1h")


def watchlist_path(rel: str | Path | None = None) -> Path:
    return resolve_under_root(rel or DEFAULT_WATCHLIST_REL)


def normalize_pair(raw: str) -> str:
    """EURUSD, EUR/USD, eurusd, EURUSD=X -> EURUSD."""
    s = str(raw or "").strip().upper()
    for ch in (" ", "/", "-", "_"):
        s = s.replace(ch, "")
    if s.endswith("=X"):
        s = s[:-2]
    if len(s) == 7 and s.endswith("X") and s[:6].isalpha():
        s = s[:6]
    if len(s) != 6 or not s.isalpha():
        raise WatchlistError(f"Expected a 6-letter FX pair (e.g. EURUSD), got {raw!r}")
    return s


def parse_ohlcv_filename(name: str) -> tuple[str, str] | None:
    stem = Path(name).stem
    if "_" not in stem:
        return None
    pair_part, interval = stem.rsplit("_", 1)
    try:
        pair = normalize_pair(pair_part)
    except WatchlistError:
        return None
    if not interval:
        return None
    return pair, interval


def discover_cached_pairs(cfg: dict[str, Any] | None = None) -> list[WatchItem]:
    """EURUSD first, then any other pair that already has a data CSV."""
    cfg = cfg if cfg is not None else load_config()
    lab_iv = str(cfg.get("interval") or "1h")
    found: list[WatchItem] = []
    seen: set[str] = set()

    def _add(pair: str, interval: str | None = None) -> None:
        if pair in seen:
            return
        seen.add(pair)
        found.append(WatchItem(pair=pair, interval=interval))

    _add("EURUSD")
    data_dir = resolve_under_root(cfg.get("paths", {}).get("data_dir", "data"))
    if data_dir.is_dir():
        for csv in sorted(data_dir.glob("*.csv")):
            parsed = parse_ohlcv_filename(csv.name)
            if not parsed:
                continue
            pair, interval = parsed
            extra_iv = None if interval == lab_iv else interval
            _add(pair, extra_iv)
    for key in cfg.get("pairs") or {}:
        try:
            pair = normalize_pair(str(key))
        except WatchlistError:
            continue
        if data_path(pair, cfg, lab_iv).exists() and pair not in seen:
            _add(pair)
    return found


def default_watchlist(cfg: dict[str, Any] | None = None) -> Watchlist:
    return Watchlist(
        pairs=discover_cached_pairs(cfg),
        refresh_seconds=DEFAULT_REFRESH_SECONDS,
        interval=None,
    )


def _item_from_raw(raw: Any) -> WatchItem:
    if isinstance(raw, str):
        return WatchItem(pair=normalize_pair(raw), interval=None)
    if isinstance(raw, dict):
        pair = normalize_pair(str(raw.get("pair") or raw.get("symbol") or ""))
        iv = raw.get("interval") or raw.get("timeframe") or None
        if iv is not None:
            iv = str(iv).strip() or None
        return WatchItem(pair=pair, interval=iv)
    raise WatchlistError(f"Watchlist pair must be a string or mapping, got {type(raw)}")


def _dedupe(items: Iterable[WatchItem]) -> list[WatchItem]:
    out: list[WatchItem] = []
    seen: set[tuple[str, str | None]] = set()
    for item in items:
        key = (item.pair, item.interval)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def watchlist_from_mapping(data: dict[str, Any] | None, *, path: Path | None = None) -> Watchlist:
    data = data or {}
    refresh = int(data.get("refresh_seconds") or DEFAULT_REFRESH_SECONDS)
    refresh = max(15, min(refresh, 3600))
    interval = data.get("interval")
    if interval is not None:
        interval = str(interval).strip() or None
    raw_pairs = data.get("pairs")
    if raw_pairs is None:
        items = discover_cached_pairs()
    else:
        items = [_item_from_raw(p) for p in raw_pairs]
    return Watchlist(
        pairs=_dedupe(items),
        refresh_seconds=refresh,
        interval=interval,
        path=str(path) if path else None,
    )


def load_watchlist(
    path: str | Path | None = None,
    cfg: dict[str, Any] | None = None,
    *,
    create: bool = False,
) -> Watchlist:
    """Load watchlist YAML. Missing file -> EURUSD (+ cached pairs); optionally write it."""
    cfg = cfg if cfg is not None else load_config()
    p = Path(path) if path is not None else watchlist_path()
    if not p.exists():
        wl = default_watchlist(cfg)
        wl.path = str(p)
        if create:
            save_watchlist(wl, p)
        return wl
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise WatchlistError(f"Watchlist must be a mapping: {p}")
    wl = watchlist_from_mapping(raw, path=p)
    return wl


def save_watchlist(wl: Watchlist, path: str | Path | None = None) -> Path:
    p = Path(path) if path is not None else (Path(wl.path) if wl.path else watchlist_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "refresh_seconds": int(wl.refresh_seconds),
        "interval": wl.interval,
        "pairs": [
            item.pair if item.interval is None else {"pair": item.pair, "interval": item.interval}
            for item in wl.pairs
        ],
    }
    text = (
        "# Streamlit research watchlist (not a broker list; no live orders).\n"
        "# Survives app reruns. Edit here or via the dashboard Add / Remove controls.\n"
        + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    )
    p.write_text(text, encoding="utf-8")
    wl.path = str(p)
    return p


def add_pair(wl: Watchlist, pair: str, interval: str | None = None) -> Watchlist:
    item = WatchItem(pair=normalize_pair(pair), interval=interval or None)
    if any(p.pair == item.pair and p.interval == item.interval for p in wl.pairs):
        return wl
    # Replace a same-pair global row when adding a timeframe override, and vice versa.
    wl.pairs = [p for p in wl.pairs if p.pair != item.pair] + [item]
    return wl


def remove_pair(wl: Watchlist, pair: str) -> Watchlist:
    key = normalize_pair(pair)
    wl.pairs = [p for p in wl.pairs if p.pair != key]
    return wl


def watchlist_to_dict(wl: Watchlist) -> dict[str, Any]:
    return asdict(wl)
