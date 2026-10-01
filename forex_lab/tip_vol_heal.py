"""Boot-time tip Volume self-heal for Decision desk live caches.

Idempotent: only fills Volume where live<=0 and a donor has Volume>0.
Never changes OHLC on existing bars. Never promotes / never touches gates.
Fail-soft: callers must catch exceptions and continue desk start.

Boot path (START_DESK) must stay fast: skip Dukascopy when history already
cleared tip zeros; cap Duka to Active (duka_max_pairs); honor budget_sec.
"""
from __future__ import annotations

import threading
import time
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


def _tip_zero_count(frame: pd.DataFrame, tip_n: int) -> int:
    vol = pd.to_numeric(frame["Volume"], errors="coerce").fillna(0.0)
    return int((vol.tail(max(8, int(tip_n))) <= 0).sum())


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


def _fetch_duka_budgeted(
    pair: str,
    *,
    lookback_hours: int,
    timeout_sec: float | None,
) -> tuple[pd.DataFrame | None, str | None]:
    """Fetch Dukascopy recent bars; fail-soft on timeout/error.

    timeout_sec caps wall time for this pair's Duka work so START_DESK never
    sits minutes on a stuck bi5 download. Uses a daemon thread so the heal
    process can exit on timeout without joining the orphan download.
    """
    kwargs = {
        "lookback_hours": max(8, int(lookback_hours)),
        "grain": "1h",
    }
    if timeout_sec is None or timeout_sec <= 0:
        try:
            return fetch_dukascopy_recent_bars(pair, **kwargs), None
        except Exception as exc:
            return None, f"dukascopy_error:{exc}"

    box: list[tuple[str, Any]] = []

    def _run() -> None:
        try:
            box.append(("ok", fetch_dukascopy_recent_bars(pair, **kwargs)))
        except Exception as exc:  # noqa: BLE001 - fail-soft to caller
            box.append(("err", exc))

    th = threading.Thread(target=_run, name=f"duka-tip-heal-{pair}", daemon=True)
    th.start()
    th.join(timeout=float(timeout_sec))
    if th.is_alive():
        return None, "dukascopy_timeout"
    if not box:
        return None, "dukascopy:empty"
    kind, payload = box[0]
    if kind == "err":
        return None, f"dukascopy_error:{payload}"
    return payload, None  # type: ignore[return-value]



