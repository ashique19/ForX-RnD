"""Append-only board call logger (calls_v1).

Every pair's board call on each burst pass is appended to
``data/paper_shadow/calls.jsonl`` (gitignored; ``FORX_CALLS_LOG`` overrides).
Read-only with respect to trading: no orders, no journal writes, never feeds
training or Stage-2. Scored later by ``scripts/score_calls.py``.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "paper_shadow" / "calls.jsonl"
SCHEMA = "calls_v1"
_FORBIDDEN = ("journal.jsonl", "explore_journal.jsonl")


def calls_path() -> Path:
    raw = os.environ.get("FORX_CALLS_LOG")
    p = Path(raw) if raw else DEFAULT_PATH
    if p.name in _FORBIDDEN:
        raise ValueError("call log must never be a paper journal")
    return p


def _f(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def bar_utc(value: object) -> str | None:
    """'2026-10-09 19:00:00 Asia/Dhaka' (or ISO) -> '2026-10-09T13:00:00Z'."""
    if value in (None, ""):
        return None
    s = str(value).strip()
    tz = None
    if s.endswith(" Asia/Dhaka"):
        s, tz = s[: -len(" Asia/Dhaka")], "Asia/Dhaka"
    try:
        ts = pd.Timestamp(s)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize(tz or "UTC")
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def call_id(pair: str, signal_bar_utc: str | None, raw_signal: str | None) -> str:
    key = f"{pair}|{signal_bar_utc}|{raw_signal}"
    return "call_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def existing_ids(path: Path | None = None) -> set[str]:
    p = path or calls_path()
    out: set[str] = set()
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                cid = json.loads(line).get("call_id")
            except (ValueError, AttributeError):
                continue
            if cid:
                out.add(cid)
    return out


def build_record(
    row: dict[str, Any],
    *,
    source: str,
    now: datetime,
    barrier_fn: Callable[[str, str], dict[str, Any] | None] | None = None,
    skip_reason: str | None = None,
) -> dict[str, Any]:
    pair = str(row.get("pair") or "").upper()
    raw = str(row.get("raw_signal") or row.get("signal") or "").upper() or None
    sig = str(row.get("signal") or "").upper() or None
    sbar = bar_utc(row.get("last_signal_dhaka") or row.get("signal_time"))
    stop, target = _f(row.get("stop")), _f(row.get("target"))
    src = "board_row" if stop is not None and target is not None else None
    horizon = row.get("horizon_bars")
    if src is None and raw in {"BUY", "SELL"} and barrier_fn is not None:
        try:
            lv = barrier_fn(pair, raw)
        except Exception:  # noqa: BLE001 - logging must never break a burst
            lv = None
        if lv:
            stop, target, horizon, src = _f(lv.get("sl")), _f(lv.get("tp")), lv.get("horizon_bars"), "board_atr_barrier_recomputed"
    return {
        "schema": SCHEMA,
        "event": "call",
        "call_id": call_id(pair, sbar, raw),
        "logged_at_utc": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "pair": pair,
        "interval": row.get("interval") or "1h",
        "signal_bar_utc": sbar,
        "last_bar_utc": bar_utc(row.get("last_bar_dhaka")),
        "signal": sig,
        "raw_signal": raw,
        "confidence": _f(row.get("confidence")),
        "price": _f(row.get("last")),
        "stop": stop,
        "target": target,
        "horizon_bars": int(horizon) if horizon not in (None, "") else None,
        "barrier_source": src,
        "muted": bool(row.get("muted")),
        "validity": row.get("validity"),
        "status": row.get("status"),
        "gate_reason": row.get("gate_reason") or None,
        "skip_reason": skip_reason,
        "never_feeds_training": True,
        "stage2": False,
    }


def log_board(
    rows: Iterable[dict[str, Any]],
    *,
    source: str,
    now: datetime | None = None,
    skip_reasons: dict[str, str] | None = None,
    barrier_fn: Callable[[str, str], dict[str, Any] | None] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Append one record per board row; a call already logged (same pair,
    signal bar and raw side) is not duplicated."""
    p = path or calls_path()
    now = now or datetime.now(timezone.utc)
    seen = existing_ids(p)
    written, dup = 0, 0
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for row in rows:
            if not isinstance(row, dict) or not row.get("pair"):
                continue
            pair = str(row.get("pair")).upper()
            rec = build_record(row, source=source, now=now, barrier_fn=barrier_fn,
                               skip_reason=(skip_reasons or {}).get(pair))
            if rec["call_id"] in seen:
                dup += 1
                continue
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            seen.add(rec["call_id"])
            written += 1
    return {"path": str(p), "written": written, "duplicates_skipped": dup}


def load_calls(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or calls_path()
    out: list[dict[str, Any]] = []
    if not p.exists():
        return out
    seen: set[str] = set()
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            cid = rec.get("call_id")
            if rec.get("event") != "call" or not cid or cid in seen:
                continue
            seen.add(cid)
            out.append(rec)
    return out
