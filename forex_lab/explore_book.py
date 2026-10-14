"""Paper EXPLORATION book (separate from the Stage-2 main shadow book).

Purpose: collect off-policy evidence on board calls the main book skips
(conf >= 0.50 vs main 0.60), so they can be evaluated later.

Hard rules (enforced here and in tests):
* Journals ONLY to data/paper_shadow/explore_journal.jsonl (gitignored);
  never writes the main journal (forex_lab.paper_shadow.journal_path()).
* Never counts toward Stage-2 (main fill_count reads the main journal only).
* Never changes main gates, min_conf, or risk-envelope YAML keys; its own
  config lives in this module (EXPLORE_CONFIG), not in config/*.yaml.
* Never feeds retraining (retrain reads reports/latest_metrics.json only).
* Never trains, never calls the paper broker, never invents prices: entries
  and exits use the board's cached ``last`` mid at pass time only.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from forex_lab.paths import resolve_under_root

SCHEMA = "explore_book_v1"
DHAKA = ZoneInfo("Asia/Dhaka")
_LOCK = threading.Lock()
_DEFAULT_REL = Path("data") / "paper_shadow" / "explore_journal.jsonl"

# Explore-only knobs. Main book threshold is READ for the would_main_take flag
# and is never changed here.
EXPLORE_CONFIG: dict[str, Any] = {
    "policy": "threshold_v1",
    "min_conf": 0.45,
    "main_min_conf": 0.60,
    "max_positions_per_pair": 3,
    # Explore-only: the main book's UTC weekday mute is ignored here. Main
    # gates / YAML are untouched; any other gate text still blocks.
    "drop_weekday_mute": True,
    "max_opens_per_pass": 20,
    "default_horizon_bars": 8,
    "bar_hours": 1.0,
    "require_fresh_signal": True,
}


def explore_journal_path() -> Path:
    raw = os.environ.get("FORX_EXPLORE_JOURNAL")
    path = Path(raw) if raw else resolve_under_root(_DEFAULT_REL)
    try:
        from forex_lab.paper_shadow import journal_path as main_path

        if path.resolve() == main_path().resolve():
            raise ValueError("explore journal must never be the main Stage-2 journal")
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        pass
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamps(now: datetime | None) -> dict[str, str]:
    utc = (now or _now()).astimezone(timezone.utc)
    return {
        "ts_utc": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts_dhaka": utc.astimezone(DHAKA).strftime("%Y-%m-%d %H:%M:%S Asia/Dhaka"),
    }


def _f(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def append(row: dict[str, Any]) -> bool:
    try:
        path = explore_journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK, path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"schema": SCHEMA, **row}, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except ValueError:
        raise
    except Exception:  # noqa: BLE001
        return False


def load(path: Path | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    p = path or explore_journal_path()
    if not p.is_file():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def open_positions(events: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    opened: dict[str, dict[str, Any]] = {}
    for e in events:
        pid = str(e.get("position_id") or "")
        if not pid:
            continue
        if e.get("event") == "open":
            opened[pid] = e
        elif e.get("event") == "close":
            opened.pop(pid, None)
    return opened


def status(events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ev = load() if events is None else events
    opens = [e for e in ev if e.get("event") == "open"]
    closes = [e for e in ev if e.get("event") == "close"]
    decisions = [e for e in ev if e.get("event") == "decision"]
    ann = {str(a.get("ref_position_id")): a for a in ev if a.get("event") == "annotation"}
    rs = []
    for c in closes:
        a = ann.get(str(c.get("position_id")))
        if a and a.get("exclude_from_explore"):
            continue
        r = a.get("corrected_pnl_r") if a and isinstance(a.get("corrected_pnl_r"), (int, float)) else c.get("pnl_r")
        if isinstance(r, (int, float)):
            rs.append(r)
    return {
        "book": "explore",
        "stage2": False,
        "fills": len(opens),
        "open": len(open_positions(ev)),
        "closed": len(closes),
        "decisions": len(decisions),
        "explore_only_fills": sum(1 for o in opens if not o.get("would_main_take")),
        "sum_r": round(sum(rs), 4) if rs else None,
        "annotations": len(ann),
        "min_conf": EXPLORE_CONFIG["min_conf"],
        "note": "Exploration paper book; never Stage-2, never trains, never touches main gates.",
    }


def would_main_take(row: dict[str, Any], main_min_conf: float) -> bool:
    signal = str(row.get("signal") or "").upper()
    conf = _f(row.get("confidence"))
    return (
        signal in {"BUY", "SELL"}
        and conf is not None
        and conf >= main_min_conf
        and not row.get("muted")
        and str(row.get("validity") or "").upper() == "OK"
        and str(row.get("status") or "").lower() in {"ready", ""}
        and not str(row.get("gate_reason") or "").strip()
    )


def _below_min_part(part: str) -> bool:
    g = part.strip().lower()
    return bool(g) and "< min" in g and "conf" in g


def _weekday_part(part: str) -> bool:
    g = part.strip().lower()
    return "weekday gate" in g or "weekday_gate" in g or "weekday mute" in g


def _residual_gate(gate: str, drop_weekday: bool) -> str:
    """Gate text left after removing the parts exploration relaxes."""
    parts = [x for x in str(gate or "").split(" | ") if x.strip()]
    keep = [x for x in parts if not _below_min_part(x) and not (drop_weekday and _weekday_part(x))]
    return " | ".join(keep)


def _below_min_only(gate: str) -> bool:
    return bool(gate.strip()) and not _residual_gate(gate, False)


def explore_eligible(row: dict[str, Any], cfg: dict[str, Any]) -> tuple[bool, str, str | None]:
    """(eligible, reason, side). Uses raw_signal so below-0.60 calls are seen.

    Every other main gate still applies (non-weekday mute, validity, status, news
    gate text); the main min_conf gate is relaxed to cfg['min_conf'] and, when
    cfg['drop_weekday_mute'], the UTC weekday mute is ignored (explore only).
    """
    side = str(row.get("raw_signal") or row.get("signal") or "").upper()
    if side not in {"BUY", "SELL"}:
        return False, "not_directional", None
    conf = _f(row.get("confidence"))
    if conf is None:
        return False, "missing_confidence", side
    if conf < float(cfg["min_conf"]):
        return False, f"below_explore_min:{conf:.4f}<{float(cfg['min_conf']):.2f}", side
    gate = str(row.get("gate_reason") or "")
    drop_wd = bool(cfg.get("drop_weekday_mute"))
    if row.get("muted"):
        weekday_only = drop_wd and any(_weekday_part(x) for x in gate.split(" | ")) and not any(
            x.strip().lower().startswith("muted ") and not _weekday_part(x) for x in gate.split(" | ")
        )
        if not weekday_only:
            return False, "muted", side
    if str(row.get("validity") or "").upper() != "OK":
        return False, f"validity:{row.get('validity')}", side
    if str(row.get("status") or "").lower() not in {"ready", ""}:
        return False, f"status:{row.get('status')}", side
    residual = _residual_gate(gate, drop_wd)
    if residual:
        return False, f"gate:{residual}", side
    if cfg.get("require_fresh_signal") and row.get("last_signal_dhaka") != row.get("last_bar_dhaka"):
        return False, "stale_signal", side
    if _f(row.get("last")) is None:
        return False, "no_price", side
    return True, f"eligible:{side}:{conf:.4f}", side


def board_barriers(pair: str, side: str) -> dict[str, Any] | None:
    """Same ATR triple-barrier SL/TP the board uses for directional calls.

    The board only prints stop/target for calls that clear the MAIN gate, so
    0.50-0.60 explore calls arrive without them. This recomputes the identical
    barrier from the cached OHLCV + main config (read-only). Never invents a
    price: returns None when ATR/close are unavailable.
    """
    try:
        from forex_lab.config_loader import load_config
        from forex_lab.data import load_cached_ohlcv
        from forex_lab.ui.board import _barrier_levels

        cfg = load_config()
        lv = _barrier_levels(load_cached_ohlcv(pair, cfg, "1h"), cfg)
    except Exception:  # noqa: BLE001
        return None
    if not lv:
        return None
    key = "long" if side == "BUY" else "short"
    return {"sl": float(lv[f"{key}_sl"]), "tp": float(lv[f"{key}_tp"]), "horizon_bars": int(lv["horizon"])}


def _resolve_sl_tp(row: dict[str, Any], side: str, barrier_fn) -> tuple[float | None, float | None, int | None, str]:
    sl, tp = _f(row.get("stop")), _f(row.get("target"))
    if sl is not None and tp is not None:
        return sl, tp, int(row.get("horizon_bars") or 0) or None, "board_row"
    lv = barrier_fn(str(row.get("pair") or "").upper(), side) if barrier_fn else None
    if not lv:
        return None, None, None, "none"
    return _f(lv.get("sl")), _f(lv.get("tp")), lv.get("horizon_bars"), "board_atr_barrier_recomputed"


def _cost_fields(pair: str) -> dict[str, Any]:
    try:
        from forex_lab.cost_honesty import enrich_assumed_cost_fields

        return dict(enrich_assumed_cost_fields(bid=None, ask=None, pair=pair))
    except Exception:  # noqa: BLE001
        return {"ba_available": False, "assumed_cost_pips": None}


def _age_hours(open_row: dict[str, Any], now: datetime) -> float:
    try:
        t = datetime.fromisoformat(str(open_row.get("ts_utc")).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (now - t).total_seconds() / 3600.0


def board_bars(pair: str):
    """Cached 1h OHLCV (UTC index) for bar-path exits; None when unavailable.

    FORX_EXPLORE_BARS=off disables it (tests, so they never read desk data)."""
    if os.environ.get("FORX_EXPLORE_BARS", "on").lower() == "off":
        return None
    try:
        from forex_lab.config_loader import load_config
        from forex_lab.data import load_cached_ohlcv

        return load_cached_ohlcv(pair, load_config(), "1h")
    except Exception:  # noqa: BLE001
        return None


def path_exit(pos: dict[str, Any], bars, now: datetime, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """First SL/TP touch (or horizon expiry) on COMPLETED 1h bars after entry.

    Exits at the barrier price, not at whatever the board shows when the next
    pass happens to run (a long gap between passes otherwise books -7R "stops").
    Bars that straddle the entry are skipped; if SL and TP are both inside one
    bar, SL is assumed (conservative). Returns None when nothing is hit yet.
    """
    if bars is None or len(bars) == 0:
        return None
    import pandas as pd

    try:
        t0 = pd.Timestamp(str(pos.get("ts_utc")).replace("Z", "+00:00"))
    except ValueError:
        return None
    idx = pd.DatetimeIndex(bars.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    nowt = pd.Timestamp(now).tz_convert("UTC")
    hours = float(cfg["bar_hours"])
    start = t0.floor("h") + pd.Timedelta(hours=hours)
    expiry = t0 + pd.Timedelta(hours=float(pos.get("horizon_bars") or cfg["default_horizon_bars"]) * hours)
    sl, tp, side = _f(pos.get("sl")), _f(pos.get("tp")), pos.get("side")
    for ts, (_, bar) in zip(idx, bars.iterrows()):
        end = ts + pd.Timedelta(hours=hours)
        if ts < start or end > nowt:
            continue
        hi, lo, cl = _f(bar.get("High")), _f(bar.get("Low")), _f(bar.get("Close"))
        if hi is None or lo is None:
            continue
        if side == "BUY":
            if sl is not None and lo <= sl:
                return {"reason": "sl", "price": sl, "bar_utc": ts}
            if tp is not None and hi >= tp:
                return {"reason": "tp", "price": tp, "bar_utc": ts}
        else:
            if sl is not None and hi >= sl:
                return {"reason": "sl", "price": sl, "bar_utc": ts}
            if tp is not None and lo <= tp:
                return {"reason": "tp", "price": tp, "bar_utc": ts}
        if end >= expiry and cl is not None:
            return {"reason": "duration", "price": cl, "bar_utc": ts}
    return None


def _exit_reason(pos: dict[str, Any], last: float, now: datetime, cfg: dict[str, Any]) -> str | None:
    side = pos.get("side")
    sl, tp = _f(pos.get("sl")), _f(pos.get("tp"))
    if side == "BUY":
        if tp is not None and last >= tp:
            return "tp"
        if sl is not None and last <= sl:
            return "sl"
    else:
        if tp is not None and last <= tp:
            return "tp"
        if sl is not None and last >= sl:
            return "sl"
    horizon = float(pos.get("horizon_bars") or cfg["default_horizon_bars"]) * float(cfg["bar_hours"])
    if _age_hours(pos, now) >= horizon:
        return "duration"
    return None


def run_pass(
    rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    barrier_fn=board_barriers,
    bars_fn=board_bars,
) -> dict[str, Any]:
    """One exploration pass over board rows: mark-to-close, then open."""
    c = {**EXPLORE_CONFIG, **(cfg or {})}
    now = now or _now()
    by_pair = {str(r.get("pair") or "").upper(): r for r in rows if isinstance(r, dict)}
    events = load()
    out: dict[str, Any] = {"closes": [], "decisions": [], "fills_added": 0, "journal": str(explore_journal_path())}

    # 1) exits (mark-based at pass time; not intrabar)
    for pid, pos in open_positions(events).items():
        row = by_pair.get(str(pos.get("pair") or "").upper())
        last = _f((row or {}).get("last"))
        hit = path_exit(pos, bars_fn(str(pos.get("pair") or "").upper()) if bars_fn else None, now, c)
        basis = "bar_path_barrier"
        if hit:
            reason, last = hit["reason"], float(hit["price"])
        else:
            if last is None:
                continue
            reason = _exit_reason(pos, last, now, c)
            basis = "board_last_at_pass"
            if not reason:
                continue
        entry = _f(pos.get("entry_mid")) or last
        risk = abs(entry - (_f(pos.get("sl")) or entry)) or None
        direction = 1.0 if pos.get("side") == "BUY" else -1.0
        pnl = (last - entry) * direction
        close = {
            "event": "close", **_stamps(now), "pair": pos.get("pair"), "side": pos.get("side"),
            "position_id": pid, "entry_mid": entry, "exit_mid": last, "exit_reason": reason,
            "pnl_price": round(pnl, 8), "pnl_r": round(pnl / risk, 4) if risk else None,
            "exit_basis": basis,
            "exit_bar_utc": hit["bar_utc"].strftime("%Y-%m-%dT%H:%M:%SZ") if hit else None,
        }
        append(close)
        out["closes"].append({"pair": pos.get("pair"), "position_id": pid, "exit_reason": reason, "pnl_r": close["pnl_r"]})
        events.append(close)

    # 2) entries
    open_now = open_positions(events)
    per_pair: dict[str, int] = {}
    for pos in open_now.values():
        per_pair[str(pos.get("pair"))] = per_pair.get(str(pos.get("pair")), 0) + 1
    opens = 0
    for pair, row in by_pair.items():
        ok, reason, side = explore_eligible(row, c)
        conf = _f(row.get("confidence"))
        main_take = would_main_take(row, float(c["main_min_conf"]))
        take = False
        sl = tp = None
        horizon = None
        barrier_src = None
        if ok:
            sl, tp, horizon, barrier_src = _resolve_sl_tp(row, str(side), barrier_fn)
            if sl is None or tp is None:
                ok, reason = False, "no_sl_tp"
        if ok:
            if per_pair.get(pair, 0) >= int(c["max_positions_per_pair"]):
                reason = "max_positions_per_pair"
            elif opens >= int(c["max_opens_per_pass"]):
                reason = "max_opens_per_pass"
            else:
                take = True
        decision = {
            "event": "decision", **_stamps(now), "pair": pair, "side": side, "conf": conf,
            "raw_signal": row.get("raw_signal"), "main_signal": row.get("signal"),
            "would_main_take": main_take, "take": take, "reason": reason,
            "policy": c["policy"], "threshold": c["min_conf"], "main_threshold": c["main_min_conf"],
            "propensity": 1.0 if take else 0.0,
            "propensity_note": "deterministic threshold policy: P(take)=1 if eligible and capacity else 0",
            "last_bar_dhaka": row.get("last_bar_dhaka"), "last_signal_dhaka": row.get("last_signal_dhaka"),
            "gate_reason": row.get("gate_reason"),
        }
        append(decision)
        out["decisions"].append({k: decision[k] for k in ("pair", "side", "conf", "would_main_take", "take", "reason")})
        if not take:
            continue
        pid = f"exp_{uuid.uuid4().hex[:12]}"
        open_row = {
            "event": "open", **_stamps(now), "pair": pair, "side": side, "conf": conf,
            "would_main_take": main_take, "policy": c["policy"], "threshold": c["min_conf"],
            "propensity": 1.0, "position_id": pid, "fill_id": f"expfill_{uuid.uuid4().hex[:12]}",
            "entry_mid": _f(row.get("last")), "sl": sl, "tp": tp, "barrier_source": barrier_src,
            "horizon_bars": int(horizon or row.get("horizon_bars") or c["default_horizon_bars"]),
            "entry_basis": "board_last_mid", **_cost_fields(pair),
        }
        append(open_row)
        per_pair[pair] = per_pair.get(pair, 0) + 1
        opens += 1
        out["fills_added"] += 1
    out["status"] = status()
    return out


__all__ = [
    "EXPLORE_CONFIG",
    "SCHEMA",
    "explore_eligible",
    "explore_journal_path",
    "load",
    "open_positions",
    "run_pass",
    "status",
    "would_main_take",
]
