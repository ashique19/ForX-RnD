"""Friendly suggestion-board chat lines from desk snapshots (decision aid only)."""

from __future__ import annotations
import re

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

def _gate_conf_text(gate: str) -> str | None:
    """Recover a model confidence when the board row omitted its numeric field."""
    m = re.search(r"\bconf(?:idence)?\s*=\s*(\d+(?:\.\d+)?)%?", gate or "", re.I)
    return _conf_text(float(m.group(1))) if m else None

def _below_min_conf(gate: str) -> bool:
    """Identify a confidence gate without changing that gate's enforcement."""
    g = (gate or "").lower()
    return bool(
        re.search(r"\bconf(?:idence)?\s*=\s*\d+(?:\.\d+)?%?\s*<\s*min(?:_?conf(?:idence)?)?\b", g)
        or "below min_conf" in g
        or "below min confidence" in g
    )


def _mtf_conflict(gate: str) -> bool:
    """True when gate text says HTF/MTF conflict blocked the flash."""
    g = (gate or "").lower()
    return "mtf conflict" in g or ("mtf" in g and "conflict" in g)


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


# Research pin-skip pairs: show honesty, never "Train idle" (pip/BA contract missing).
PIN_SKIP_PAIRS = frozenset({"BTCUSD"})
PIN_SKIP_REASON = {
    "BTCUSD": (
        "research pin-skip - pip/tick contract + mid-only feed; "
        "not a Train candidate until contract written (see _BTCUSD_PIN_SKIP note)"
    ),
}

def _price_plausible(pair: str, last: float | None, stop: Any = None, target: Any = None) -> bool:
    """Reject crossed FX/BTC scales (e.g. BTCUSD @ 0.69 with FX stops)."""
    if last is None:
        return False
    try:
        last_f = float(last)
    except (TypeError, ValueError):
        return False
    u = str(pair or "").upper()
    if u.startswith("BTC") or u.endswith("BTC"):
        if last_f < 1000:
            return False
    elif "JPY" in u:
        if not (20 < last_f < 500):
            return False
    elif u.startswith("XAU"):
        if last_f < 100:
            return False
    else:
        # major FX quote
        if not (0.1 < last_f < 5.0):
            return False
    for v in (stop, target):
        if isinstance(v, (int, float)):
            vf = float(v)
            if last_f > 0 and (vf <= 0 or abs(vf - last_f) / last_f > 0.15):
                # stop/target more than 15% from mid is suspicious for H1 FX; for BTC allow wider
                if u.startswith("BTC"):
                    if abs(vf - last_f) / last_f > 0.5:
                        return False
                else:
                    return False
    return True




def humanize_gate(gate: str) -> str:
    """Make weekday/conf mute reasons desk-readable."""
    import re

    g = _clean_gate(gate)
    if not g:
        return ""
    if g.startswith("muted "):
        return g
    m = re.search(
        r"weekday_gate blocks ([A-Za-z,]+) \(UTC\); today=([A-Za-z]+)",
        g,
        flags=re.I,
    )
    if not m:
        return g
    names = m.group(1)
    today_raw = m.group(2)
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    short = {d: i for i, d in enumerate(order)}
    day_list = [x.strip() for x in names.split(",") if x.strip()]
    today_n = today_raw[:3].title()
    if today_n not in short:
        today_n = today_raw.title()[:3]
    others = [d[:3].title() for d in day_list if d[:3].title() != today_n]
    also = f"; also {','.join(others)}" if others else ""
    lifts = ""
    wi = short.get(today_n)
    if wi is not None:
        block_set = {short.get(d[:3].title(), -1) for d in day_list}
        for step in range(1, 8):
            cand = (wi + step) % 7
            if cand not in block_set:
                lifts = f" - lifts {order[cand]} UTC"
                break
    muted = f"muted {today_n} (UTC weekday gate{also}){lifts}"
    return re.sub(
        r"weekday_gate blocks [A-Za-z,]+ \(UTC\); today=[A-Za-z]+",
        muted,
        g,
        count=1,
        flags=re.I,
    )



