"""Boot-time tip Volume self-heal for Decision desk live caches.

Idempotent: only fills Volume where live<=0 and a donor has Volume>0.
Never changes OHLC on existing bars. Never promotes / never touches gates.
Fail-soft: callers must catch exceptions and continue desk start.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import (
    REQUIRED_COLS,
    SOURCE_DUKASCOPY,
    data_path,
    load_cached_ohlcv,
    write_cache_source,
    _normalize_ohlcv,
)
from forex_lab.history import (
    fetch_dukascopy_recent_bars,
    history_path,
    load_history_csv,
)


@dataclass
class TipVolHealResult:
    pair: str
    healed_bars: int = 0
    tip_zeros_before: int = 0
    tip_zeros_after: int = 0
    sources: list[str] = field(default_factory=list)
    skipped: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pair": self.pair,
            "healed_bars": self.healed_bars,
            "tip_zeros_before": self.tip_zeros_before,
            "tip_zeros_after": self.tip_zeros_after,
            "sources": list(self.sources),
            "skipped": self.skipped,
            "error": self.error,
        }


def _watchlist_pairs(cfg: dict[str, Any]) -> list[str]:
    """Resolve Active + watched pairs from config/watchlist.yaml (desk source of truth)."""
    pairs: list[str] = []
    try:
        from forex_lab.ui.watchlist import active_pair, load_watchlist

        wl = load_watchlist()
        focus = str(active_pair(wl) or "").upper().replace("/", "")
        if focus:
            pairs.append(focus)
        for item in wl.pairs or []:
            name = str(getattr(item, "pair", "") or "").upper().replace("/", "")
            if name and name not in pairs:
                pairs.append(name)
    except Exception:
        pass
    # Fallback: cfg.watchlist block or hard Active EURUSD so heal never no-ops empty.
    if not pairs:
        wl = cfg.get("watchlist") or {}
        active = str(wl.get("active") or "EURUSD").upper().replace("/", "")
        if active:
            pairs.append(active)
        for raw in wl.get("pairs") or []:
            if isinstance(raw, dict):
                name = str(raw.get("pair") or "").upper().replace("/", "")
            else:
                name = str(raw).upper().replace("/", "")
            if name and name not in pairs:
                pairs.append(name)
    if not pairs:
        pairs = ["EURUSD"]
    return pairs


def _coalesce_volume_only(base: pd.DataFrame, donor: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if base is None or base.empty:
        if donor is None or donor.empty:
            return base, 0
        return _normalize_ohlcv(donor)[REQUIRED_COLS], 0
    out = _normalize_ohlcv(base).copy()
    if donor is None or donor.empty:
        return out[REQUIRED_COLS], 0
    don = _normalize_ohlcv(donor)
    prior = pd.to_numeric(out["Volume"], errors="coerce")
    newv = pd.to_numeric(don["Volume"], errors="coerce").reindex(out.index)
    n = int(((prior.fillna(0.0) <= 0) & (newv.fillna(0.0) > 0)).sum())
    out["Volume"] = prior.where(prior.fillna(0.0) > 0, newv).fillna(0.0)
    extra = don.loc[don.index > out.index.max()] if len(don) else don
    if extra is not None and len(extra):
        # Only append extras that have positive volume (avoid extending with zero tips).
        extra_pos = extra.loc[pd.to_numeric(extra["Volume"], errors="coerce").fillna(0.0) > 0]
        if len(extra_pos):
            out = pd.concat([out, extra_pos])
            out = out[~out.index.duplicated(keep="last")].sort_index()
    out.index.name = "Datetime"
    return out[REQUIRED_COLS], n


def heal_pair_tip_volume(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    tip_bars: int = 200,
    duka_lookback_hours: int = 72,
    use_dukascopy: bool = True,
    write: bool = True,
) -> TipVolHealResult:
    """Refill zero/blank Volume on recent tip bars for one pair.

    Order: history same-ts positive Volume, then Dukascopy recent (optional).
    OHLC on existing timestamps is preserved.
    """
    cfg = cfg or load_config()
    pair_u = str(pair).upper().replace("/", "")
    result = TipVolHealResult(pair=pair_u)
    try:
        live = load_cached_ohlcv(pair_u, cfg, "1h")
    except Exception as exc:
        result.error = f"load_cached: {exc}"
        return result
    if live is None or live.empty:
        result.skipped = "no_live_cache"
        return result
    live = _normalize_ohlcv(live)
    vol = pd.to_numeric(live["Volume"], errors="coerce").fillna(0.0)
    tip_n = max(8, int(tip_bars))
    tip_slice = vol.tail(tip_n)
    result.tip_zeros_before = int((tip_slice <= 0).sum())
    if result.tip_zeros_before == 0:
        result.skipped = "tip_already_positive"
        result.tip_zeros_after = 0
        return result

    merged = live
    total = 0

    # History donor
    try:
        hist = load_history_csv(history_path(pair_u, "1h", cfg))
        if hist is not None and not hist.empty:
            merged, n = _coalesce_volume_only(merged, hist)
            if n:
                total += n
                result.sources.append(f"history:{n}")
    except Exception as exc:
        result.sources.append(f"history_error:{exc}")

    # Dukascopy donor (tip remesh / tick volume)
    if use_dukascopy:
        try:
            duka = fetch_dukascopy_recent_bars(
                pair_u, lookback_hours=max(8, int(duka_lookback_hours)), grain="1h"
            )
            if duka is not None and not duka.empty:
                duka = _normalize_ohlcv(duka[REQUIRED_COLS])
                merged, n = _coalesce_volume_only(merged, duka)
                if n:
                    total += n
                    result.sources.append(f"dukascopy:{n}")
                if write:
                    try:
                        write_cache_source(pair_u, cfg, "1h", SOURCE_DUKASCOPY)
                    except Exception:
                        pass
        except Exception as exc:
            result.sources.append(f"dukascopy_error:{exc}")

    result.healed_bars = total
    vol_after = pd.to_numeric(merged["Volume"], errors="coerce").fillna(0.0)
    result.tip_zeros_after = int((vol_after.tail(tip_n) <= 0).sum())

    if write and total > 0:
        live_path = data_path(pair_u, cfg, "1h")
        merged.to_csv(live_path)

    return result


def heal_watchlist_tip_volumes(
    cfg: dict[str, Any] | None = None,
    *,
    tip_bars: int = 200,
    duka_lookback_hours: int = 72,
    use_dukascopy: bool = True,
    write: bool = True,
    max_pairs: int | None = None,
) -> dict[str, Any]:
    """Heal Active + watchlist live caches. Fail-soft per pair."""
    cfg = cfg or load_config()
    pairs = _watchlist_pairs(cfg)
    if max_pairs is not None:
        pairs = pairs[: max(1, int(max_pairs))]
    results: list[TipVolHealResult] = []
    healed_total = 0
    for pair in pairs:
        try:
            # Active first gets Dukascopy; later pairs can skip Duka if slow — still try hist.
            use_duka = use_dukascopy
            r = heal_pair_tip_volume(
                pair,
                cfg,
                tip_bars=tip_bars,
                duka_lookback_hours=duka_lookback_hours,
                use_dukascopy=use_duka,
                write=write,
            )
        except Exception as exc:
            r = TipVolHealResult(pair=pair, error=str(exc))
        results.append(r)
        healed_total += int(r.healed_bars or 0)

    return {
        "ok": True,
        "healed_bars": healed_total,
        "pairs": [r.as_dict() for r in results],
    }


def format_heal_log_line(summary: dict[str, Any]) -> str:
    n = int(summary.get("healed_bars") or 0)
    parts = []
    for p in summary.get("pairs") or []:
        if p.get("healed_bars"):
            parts.append(f"{p.get('pair')}:{p.get('healed_bars')}")
        elif p.get("skipped"):
            parts.append(f"{p.get('pair')}={p.get('skipped')}")
        elif p.get("error"):
            parts.append(f"{p.get('pair')}=err")
    detail = ",".join(parts) if parts else "none"
    return f"tip_vol_healed {n} bars ({detail})"
