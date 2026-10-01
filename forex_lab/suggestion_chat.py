"""Friendly suggestion-board chat lines from desk snapshots (decision aid only)."""
from __future__ import annotations

from typing import Any


HONESTY = (
    "Decision aid only — not broker quotes, not auto-trade, not a promote signal."
)


def _px(pair: str, value: float | None) -> str:
    if value is None:
        return "—"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    upper = str(pair or "").upper()
    if "JPY" in upper:
        return f"{v:.3f}"
    if upper.startswith("XAU") or upper.startswith("XAG") or upper.startswith("BTC"):
        return f"{v:.2f}"
    return f"{v:.5f}"


def _conf_text(raw: Any) -> str | None:
    try:
        if raw is None:
            return None
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v > 1.0:
        v = v / 100.0
    if v < 0:
        return None
    return f"{v:.0%}" if v <= 1 else f"{v:.2f}"


def fingerprint_line(pair: str, kind: str, body: str) -> str:
    return f"{str(pair).upper()}|{kind}|{body.strip()}"


def format_board_line(
    row: dict[str, Any],
    *,
    active: str | None = None,
    open_position: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one chat line from a board row (+ optional open paper position)."""
    pair = str(row.get("pair") or "").upper()
    if not pair:
        return None
    signal = str(row.get("signal") or "").upper()
    raw = str(row.get("raw_signal") or "").upper()
    gate = str(row.get("gate_reason") or "").strip()
    last = row.get("last")
    target = row.get("target")
    stop = row.get("stop")
    conf = _conf_text(row.get("confidence"))
    is_active = bool(active) and pair == str(active).upper()
    kind = "status"
    body: str

    if open_position and str(open_position.get("pair") or "").upper() == pair:
        side = str(open_position.get("side") or open_position.get("trigger") or "").upper()
        entry = open_position.get("entry_price")
        entry_txt = open_position.get("entry_price_text") or _px(pair, entry if isinstance(entry, (int, float)) else None)
        flash = signal if signal in {"BUY", "SELL"} else raw
        if flash and side and flash != side and flash in {"BUY", "SELL"}:
            kind = "close_hint"
            body = (
                f"{pair}: consider closing paper {side} opened @ {entry_txt} — "
                f"flash is now {flash} (research hint, not an auto-close)."
            )
        elif signal == "HOLD" and gate:
            kind = "close_hint"
            body = (
                f"{pair}: open paper {side or 'position'} @ {entry_txt} still open; "
                f"live flash HOLD ({gate}). No auto-close."
            )
        else:
            kind = "open_pos"
            body = f"{pair}: paper {side or 'position'} open @ {entry_txt} — monitoring."
    elif signal in {"BUY", "SELL"}:
        kind = "open_window"
        bits = [f"{pair}: open {signal} window now @ {_px(pair, last if isinstance(last, (int, float)) else None)}"]
        if isinstance(target, (int, float)):
            bits.append(f"target @ {_px(pair, float(target))}")
        if isinstance(stop, (int, float)):
            bits.append(f"stop/limit @ {_px(pair, float(stop))}")
        if conf:
            bits.append(f"conf {conf}")
        body = " — ".join(bits[:2])
        if len(bits) > 2:
            body += ", " + ", ".join(bits[2:])
        body += " (research, not an order)."
    elif raw in {"BUY", "SELL"} and (signal in {"HOLD", "—", "", "—"} or gate):
        kind = "window_gone"
        why = gate or "gated to HOLD"
        body = f"{pair}: open window gone. Don't {raw.lower()} now — {why}."
    elif signal == "HOLD":
        kind = "hold"
        body = f"{pair}: HOLD — no directional flash right now."
        if conf:
            body += f" (conf {conf})"
    else:
        kind = "status"
        body = f"{pair}: {signal or '—'} — waiting on a clean flash."

    if is_active and kind in {"open_window", "window_gone", "close_hint"}:
        weight = "active"
    else:
        weight = "active" if is_active else "light"

    fp = fingerprint_line(pair, kind, body)
    return {
        "id": fp,
        "pair": pair,
        "kind": kind,
        "weight": weight,
        "text": body,
        "signal": signal or None,
        "raw_signal": raw or None,
        "confidence": row.get("confidence"),
        "honesty": HONESTY,
    }


def build_suggestion_feed(
    rows: list[dict[str, Any]],
    *,
    active: str | None = None,
    open_positions: list[dict[str, Any]] | None = None,
    brief_primary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble chat lines; Active pair enriched from brief when provided."""
    opens = list(open_positions or [])
    open_by_pair = {str(p.get("pair") or "").upper(): p for p in opens if p.get("pair")}
    active_u = str(active or "").upper() or None
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Active first
    ordered = sorted(
        rows,
        key=lambda r: (
            0 if str(r.get("pair") or "").upper() == active_u else 1,
            str(r.get("pair") or ""),
        ),
    )
    for row in ordered:
        pair = str(row.get("pair") or "").upper()
        enriched = dict(row)
        if brief_primary and pair == active_u:
            for key in ("stop", "target", "confidence", "gate_reason", "raw_signal", "signal", "last", "now"):
                if enriched.get(key) in (None, "", "—") and brief_primary.get(key) not in (None, ""):
                    enriched[key] = brief_primary.get(key)
            if enriched.get("last") is None and brief_primary.get("now") is not None:
                enriched["last"] = brief_primary.get("now")
            if not enriched.get("signal") and brief_primary.get("signal"):
                enriched["signal"] = brief_primary.get("signal")
            if not enriched.get("gate_reason") and brief_primary.get("gate_reason"):
                enriched["gate_reason"] = brief_primary.get("gate_reason")
        line = format_board_line(
            enriched,
            active=active_u,
            open_position=open_by_pair.get(pair),
        )
        if not line:
            continue
        if line["id"] in seen:
            continue
        seen.add(line["id"])
        lines.append(line)

    return {
        "ok": True,
        "active": active_u,
        "count": len(lines),
        "lines": lines,
        "honesty": HONESTY,
        "decision_aid": True,
        "auto_trade": False,
    }