def prefer_mute_why(gate: str) -> str:
    """Prefer weekday mute clause; keep conf as a short trailing note."""
    import re

    g = humanize_gate(gate)
    if not g:
        return "gated to HOLD"
    if "muted " not in g.lower():
        return g
    # Split on | and put muted first
    parts = [x.strip() for x in g.split("|") if x.strip()]
    muted = [x for x in parts if x.lower().startswith("muted ")]
    confs = [x for x in parts if x.lower().startswith("conf=")]
    other = [x for x in parts if x not in muted and x not in confs]
    bits = muted + other
    if confs:
        bits.append("also " + confs[0])
    return " | ".join(bits) if bits else g


def fingerprint_stable_body(kind: str, body: str, gate: str = "") -> str:
    """Drop ticking conf floats from window_gone fingerprints to rate-limit spam."""
    import re

    if kind not in {"window_gone", "open_window"}:
        return body.strip()
    stable = body
    stable = re.sub(r"conf=\d+(?:\.\d+)?\s*<\s*min\s*\d+(?:\.\d+)?\s*\|?\s*", "", stable)
    stable = re.sub(r"\(conf\s*\d+%\)", "", stable)
    stable = re.sub(r"still within \d+(?:\.\d+)?%\s+(?:BUY|SELL)\s+conf", "still within PCT direction conf", stable, flags=re.I)
    stable = re.sub(r"\bconf\s+\d+(?:\.\d+)?%", "conf PCT", stable, flags=re.I)
    stable = re.sub(r"(@|\bat)\s+\d+(?:\.\d+)?", r"\1 PX", stable, flags=re.I)
    gate_l = (gate or "").lower()
    body_l = body.lower()
    if "weekday" in gate_l or "muted " in gate_l or "weekday" in body_l or "muted " in body_l:
        stable = re.sub(r"conf=[^|\-]+\|\s*", "", stable)
    stable = re.sub(r"\|\s*also\s*\.?\s*", " ", stable)
    stable = re.sub(r"\balso\s*\.?\s*$", "", stable)
    return re.sub(r"\s{2,}", " ", stable).strip(" |")


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
    gate = humanize_gate(str(row.get("gate_reason") or "").strip())
    status = str(row.get("status") or "").strip().lower()
    # Model / joblib / retrain / challenger copy belongs in ModelBuildStrip — not suggestion chat.
    # Keep BTCUSD pin-skip honesty (research skip), which is not a retrain banner.
    if status in {"need_train", "untrained"} and pair not in PIN_SKIP_PAIRS:
        return None
    last = _last_num(row)
    last_txt = _px(pair, last) if last is not None else None
    target = row.get("target")
    stop = row.get("stop")
    conf = _conf_text(row.get("confidence")) or _gate_conf_text(gate)
    is_active = bool(active) and pair == str(active).upper()
    kind = "status"
    body: str
    # Pin-skip pairs never emit BUY/SELL advisories (wrong contract / scale).
    if pair in PIN_SKIP_PAIRS:
        why = PIN_SKIP_REASON.get(pair) or "research pin-skip (not a Train candidate)."
        kind = "status"
        if last_txt:
            body = f"{pair}: watching @ {last_txt} - {why}"
        else:
            body = f"{pair}: {why}"
        fp = fingerprint_line(pair, kind, "pin_skip")
        return {
            "id": fp,
            "pair": pair,
            "kind": kind,
            "weight": "active" if is_active else "light",
            "text": body,
            "signal": None,
            "raw_signal": None,
            "confidence": None,
            "honesty": HONESTY,
        }
    # Drop open/mute advisories when mid/stop/target scale is nonsense.
    _dir = signal in {"BUY", "SELL"} or raw in {"BUY", "SELL"}
    if _dir and not _price_plausible(pair, last, stop, target):
        kind = "status"
        body = (
            f"{pair}: levels look wrong vs mid "
            f"({last_txt or '-'}) - suppressed advisory until Fetch/remesh."
        )
        fp = fingerprint_line(pair, kind, "bad_scale")
        return {
            "id": fp,
            "pair": pair,
            "kind": kind,
            "weight": "active" if is_active else "light",
            "text": body,
            "signal": signal or None,
            "raw_signal": raw or None,
            "confidence": row.get("confidence"),
            "honesty": HONESTY,
        }
    if open_position and str(open_position.get("pair") or "").upper() == pair:
        side = _norm_side(
            open_position.get("side") or open_position.get("trigger")
        ) or "position"
        entry = open_position.get("entry_price")
        entry_txt = open_position.get("entry_price_text") or _px(
            pair, entry if isinstance(entry, (int, float)) else None
        )
        pnl = str(open_position.get("pnl_text") or "").strip()
        pnl = _clean_gate(pnl.replace("·", " | "))
        pnl_bit = f" ({pnl})" if pnl and pnl not in _PLACEHOLDER else ""
        extras: list[str] = []
        source = str(open_position.get("source") or "").strip()
        if source:
            extras.append(f"via {source}")
        dur = str(open_position.get("duration") or "").strip()
        if dur and dur not in _PLACEHOLDER and dur not in {" - ", "–", "-"}:
            extras.append(f"held {dur}")
        opened = str(open_position.get("entry_time_dhaka") or "").strip()
        if opened and len(extras) < 3:
            extras.append(f"in since {opened}")
        sl_v = open_position.get("sl")
        tp_v = open_position.get("tp")
        if isinstance(sl_v, (int, float)):
            extras.append(f"sl {_px(pair, float(sl_v))}")
        if isinstance(tp_v, (int, float)) and len(extras) < 5:
            extras.append(f"tp {_px(pair, float(tp_v))}")
        extra_bit = (" - " + ", ".join(extras[:4])) if extras else ""
        flash = signal if signal in {"BUY", "SELL"} else raw
        if flash and side in {"BUY", "SELL"} and flash != side and flash in {"BUY", "SELL"}:
            kind = "close_hint"
            body = (
                f"{pair}: consider closing paper {side} opened @ {entry_txt}{pnl_bit}"
                f"{extra_bit} - flash is now {flash} (research hint, not an auto-close)."
            )
        elif signal == "HOLD" and gate:
            kind = "close_hint"
            body = (
                f"{pair}: open paper {side} @ {entry_txt}{pnl_bit} still open"
                f"{extra_bit}; live flash HOLD ({gate}). No auto-close."
            )
        else:
            kind = "open_pos"
            mon = f"flash {flash}" if flash in {"BUY", "SELL"} else "monitoring"
            body = f"{pair}: paper {side} open @ {entry_txt}{pnl_bit}{extra_bit} - {mon}."
    elif signal in {"BUY", "SELL"}:
        kind = "open_window"
        px_txt = _px(pair, last) if last is not None else "-"
        has_target = isinstance(target, (int, float))
        has_stop = isinstance(stop, (int, float))
        side = signal
        conf_bit = conf or "n/a"
        body = f"{pair} still within {conf_bit} {side} conf. {side.capitalize()} and hold @ {px_txt}."
        if has_stop and has_target:
            body += f" Stoploss at {_px(pair, float(stop))}, close at {_px(pair, float(target))}."
        elif has_stop:
            body += f" Stoploss at {_px(pair, float(stop))}."
        elif has_target:
            body += f" Close at {_px(pair, float(target))}."
        else:
            body += " Stoploss/close levels not set by gates yet."
        body += " Research levels only (not broker orders)."
    elif raw in {"BUY", "SELL"} and (signal in {"HOLD", ""} or gate):
        why = prefer_mute_why(gate) if gate else "gated to HOLD"
        muted = "muted " in why.lower() or "weekday gate" in why.lower()
        below_min = _below_min_conf(gate)
        mtf_blocked = _mtf_conflict(why) or _mtf_conflict(gate)
        px_txt = _px(pair, last) if last is not None else "-"
        has_target = isinstance(target, (int, float))
        has_stop = isinstance(stop, (int, float))
        if mtf_blocked:
            # Desk HOLD wins over model lean / consensus — never frame as Buy/Sell setup.
            kind = "window_gone"
            body = (
                f"{pair}: desk HOLD — MTF conflict wins over model {raw}"
                f" ({why}). Don't {raw.lower()} now. "
                f"No actionable target/stop while gated."
            )
        elif muted or below_min:
            # Keep raw direction visible as a decision aid while the live gate stays closed.
            kind = "open_window"
            conf_bit = conf or "n/a"
            label = "advisory" if muted else "lean"
            body = f"{pair} {label}: still within {conf_bit} {raw} conf. {raw.capitalize()} and hold @ {px_txt}."
            if has_stop and has_target:
                body += f" Stoploss at {_px(pair, float(stop))}, close at {_px(pair, float(target))}."
            elif has_stop:
                body += f" Stoploss at {_px(pair, float(stop))}."
            elif has_target:
                body += f" Close at {_px(pair, float(target))}."
            else:
                body += " Stoploss/close levels not set by gates yet."
            if muted:
                body += f" Weekday mute on ({why}) - not opening live; decision aid / paper journal only."
            else:
                body += f" Below min_conf; live gate remains closed ({why}). Decision aid only."
        else:
            kind = "window_gone"
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
    elif pair in PIN_SKIP_PAIRS:
        kind = "status"
        why = PIN_SKIP_REASON.get(pair) or "research pin-skip (not a Train candidate)."
        if last_txt:
            body = f"{pair}: watching @ {last_txt} - {why}"
        else:
            body = f"{pair}: {why}"
    elif status in {"need_fetch", "missing"}:
        kind = "status"
        body = f"{pair}: quiet - need Fetch for fresh bars (OHLCV only; no Train)."
    elif status in {"error", "fail", "failed"}:
        kind = "status"
        detail = str(row.get("details") or row.get("validity_reason") or "").strip()
        detail = _clean_gate(detail)
        # Keep fetch/data failures trade-adjacent; never inject retrain/joblib/challenger copy.
        body = f"{pair}: data issue - check Fetch."
        if detail and len(detail) < 80 and not re.search(
            r"challenger|joblib|retrain|model.?age|champion", detail, re.I
        ):
            body = f"{pair}: data issue - {detail}."
        elif detail and re.search(r"challenger|joblib|retrain|model.?age|champion", detail, re.I):
            return None
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
    if kind == "status" and pair in PIN_SKIP_PAIRS:
        fp = fingerprint_line(pair, kind, "pin_skip")
    elif kind == "status" and status in {"need_train", "untrained", "need_fetch", "missing"}:
        fp = fingerprint_line(pair, kind, status or "status")
    elif kind in {"window_gone", "open_window"}:
        fp = fingerprint_line(pair, kind, fingerprint_stable_body(kind, body, gate))
    else:
        fp = fingerprint_line(pair, kind, body)
    out = {
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
    side_for_paper = signal if signal in {"BUY", "SELL"} else (raw if raw in {"BUY", "SELL"} else None)
    if kind == "open_window" and side_for_paper in {"BUY", "SELL"}:
        body_l = body.lower()
        # Fri clarity: weekday mute chip != below-min chip (both can soft-close live).
        weekday_muted = (
            "weekday mute on" in body_l
            or "live gate still muted" in body_l
            or ("not opening" in body_l and "weekday" in body_l)
        )
        below_min = "below min_conf" in body_l or _below_min_conf(gate)
        if weekday_muted:
            paper_suffix = " (weekday mute)"
        elif below_min:
            paper_suffix = " (below min)"
        else:
            paper_suffix = ""
        out["paper_action"] = {
            "pair": pair,
            "side": side_for_paper,
            "can_paper_open": True,
            "label": f"Journal {side_for_paper}{paper_suffix}",
        }
        out["signal"] = side_for_paper
        out["muted_advisory"] = bool(weekday_muted)
        out["below_min_conf"] = bool(below_min)
    if kind == "close_hint" and open_position:
        pos_id = str(open_position.get("id") or "").strip() or None
        close_side = _norm_side(
            open_position.get("side") or open_position.get("trigger")
        )
        if close_side in {"BUY", "SELL"}:
            out["paper_action"] = {
                "pair": pair,
                "side": "CLOSE",
                "can_paper_close": True,
                "position_id": pos_id,
                "label": "Journal close",
            }
    return out


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



def _conf_rank(raw: Any) -> float:
    try:
        if raw is None:
            return -1.0
        v = float(raw)
    except (TypeError, ValueError):
        return -1.0
    if v > 1.0:
        v = v / 100.0
    return v if v >= 0 else -1.0


_DESK_CALL_RANK = {
    "close_hint": 100,
    "open_window": 90,
    "open_pos": 70,
    "window_gone": 30,
    "hold": 5,
    "paper_closed": 10,
    "status": 8,
}


def pick_desk_call(
    lines: list[dict[str, Any]],
    *,
    active: str | None = None,
) -> dict[str, Any] | None:
    """Best actionable / watch line across pairs for a sticky desk-call strip."""
    active_u = str(active or "").upper() or None
    actionable = [
        ln
        for ln in lines
        if str(ln.get("kind") or "") in {"close_hint", "open_window", "open_pos"}
    ]

    def sort_key(ln: dict[str, Any]) -> tuple:
        kind = str(ln.get("kind") or "")
        weight_bonus = 1 if str(ln.get("weight") or "") == "active" else 0
        pair_bonus = 1 if str(ln.get("pair") or "").upper() == active_u else 0
        # Fully open live path outranks below-min lean and weekday-mute advisory.
        unmuted_bonus = 0
        if (
            kind == "open_window"
            and not ln.get("muted_advisory")
            and not ln.get("below_min_conf")
        ):
            unmuted_bonus = 1
        # Among same kind/mute tier: higher conf wins; active pair is only a tie-break.
        return (
            _DESK_CALL_RANK.get(kind, 0),
            unmuted_bonus,
            _conf_rank(ln.get("confidence")),
            weight_bonus,
            pair_bonus,
        )

    if actionable:
        best = max(actionable, key=sort_key)
        kind = str(best.get("kind") or "")
        pair = str(best.get("pair") or "").upper()
        side = _norm_side(best.get("signal") or best.get("raw_signal"))
        if kind == "open_window":
            if best.get("muted_advisory"):
                note = "muted weekday"
                headline = (
                    f"Desk call: {pair} advisory {side or 'window'} - "
                    f"{note} (not opening; paper journal ok)."
                )
            elif best.get("below_min_conf"):
                note = "below min_conf"
                headline = (
                    f"Desk call: {pair} advisory {side or 'window'} - "
                    f"{note} (not opening; paper journal ok)."
                )
            else:
                headline = (
                    f"Desk call: {pair} {side or 'window'} - "
                    "actionable research window (paper only)."
                )
        elif kind == "close_hint":
            headline = (
                f"Desk call: {pair} - consider paper close "
                "(research hint, not auto)."
            )
        else:
            headline = f"Desk call: {pair} paper still open - monitor (no auto)."
        conf_v = _conf_rank(best.get("confidence"))
        conf_pct = int(round(conf_v * 100)) if conf_v >= 0 else None
        muted_adv = bool(best.get("muted_advisory")) if kind == "open_window" else False
        below_min = bool(best.get("below_min_conf")) if kind == "open_window" else False
        return {
            "id": f"desk_call|{best.get('id')}",
            "kind": "desk_call",
            "pair": pair,
            "source_kind": kind,
            "headline": headline,
            "text": str(best.get("text") or ""),
            "weight": "active",
            "signal": best.get("signal"),
            "raw_signal": best.get("raw_signal"),
            "confidence": best.get("confidence"),
            "conf_pct": conf_pct,
            "muted_advisory": muted_adv,
            "below_min_conf": below_min,
            "honesty": HONESTY,
            "actionable": True,
            "paper_action": best.get("paper_action"),
        }

    watch = [
        ln
        for ln in lines
        if str(ln.get("kind") or "") in {"window_gone", "hold"}
        and str(ln.get("pair") or "").upper() not in PIN_SKIP_PAIRS
    ]
    if not watch:
        return {
            "id": "desk_call|none",
            "kind": "desk_call",
            "pair": active_u,
            "source_kind": "none",
            "headline": "Desk call: no clean flash across watchlist right now.",
            "text": "Waiting on board flashes. Decision aid only - not auto-trade.",
            "weight": "active",
            "signal": None,
            "raw_signal": None,
            "confidence": None,
            "honesty": HONESTY,
            "actionable": False,
            "paper_action": None,
        }
    best = max(watch, key=sort_key)
    pair = str(best.get("pair") or "").upper()
    kind = str(best.get("kind") or "")
    muted = (
        "muted" in str(best.get("text") or "").lower()
        or "weekday" in str(best.get("text") or "").lower()
    )
    if muted:
        headline = (
            f"Desk call: no open window - weekday mute in force. "
            f"Closest watch: {pair} (gated; not actionable)."
        )
    elif kind == "hold":
        headline = f"Desk call: no open window. Quietest watch: {pair} HOLD (research)."
    else:
        headline = f"Desk call: no open window. Closest: {pair} gated (not actionable)."
    return {
        "id": f"desk_call|{best.get('id')}",
        "kind": "desk_call",
        "pair": pair,
        "source_kind": kind,
        "headline": headline,
        "text": str(best.get("text") or ""),
        "weight": "active",
        "signal": best.get("signal"),
        "raw_signal": best.get("raw_signal"),
        "confidence": best.get("confidence"),
        "honesty": HONESTY,
        "actionable": False,
        "paper_action": None,
    }


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
    # Hold is low priority — actionable / muted-advisory first on the board.
    _board_rank = {
        "close_hint": 0,
        "open_window": 1,
        "open_pos": 2,
        "window_gone": 3,
        "status": 4,
        "paper_closed": 5,
        "hold": 9,
    }
    has_action = any(str(ln.get("kind") or "") in {"close_hint", "open_window", "open_pos"} for ln in lines)
    if has_action:
        lines = [
            ln
            for ln in lines
            if str(ln.get("kind") or "") != "hold"
            or str(ln.get("pair") or "").upper() == (active_u or "")
        ]
    lines.sort(
        key=lambda ln: (
            _board_rank.get(str(ln.get("kind") or ""), 6),
            # Fully open first, then below-min lean, then weekday-mute advisory.
            (
                2
                if str(ln.get("kind") or "") == "open_window" and ln.get("muted_advisory")
                else 1
                if str(ln.get("kind") or "") == "open_window" and ln.get("below_min_conf")
                else 0
            ),
            # Higher conf before active-weight tie-break (active still highlighted in UI).
            -_conf_rank(ln.get("confidence")),
            0 if str(ln.get("weight") or "") == "active" else 1,
            str(ln.get("pair") or ""),
        )
    )
    desk_call = pick_desk_call(lines, active=active_u)
    return {
        "ok": True,
        "active": active_u,
        "count": len(lines),
        "lines": lines,
        "desk_call": desk_call,
        "honesty": HONESTY,
        "decision_aid": True,
        "auto_trade": False,
        "paper_closed_lines": closed_n,
        "kinds": new_kinds,
    }
