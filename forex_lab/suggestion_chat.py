"""Friendly suggestion-board chat lines from desk snapshots (decision aid only)."""
from __future__ import annotations

from typing import Any


HONESTY = (
    "Decision aid only - not broker quotes, not auto-trade, not a promote signal."
)

_PLACEHOLDER = {
    "",
    "-",
    "--",
    "---",
    "\u2014",
    "\u2013",
    "\u2212",
    "?",
    "N/A",
    "NA",
    "NONE",
    "NULL",
    "\ufffd",
}


def _px(pair: str, value: float | None) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
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


def _norm_side(raw: Any) -> str:
    """BUY/SELL/HOLD or empty; strip em-dash placeholders and mojibake."""
    s = str(raw or "").strip().upper()
    if not s or s in _PLACEHOLDER:
        return ""
    if s in {"BUY", "SELL", "HOLD"}:
        return s
    # Non-alpha junk (encoding-broken dashes) -> empty
    if not any(ch.isalpha() for ch in s):
        return ""
    return s


def _clean_gate(gate: str) -> str:
    if not gate:
        return ""
    g = str(gate)
    for bad in (
        " \u00b7 ",
        "\u00b7",
        " Â· ",
        " Â·",
        "Â·",
        " A· ",
        " · ",
        " ·",
        "·",
        " � ",
        " �",
        "�",
    ):
        if bad in g:
            g = g.replace(bad, " | ")
    while "  " in g:
        g = g.replace("  ", " ")
    while " |  | " in g:
        g = g.replace(" |  | ", " | ")
    return g.strip(" |")


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
    signal = _norm_side(row.get("signal"))
    raw = _norm_side(row.get("raw_signal"))
    gate = _clean_gate(str(row.get("gate_reason") or "").strip())
    status = str(row.get("status") or "").strip().lower()
    last = row.get("last")
    target = row.get("target")
    stop = row.get("stop")
    conf = _conf_text(row.get("confidence"))
    is_active = bool(active) and pair == str(active).upper()
    kind = "status"
    body: str

    if open_position and str(open_position.get("pair") or "").upper() == pair:
        side = _norm_side(
            open_position.get("side") or open_position.get("trigger")
        ) or "position"
        entry = open_position.get("entry_price")
        entry_txt = open_position.get("entry_price_text") or _px(
            pair, entry if isinstance(entry, (int, float)) else None
        )
        pnl = str(open_position.get("pnl_text") or "").strip()
        pnl_bit = f" ({pnl})" if pnl else ""
        flash = signal if signal in {"BUY", "SELL"} else raw
        if flash and side in {"BUY", "SELL"} and flash != side and flash in {"BUY", "SELL"}:
            kind = "close_hint"
            body = (
                f"{pair}: consider closing paper {side} opened @ {entry_txt}{pnl_bit} - "
                f"flash is now {flash} (research hint, not an auto-close)."
            )
        elif signal == "HOLD" and gate:
            kind = "close_hint"
            body = (
                f"{pair}: open paper {side} @ {entry_txt}{pnl_bit} still open; "
                f"live flash HOLD ({gate}). No auto-close."
            )
        else:
            kind = "open_pos"
            mon = f"flash {flash}" if flash in {"BUY", "SELL"} else "monitoring"
            body = f"{pair}: paper {side} open @ {entry_txt}{pnl_bit} - {mon}."
    elif signal in {"BUY", "SELL"}:
        kind = "open_window"
        bits = [
            f"{pair}: open {signal} window now @ {_px(pair, last if isinstance(last, (int, float)) else None)}"
        ]
        if isinstance(target, (int, float)):
            bits.append(f"target @ {_px(pair, float(target))}")
        if isinstance(stop, (int, float)):
            bits.append(f"stop/limit @ {_px(pair, float(stop))}")
        if conf:
            bits.append(f"conf {conf}")
        body = " - ".join(bits[:2])
        if len(bits) > 2:
            body += ", " + ", ".join(bits[2:])
        body += " (research, not an order)."
    elif raw in {"BUY", "SELL"} and (signal in {"HOLD", ""} or gate):
        kind = "window_gone"
        why = gate or "gated to HOLD"
        body = f"{pair}: open window gone. Don't {raw.lower()} now - {why}."
    elif signal == "HOLD":
        kind = "hold"
        body = f"{pair}: HOLD - no directional flash right now."
        if conf:
            body += f" (conf {conf})"
        if gate:
            body += f" [{gate}]"
    elif status in {"need_train", "untrained"}:
        kind = "status"
        body = f"{pair}: quiet - no trained flash yet (Fetch/Train)."
    elif status in {"error", "fail", "failed"}:
        kind = "status"
        detail = str(row.get("details") or row.get("validity_reason") or "").strip()
        detail = _clean_gate(detail)
        body = f"{pair}: data/model issue - check Fetch."
        if detail and len(detail) < 80:
            body = f"{pair}: data/model issue - {detail}."
    else:
        kind = "status"
        body = f"{pair}: quiet - waiting on a clean flash."

    if is_active and kind in {"open_window", "window_gone", "close_hint", "open_pos"}:
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
                if enriched.get(key) in (None, "", "-", "\u2014", "\u2013") and brief_primary.get(key) not in (None, ""):
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
