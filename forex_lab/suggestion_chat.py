"""Friendly suggestion-board chat lines from desk snapshots (decision aid only)."""

from __future__ import annotations

import json
from pathlib import Path

from typing import Any

HONESTY = (
    "Decision aid only - not broker quotes, not auto-trade, not a promote signal."
)

_OPENISH = frozenset({"open_window"})
_GONEISH = frozenset({"window_gone", "hold", "status"})

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

def _last_num(row: dict[str, Any]) -> float | None:
    for key in ("last", "now", "price", "close"):
        val = row.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        try:
            if val is not None and str(val).strip() and str(val) not in _PLACEHOLDER:
                return float(val)
        except (TypeError, ValueError):
            continue
    return None

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
    last = _last_num(row)
    last_txt = _px(pair, last) if last is not None else None
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
            f"{pair}: open {signal} window now @ {_px(pair, last)}"
        ]
        has_target = isinstance(target, (int, float))
        has_stop = isinstance(stop, (int, float))
        if has_target:
            bits.append(f"target @ {_px(pair, float(target))}")
        if has_stop:
            bits.append(f"stop/limit @ {_px(pair, float(stop))}")
        if conf:
            bits.append(f"conf {conf}")
        body = " - ".join(bits[:2])
        if len(bits) > 2:
            body += ", " + ", ".join(bits[2:])
        if has_target or has_stop:
            body += " (research levels from gates, not broker orders)."
        else:
            body += " (research window; target/stop not set by gates yet)."
    elif raw in {"BUY", "SELL"} and (signal in {"HOLD", ""} or gate):
        kind = "window_gone"
        why = gate or "gated to HOLD"
        body = (
            f"{pair}: open window gone. Don't {raw.lower()} now - {why}. "
            f"No actionable target/stop while gated."
        )
    elif signal == "HOLD":
        kind = "hold"
        if last_txt:
            body = f"{pair}: HOLD @ {last_txt} - no directional flash right now."
        else:
            body = f"{pair}: HOLD - no directional flash right now."
        if conf:
            body += f" (conf {conf})"
        if gate:
            body += f" [{gate}]"
    elif status in {"need_train", "untrained"}:
        kind = "status"
        if last_txt:
            body = (
                f"{pair}: watching @ {last_txt} - prices live; Train idle pair "
                f"(or Lab) for a flash (no model yet)."
            )
        else:
            body = (
                f"{pair}: quiet - need Fetch for bars, then Train idle pair "
                f"(or Lab) for a flash."
            )
    elif status in {"need_fetch", "missing"}:
        kind = "status"
        body = f"{pair}: quiet - need Fetch for fresh bars (OHLCV only; no Train)."
    elif status in {"error", "fail", "failed"}:
        kind = "status"
        detail = str(row.get("details") or row.get("validity_reason") or "").strip()
        detail = _clean_gate(detail)
        body = f"{pair}: data/model issue - check Fetch."
        if detail and len(detail) < 80:
            body = f"{pair}: data/model issue - {detail}."
    else:
        kind = "status"
        if last_txt:
            body = f"{pair}: watching @ {last_txt} - waiting on a clean flash."
        else:
            body = f"{pair}: quiet - waiting on a clean flash."
    if is_active and kind in {"open_window", "window_gone", "close_hint", "open_pos"}:
        weight = "active"
    else:
        weight = "active" if is_active else "light"
    # Idle need_train/need_fetch: keep fingerprint stable so light price refreshes
    # do not spam the chat; Watchlist/board rows still show the live last.
    if kind == "status" and status in {"need_train", "untrained", "need_fetch", "missing"}:
        fp = fingerprint_line(pair, kind, status or "status")
    else:
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

def format_closed_trade_line(row: dict[str, Any]) -> dict[str, Any] | None:
    """One friendly diary line for a recently closed paper trade (research only)."""
    pair = str(row.get("pair") or "").upper()
    if not pair:
        return None
    side = _norm_side(row.get("trigger") or row.get("side")) or "trade"
    entry_txt = str(row.get("entry_price_text") or "").strip() or _px(
        pair, row.get("entry_price") if isinstance(row.get("entry_price"), (int, float)) else None
    )
    exit_txt = str(row.get("exit_price_text") or "").strip()
    if not exit_txt or exit_txt in _PLACEHOLDER:
        exit_txt = _px(
            pair, row.get("exit_price") if isinstance(row.get("exit_price"), (int, float)) else None
        )
    pnl = str(row.get("pnl_text") or "").strip()
    pnl = pnl.replace("·", " | ").replace("•", " | ").replace("�", "|")
    pnl = _clean_gate(pnl)
    outcome = str(row.get("outcome") or "").strip().upper()
    reason = str(row.get("exit_reason") or "").strip()
    source = str(row.get("source") or "").strip()
    when = str(row.get("exit_time_dhaka") or "").strip()
    bits = [f"{pair}: paper {side} closed"]
    if entry_txt and entry_txt not in _PLACEHOLDER:
        bits.append(f"in @ {entry_txt}")
    if exit_txt and exit_txt not in _PLACEHOLDER:
        bits.append(f"out @ {exit_txt}")
    body = " ".join(bits)
    extras: list[str] = []
    if pnl and pnl not in _PLACEHOLDER:
        extras.append(pnl)
    if outcome in {"RIGHT", "WRONG", "FLAT"}:
        extras.append(outcome)
    if reason and reason.lower() not in {"duration", "none", "null"}:
        extras.append(reason.replace("_", " "))
    if source:
        extras.append(f"via {source}")
    if when:
        extras.append(when)
    if extras:
        body += " - " + ", ".join(extras[:4])
    body += " (paper diary, not a live fill)."
    kind = "paper_closed"
    fp = fingerprint_line(pair, kind, body)
    return {
        "id": fp,
        "pair": pair,
        "kind": kind,
        "weight": "light",
        "text": body,
        "signal": None,
        "raw_signal": None,
        "confidence": row.get("confidence"),
        "honesty": HONESTY,
        "outcome": outcome or None,
        "exit_reason": reason or None,
    }