def heal_pair_tip_volume(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    tip_bars: int = 200,
    duka_lookback_hours: int = 72,
    use_dukascopy: bool = True,
    write: bool = True,
    duka_deadline: float | None = None,
) -> TipVolHealResult:
    """Refill zero/blank Volume on recent tip bars for one pair.

    Order: history same-ts positive Volume, then Dukascopy recent (optional).
    Skip Dukascopy when history already cleared tip zeros, or when duka_deadline
    (time.monotonic) has passed. OHLC on existing timestamps is preserved.
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
    tip_n = max(8, int(tip_bars))
    result.tip_zeros_before = _tip_zero_count(live, tip_n)
    if result.tip_zeros_before == 0:
        result.skipped = "tip_already_positive"
        result.tip_zeros_after = 0
        return result

    merged = live
    total = 0

    # History donor (cheap local CSV — always try first)
    try:
        hist = load_history_csv(history_path(pair_u, "1h", cfg))
        if hist is not None and not hist.empty:
            merged, n = _coalesce_volume_only(merged, hist)
            if n:
                total += n
                result.sources.append(f"history:{n}")
    except Exception as exc:
        result.sources.append(f"history_error:{exc}")

    zeros_after_hist = _tip_zero_count(merged, tip_n)
    run_duka = bool(use_dukascopy)
    if run_duka and zeros_after_hist == 0:
        result.sources.append("duka_skipped:hist_enough")
        run_duka = False
    elif run_duka and duka_deadline is not None and time.monotonic() >= float(duka_deadline):
        result.sources.append("duka_skipped:budget")
        run_duka = False

    # Dukascopy donor (tip remesh / tick volume) — budgeted
    if run_duka:
        remaining = None
        if duka_deadline is not None:
            remaining = max(0.5, float(duka_deadline) - time.monotonic())
        duka, err = _fetch_duka_budgeted(
            pair_u, lookback_hours=duka_lookback_hours, timeout_sec=remaining
        )
        if err:
            result.sources.append(err)
        elif duka is not None and not duka.empty:
            try:
                duka = _normalize_ohlcv(duka[REQUIRED_COLS])
                merged, n = _coalesce_volume_only(merged, duka)
                if n:
                    total += n
                    result.sources.append(f"dukascopy:{n}")
                else:
                    result.sources.append("dukascopy:0")
                if write and n:
                    try:
                        write_cache_source(pair_u, cfg, "1h", SOURCE_DUKASCOPY)
                    except Exception:
                        pass
            except Exception as exc:
                result.sources.append(f"dukascopy_error:{exc}")
        else:
            result.sources.append("dukascopy:empty")

    result.healed_bars = total
    result.tip_zeros_after = _tip_zero_count(merged, tip_n)

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
    duka_max_pairs: int = 1,
    budget_sec: float | None = 45.0,
) -> dict[str, Any]:
    """Heal Active + watchlist live caches. Fail-soft per pair.

    Dukascopy is slow (bi5 downloads). Boot defaults:
    - duka_max_pairs=1 → only Active (first) may hit Duka after hist
    - budget_sec=45 → wall-clock cap for all Duka work; remaining pairs hist-only
    Hist always runs for every pair (cheap, correctness for Volume=0 when donor has data).
    """
    cfg = cfg or load_config()
    pairs = _watchlist_pairs(cfg)
    if max_pairs is not None:
        pairs = pairs[: max(1, int(max_pairs))]
    deadline = (
        time.monotonic() + float(budget_sec)
        if budget_sec is not None and float(budget_sec) > 0
        else None
    )
    duka_slots = max(0, int(duka_max_pairs)) if use_dukascopy else 0
    results: list[TipVolHealResult] = []
    healed_total = 0
    duka_used = 0
    for pair in pairs:
        try:
            allow_duka = use_dukascopy and duka_used < duka_slots
            if allow_duka and deadline is not None and time.monotonic() >= deadline:
                allow_duka = False
            r = heal_pair_tip_volume(
                pair,
                cfg,
                tip_bars=tip_bars,
                duka_lookback_hours=duka_lookback_hours,
                use_dukascopy=allow_duka,
                write=write,
                duka_deadline=deadline if allow_duka else None,
            )
            # Count a Duka slot only when we actually attempted a download (not hist_enough skip).
            if allow_duka and any(
                s.startswith("dukascopy:")
                or s.startswith("dukascopy_error:")
                or s == "dukascopy_timeout"
                for s in r.sources
            ):
                duka_used += 1
            elif allow_duka and any(s.startswith("duka_skipped:") for s in r.sources):
                # hist_enough / budget skip did not consume network; keep slot for next needy pair
                pass
            elif allow_duka and not any(s.startswith("duka_skipped:") for s in r.sources):
                # attempted path with empty sources is rare; still reserve if tip needed Duka
                if r.tip_zeros_before and "history:" not in "".join(r.sources):
                    duka_used += 1
        except Exception as exc:
            r = TipVolHealResult(pair=pair, error=str(exc))
        results.append(r)
        healed_total += int(r.healed_bars or 0)

    out: dict[str, Any] = {
        "ok": True,
        "healed_bars": healed_total,
        "pairs": [r.as_dict() for r in results],
        "duka_used": duka_used,
    }
    if deadline is not None:
        out["budget_sec"] = float(budget_sec) if budget_sec is not None else None
        out["budget_remaining_sec"] = round(max(0.0, deadline - time.monotonic()), 2)
    return out


def format_heal_log_line(summary: dict[str, Any]) -> str:
    n = int(summary.get("healed_bars") or 0)
    parts = []
    for p in summary.get("pairs") or []:
        if p.get("healed_bars"):
            z0 = p.get("tip_zeros_before")
            z1 = p.get("tip_zeros_after")
            zbit = f" z{z0}->{z1}" if z0 is not None and z1 is not None else ""
            parts.append(f"{p.get('pair')}:{p.get('healed_bars')}{zbit}")
        elif p.get("skipped"):
            parts.append(f"{p.get('pair')}={p.get('skipped')}")
        elif p.get("error"):
            parts.append(f"{p.get('pair')}=err")
        else:
            # Attempted but no fill — show why (sources) so boot log is not opaque "none"
            src = ",".join(p.get("sources") or []) or "no_donor"
            z0 = p.get("tip_zeros_before")
            parts.append(f"{p.get('pair')}=0({src};z{z0})")
    detail = ",".join(parts) if parts else "none"
    extra = ""
    if summary.get("duka_used") is not None:
        extra += f" duka_used={summary.get('duka_used')}"
    if summary.get("budget_remaining_sec") is not None:
        extra += f" budget_left={summary.get('budget_remaining_sec')}s"
    return f"tip_vol_healed {n} bars ({detail}){extra}"

