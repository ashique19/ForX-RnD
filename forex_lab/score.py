"""Paper lookback scorer — RIGHT / WRONG / PENDING from cached bars.

Scores PaperBroker journal rows after later OHLCV exists: first-touch TP/SL,
or signed move at the label horizon. This is a practice book, not a live edge
and not a broker fill.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

SCORED_OUTCOMES = ("RIGHT", "WRONG")
DISPLAY_OUTCOMES = ("RIGHT", "WRONG", "PENDING", "FLAT")
CLOSED_UNSCORED = ("TIMEOUT", "FLAT")


@dataclass(frozen=True)
class ScoreResult:
    """Lookback result for one paper position against later bars."""

    status: str  # pending | closed
    outcome: str  # RIGHT | WRONG | PENDING | FLAT
    exit_px: float | None
    reason: str | None  # tp | sl | timeout | manual | None
    bars_seen: int


def conf_bucket(confidence: object) -> str:
    try:
        c = float(confidence)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "n/a"
    if pd.isna(c):
        return "n/a"
    if c < 0.40:
        return "<0.40"
    if c < 0.60:
        return "0.40-0.60"
    return ">=0.60"


def signed_return(side: str, entry: float, exit_px: float) -> float:
    if str(side).upper() == "BUY":
        return (exit_px - entry) / entry
    return (entry - exit_px) / entry


def walk_barriers(
    ohlcv: pd.DataFrame,
    *,
    start_loc: int,
    horizon: int,
    side: str,
    entry: float,
    sl: float | None,
    tp: float | None,
    path: str = "high_low",
) -> tuple[str, float | None, str | None, int]:
    """Scan from start_loc. Returns (pending|closed, exit_px, reason, bars_seen)."""
    n = len(ohlcv)
    if start_loc < 0 or start_loc >= n:
        return "pending", None, None, 0
    end = min(n, start_loc + max(1, int(horizon)))
    use_hl = path != "close"
    high = ohlcv["High"].to_numpy()
    low = ohlcv["Low"].to_numpy()
    close = ohlcv["Close"].to_numpy()
    side_u = str(side).upper()
    bars = 0
    for i in range(start_loc, end):
        bars += 1
        px_up = float(high[i] if use_hl else close[i])
        px_dn = float(low[i] if use_hl else close[i])
        if side_u == "BUY":
            hit_tp = tp is not None and px_up >= tp
            hit_sl = sl is not None and px_dn <= sl
        else:
            hit_tp = tp is not None and px_dn <= tp
            hit_sl = sl is not None and px_up >= sl
        if hit_tp and hit_sl:
            # Same-bar conflict: conservative WRONG (SL).
            return "closed", float(sl) if sl is not None else float(close[i]), "sl", bars
        if hit_sl:
            return "closed", float(sl) if sl is not None else float(close[i]), "sl", bars
        if hit_tp:
            return "closed", float(tp) if tp is not None else float(close[i]), "tp", bars
    if bars >= int(horizon):
        return "closed", float(close[end - 1]), "timeout", bars
    return "pending", float(close[end - 1]) if bars else None, None, bars


def outcome_from_exit(
    reason: str | None,
    *,
    side: str = "",
    entry: float | None = None,
    exit_px: float | None = None,
    spread_frac: float = 0.0,
) -> str:
    """Map an exit reason to RIGHT / WRONG / PENDING / FLAT.

    Horizon timeout is scored from the signed move at the last bar (after
    the paper spread). Not enough bars stay PENDING. Manual close is FLAT
    (user flattened — not a lookback of the original thesis).
    """
    r = str(reason or "").lower()
    if r == "tp":
        return "RIGHT"
    if r in {"sl", "sl_conflict"}:
        return "WRONG"
    if r == "timeout":
        if entry is None or exit_px is None:
            return "PENDING"
        try:
            ret = signed_return(side, float(entry), float(exit_px)) - float(spread_frac or 0.0)
        except (TypeError, ValueError):
            return "PENDING"
        return "RIGHT" if ret > 0 else "WRONG"
    if r in {"manual", "flat"}:
        return "FLAT"
    return "PENDING"


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(out):
        return None
    return out


def normalize_outcome(row: dict[str, Any]) -> str:
    """RIGHT/WRONG/PENDING/FLAT for display and aggregates.

    Older journals stored horizon exits as TIMEOUT. Re-score those from
    realized PnL (or entry/exit) so hit-rate stats stay three-state.
    """
    o = str(row.get("outcome") or "").upper().strip()
    if o in {"RIGHT", "WRONG", "PENDING", "FLAT"}:
        return o
    reason = str(row.get("exit_reason") or "").lower()
    if o == "TIMEOUT" or reason == "timeout":
        realized = _float_or_none(row.get("realized"))
        if realized is not None:
            return "RIGHT" if realized > 0 else "WRONG"
        entry = _float_or_none(row.get("entry_price"))
        exit_px = _float_or_none(row.get("exit_price"))
        spread = _float_or_none(row.get("spread_frac")) or 0.0
        if entry is not None and exit_px is not None:
            return outcome_from_exit(
                "timeout",
                side=str(row.get("side") or ""),
                entry=entry,
                exit_px=exit_px,
                spread_frac=spread,
            )
        return "PENDING"
    if not o:
        return "PENDING"
    return o


def start_loc_after_entry(ohlcv: pd.DataFrame, entry_bar_time: object) -> int | None:
    """Index of the first bar strictly after the entry bar, or None."""
    entry_bar = pd.to_datetime(entry_bar_time, utc=True, errors="coerce")
    if pd.isna(entry_bar) or ohlcv is None or ohlcv.empty:
        return None
    entry_naive = entry_bar.tz_convert("UTC").tz_localize(None)
    idx = pd.to_datetime(ohlcv.index, utc=True).tz_convert("UTC").tz_localize(None)
    later = [i for i, ts in enumerate(idx) if ts > entry_naive]
    if not later:
        return None
    return int(later[0])


def score_from_ohlcv(
    pos: dict[str, Any],
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any] | None = None,
) -> ScoreResult:
    """Lookback-score one paper position. PENDING until enough bars exist."""
    cfg = cfg or {}
    if ohlcv is None or ohlcv.empty:
        return ScoreResult("pending", "PENDING", None, None, 0)
    start_loc = start_loc_after_entry(ohlcv, pos.get("entry_bar_time"))
    if start_loc is None:
        last = _float_or_none(ohlcv["Close"].iloc[-1])
        return ScoreResult("pending", "PENDING", last, None, 0)
    path = str((cfg.get("barrier") or {}).get("path") or "high_low")
    horizon = int(pos.get("horizon") or cfg.get("horizon") or 8)
    sl = _float_or_none(pos.get("sl"))
    tp = _float_or_none(pos.get("tp"))
    entry = _float_or_none(pos.get("entry_price")) or 0.0
    status, exit_px, reason, bars = walk_barriers(
        ohlcv,
        start_loc=start_loc,
        horizon=horizon,
        side=str(pos.get("side") or ""),
        entry=entry,
        sl=sl,
        tp=tp,
        path=path,
    )
    spread = _float_or_none(pos.get("spread_frac")) or 0.0
    if status == "closed" and reason:
        outcome = outcome_from_exit(
            reason,
            side=str(pos.get("side") or ""),
            entry=entry,
            exit_px=exit_px,
            spread_frac=spread,
        )
        return ScoreResult("closed", outcome, exit_px, reason, bars)
    return ScoreResult("pending", "PENDING", exit_px, None, bars)


def filter_journal(
    rows: list[dict[str, Any]],
    *,
    wrong_only: bool = False,
    session: str | None = None,
    validity: str | None = None,
    conf: str | None = None,
) -> list[dict[str, Any]]:
    """Filter journal rows for mistake review. Outcomes are normalized."""

    def _skip_all(value: str | None) -> bool:
        return value is None or str(value).strip() in {"", "All", "all", "ALL"}

    out: list[dict[str, Any]] = []
    for row in rows or []:
        rec = dict(row)
        rec["outcome"] = normalize_outcome(rec)
        if wrong_only and rec["outcome"] != "WRONG":
            continue
        if not _skip_all(session) and str(rec.get("session") or "n/a") != session:
            continue
        if not _skip_all(validity) and str(rec.get("validity_at_entry") or "n/a") != validity:
            continue
        if not _skip_all(conf) and str(rec.get("conf_bucket") or "n/a") != conf:
            continue
        out.append(rec)
    return out


def _rate_by(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(str(r.get(key) or "n/a"), []).append(r)
    out = []
    for name, group in sorted(buckets.items()):
        w = sum(1 for x in group if x.get("outcome") == "WRONG")
        ok = sum(1 for x in group if x.get("outcome") == "RIGHT")
        n = ok + w
        out.append(
            {
                "bucket": name,
                "n_scored": n,
                "right": ok,
                "wrong": w,
                "hit_rate": None if n == 0 else ok / n,
                "error_rate": None if n == 0 else w / n,
            }
        )
    return out


def journal_aggregates(
    closed: list[dict[str, Any]],
    open_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    closed_n = [dict(r) for r in (closed or [])]
    timeout_n = sum(
        1
        for r in closed_n
        if str(r.get("outcome") or "").upper() == "TIMEOUT"
        or str(r.get("exit_reason") or "").lower() == "timeout"
    )
    for r in closed_n:
        r["outcome"] = normalize_outcome(r)
    pending_n = len(open_rows or [])
    right = [r for r in closed_n if r.get("outcome") == "RIGHT"]
    wrong = [r for r in closed_n if r.get("outcome") == "WRONG"]
    flat = [r for r in closed_n if r.get("outcome") == "FLAT"]
    scored = len(right) + len(wrong)
    hit = (len(right) / scored) if scored else None
    err = (len(wrong) / scored) if scored else None
    scored_rows = [r for r in closed_n if r.get("outcome") in SCORED_OUTCOMES]
    return {
        "n_closed": len(closed_n),
        "n_pending": pending_n,
        "n_scored": scored,
        "right": len(right),
        "wrong": len(wrong),
        "timeout": timeout_n,
        "flat": len(flat),
        "hit_rate": hit,
        "error_rate": err,
        "by_pair": _rate_by(scored_rows, "pair"),
        "by_session": _rate_by(scored_rows, "session"),
        "by_validity": _rate_by(scored_rows, "validity_at_entry"),
        "by_confidence": _rate_by(scored_rows, "conf_bucket"),
        "notes": improvement_notes(closed_n, err, scored_rows),
    }


def _err(rows: list[dict[str, Any]]) -> float | None:
    n = len(rows)
    if not n:
        return None
    return sum(1 for r in rows if r.get("outcome") == "WRONG") / n


def improvement_notes(
    closed: list[dict[str, Any]],
    error_rate: float | None,
    scored_rows: list[dict[str, Any]],
) -> list[str]:
    """Short, aggregate-driven hints. Never claims a live edge."""
    notes = [
        "Paper lookback only — not linked to any broker and not a live edge. "
        "RIGHT = TP before SL (or a positive signed move at the horizon); "
        "WRONG = SL first (or a non-positive move at the horizon); "
        "PENDING until enough cached bars exist.",
        "If a live backend is added later, it should implement BrokerPort "
        "(submit / close / list_positions / list_fills). Do not call a vendor "
        "SDK from the UI.",
    ]
    if not scored_rows:
        notes.append(
            "No scored (RIGHT/WRONG) paper trades yet. PENDING until enough bars "
            "pass to hit TP, SL, or the label horizon."
        )
        return notes

    n = len(scored_rows)
    hit = 1.0 - error_rate if error_rate is not None else None
    if hit is not None:
        notes.append(
            f"Paper hit rate {100 * hit:.0f}% on {n} scored trade"
            f"{'' if n == 1 else 's'} — practice stats, not evidence of an edge."
        )

    stale = [r for r in scored_rows if str(r.get("validity_at_entry")).upper() == "STALE"]
    ok = [r for r in scored_rows if str(r.get("validity_at_entry")).upper() == "OK"]
    se, oe = _err(stale), _err(ok)
    if se is not None and len(stale) >= 2 and se >= 0.5:
        notes.append(
            "Don't take paper entries on STALE flashes — that bucket was wrong "
            "at least half the time here. Refresh (Fetch) first."
        )
    elif se is not None and oe is not None and se > oe:
        notes.append(
            "Avoid entries when validity is STALE — that bucket was wrong more often than OK."
        )
    elif stale:
        notes.append("Prefer OK validity; STALE flashes are not live calls on the board.")

    low = [r for r in scored_rows if r.get("conf_bucket") == "<0.40"]
    high = [r for r in scored_rows if r.get("conf_bucket") == ">=0.60"]
    le, he = _err(low), _err(high)
    if le is not None and he is not None and le > he:
        notes.append(
            "Raise signals.min_confidence — the low-confidence bucket was wrong more often."
        )
    elif le is not None and len(low) >= 2 and le >= 0.5:
        notes.append(
            "Low-confidence (<0.40) entries were wrong at least half the time — skip or wait."
        )

    sessions: dict[str, list[dict[str, Any]]] = {}
    for r in scored_rows:
        sessions.setdefault(str(r.get("session") or "n/a"), []).append(r)
    worst_name = None
    worst_err = -1.0
    for name, group in sessions.items():
        if name in {"n/a", "closed", "off"}:
            continue
        e = _err(group)
        if e is None or len(group) < 2:
            continue
        if e > worst_err:
            worst_err = e
            worst_name = name
    if (
        worst_name is not None
        and worst_err >= 0.5
        and (error_rate is None or worst_err > error_rate)
    ):
        notes.append(
            f"Session `{worst_name}` was wrong more often than the rest of the book — "
            "skip or wait for a clearer session rather than forcing a paper fill."
        )

    against = 0
    for r in scored_rows:
        if r.get("outcome") != "WRONG":
            continue
        bias = str(r.get("news_bias") or "").lower()
        side = str(r.get("side") or "").upper()
        if side == "BUY" and bias == "bearish":
            against += 1
        elif side == "SELL" and bias == "bullish":
            against += 1
    if against >= 2:
        notes.append(
            "Several wrongs went against the news-bias note. Keep news as context, "
            "not a trigger — an event gate is a later filter, not auto-trading."
        )
    elif error_rate is not None and error_rate >= 0.5:
        notes.append(
            "Scored error rate is high. Consider an event gate (skip around known news) "
            "and keep the news lane as context, not a trigger."
        )
    else:
        notes.append(
            "News remains context only. An event gate (skip entries around scheduled "
            "releases) is a later filter, not auto-trading."
        )
    return notes