def format_transition_line(
    pair: str,
    *,
    prev_kind: str,
    new_kind: str,
    new_line: dict[str, Any],
) -> dict[str, Any] | None:
    """Explicit window open/gone transition (research only)."""
    pair_u = str(pair or "").upper()
    if not pair_u:
        return None
    prev_k = str(prev_kind or "")
    new_k = str(new_kind or "")
    base = str(new_line.get("text") or "").strip()
    if prev_k in _OPENISH and new_k in {"window_gone", "hold"}:
        kind = "window_gone"
        if new_k == "window_gone" and base:
            if base.startswith(f"{pair_u}: open window gone"):
                body = base.replace(
                    f"{pair_u}: open window gone.",
                    f"{pair_u}: window just closed.",
                    1,
                )
            else:
                body = f"{pair_u}: window just closed. {base}"
        else:
            body = (
                f"{pair_u}: window just closed - back to HOLD "
                f"(research; no actionable target/stop)."
            )
    elif prev_k in _GONEISH and new_k == "open_window":
        kind = "open_window"
        if base:
            body = base.replace(
                f"{pair_u}: open ",
                f"{pair_u}: window just opened - ",
                1,
            )
        else:
            body = f"{pair_u}: window just opened (research, not an order)."
    else:
        return None
    weight = str(new_line.get("weight") or "light")
    fp = fingerprint_line(pair_u, f"transition:{prev_k}->{new_k}", body)
    return {
        "id": fp,
        "pair": pair_u,
        "kind": kind,
        "weight": weight,
        "text": body,
        "signal": new_line.get("signal"),
        "raw_signal": new_line.get("raw_signal"),
        "confidence": new_line.get("confidence"),
        "honesty": HONESTY,
        "transition": f"{prev_k}->{new_k}",
    }


def default_state_path() -> Path:
    return Path("data") / "suggestion_board_state.json"


def load_prev_kinds(path: Path | None = None) -> dict[str, str]:
    pth = path or default_state_path()
    try:
        import json as _json
        raw = _json.loads(pth.read_text(encoding="utf-8"))
        kinds = raw.get("kinds") if isinstance(raw, dict) else None
        if isinstance(kinds, dict):
            return {str(k).upper(): str(v) for k, v in kinds.items() if k and v}
    except Exception:
        pass
    return {}


def save_prev_kinds(kinds: dict[str, str], path: Path | None = None) -> None:
    pth = path or default_state_path()
    try:
        import json as _json
        pth.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kinds": {str(k).upper(): str(v) for k, v in kinds.items()}}
        pth.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def build_suggestion_feed(
    rows: list[dict[str, Any]],
    *,
    active: str | None = None,
    open_positions: list[dict[str, Any]] | None = None,
    recent_closed: list[dict[str, Any]] | None = None,
    brief_primary: dict[str, Any] | None = None,
    max_closed_lines: int = 3,
    prev_kinds: dict[str, str] | None = None,
    persist_state: bool = False,
    state_path: Path | None = None,
) -> dict[str, Any]:
    """Assemble chat lines; Active pair enriched from brief when provided.

    recent_closed: optional paper journal closed rows (newest first) for diary lines.
    prev_kinds: optional prior kind-by-pair for window open/gone transitions.
    """
    opens = list(open_positions or [])
    open_by_pair = {str(p.get("pair") or "").upper(): p for p in opens if p.get("pair")}
    active_u = str(active or "").upper() or None
    lines: list[dict[str, Any]] = []
    seen: set[str] = set()
    new_kinds: dict[str, str] = {}
    prior = {str(k).upper(): str(v) for k, v in (prev_kinds or {}).items()}
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
        kind = str(line.get("kind") or "")
        new_kinds[pair] = kind
        prev_k = prior.get(pair) or ""
        if prev_k and prev_k != kind:
            tline = format_transition_line(
                pair, prev_kind=prev_k, new_kind=kind, new_line=line
            )
            if tline and tline["id"] not in seen:
                seen.add(tline["id"])
                lines.append(tline)
                if tline.get("kind") == kind:
                    continue
        if line["id"] in seen:
            continue
        seen.add(line["id"])
        lines.append(line)
    closed_n = 0
    for crow in list(recent_closed or []):
        if closed_n >= max(0, int(max_closed_lines)):
            break
        cline = format_closed_trade_line(crow)
        if not cline or cline["id"] in seen:
            continue
        seen.add(cline["id"])
        lines.append(cline)
        closed_n += 1
    if persist_state:
        merged = dict(prior)
        merged.update(new_kinds)
        save_prev_kinds(merged, path=state_path)
    return {
        "ok": True,
        "active": active_u,
        "count": len(lines),
        "lines": lines,
        "honesty": HONESTY,
        "decision_aid": True,
        "auto_trade": False,
        "paper_closed_lines": closed_n,
        "kinds": new_kinds,
    }
