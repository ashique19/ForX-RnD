"""Decision-support suggestions — never orders.

Combines (1) high-impact event proximity, (2) open paper positions, and
(3) the model flash / MTF badge into advisory cards: no new opens, hold,
close, or tighten SL.

Nothing here calls ``BrokerPort.submit``. The UI may offer a **click** to
apply a tighter paper SL (PaperBroker extra) or to Paper CLOSE. Research
only — not financial advice.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from forex_lab.calendar import (
    CalendarEvent,
    countdown_label,
    event_window,
    events_affecting,
)
from forex_lab.freshness import VALIDITY_ERROR, VALIDITY_MISSING, VALIDITY_STALE
from forex_lab.mtf import MTF_CONFLICT, MtfStatus

ACTION_NO_NEW = "no_new_opens"
ACTION_HOLD = "hold"
ACTION_CLOSE = "close"
ACTION_TIGHTEN = "tighten_sl"
ACTION_NONE = "none"

WINDOW_BEFORE = "before"
WINDOW_DURING = "during"
WINDOW_AFTER = "after"

DISCLAIMER = (
    "Advisory only — not an order, not financial advice. "
    "Never auto-submitted via BrokerPort. Click Paper BUY/SELL/CLOSE yourself."
)


@dataclass
class Suggestion:
    action: str
    title: str
    detail: str
    window: str = "none"
    severity: str = "info"  # info | caution | warn
    event_title: str | None = None
    event_when: str | None = None
    countdown: str | None = None
    currencies: str | None = None
    suggested_sl: float | None = None
    current_sl: float | None = None
    sl_note: str = ""
    auto_submit: bool = False
    disclaimer: str = DISCLAIMER
    extras: dict[str, Any] = field(default_factory=dict)

    def as_card_lines(self) -> list[str]:
        return [self.title, self.detail]


def _advice_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("advice") or {})


def advice_enabled(cfg: dict[str, Any] | None) -> bool:
    block = _advice_cfg(cfg)
    if "enabled" in block:
        return bool(block.get("enabled"))
    return True


def _window_minutes(cfg: dict[str, Any] | None) -> tuple[int, int, int]:
    block = _advice_cfg(cfg)
    cal = dict((cfg or {}).get("calendar") or {})
    before = int(block.get("before_minutes") or cal.get("before_minutes") or 60)
    during = int(block.get("during_minutes") or cal.get("during_minutes") or 15)
    after = int(block.get("after_minutes") or cal.get("after_minutes") or 30)
    return before, during, after


def _nearest_active(
    events: list[CalendarEvent],
    now: datetime,
    cfg: dict[str, Any] | None,
) -> tuple[CalendarEvent | None, str]:
    before, during, after = _window_minutes(cfg)
    ranked: list[tuple[int, int, float, CalendarEvent, str]] = []
    rank = {WINDOW_DURING: 0, WINDOW_BEFORE: 1, WINDOW_AFTER: 2}
    for e in events:
        win = event_window(
            e, now, before_minutes=before, during_minutes=during, after_minutes=after
        )
        if win == "none":
            continue
        ts = e.when_dt()
        dist = abs((ts - now).total_seconds()) if ts is not None else 1e18
        bump = 0 if e.highlight else 1
        ranked.append((rank[win], bump, dist, e, win))
    if not ranked:
        return None, "none"
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    event = ranked[0][3]
    win = ranked[0][4]
    return event, win


def _open_side(position: dict[str, Any] | None) -> str:
    if not position:
        return ""
    return str(position.get("side") or "").upper()


def _current_sl(position: dict[str, Any] | None) -> float | None:
    if not position:
        return None
    sl = position.get("sl")
    try:
        v = float(sl)
    except (TypeError, ValueError):
        return None
    if not pd.notna(v):
        return None
    return v


def tighten_sl_from_atr(
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any] | None,
    side: str,
    *,
    current_sl: float | None,
    last_price: float | None,
    validity: str = "OK",
) -> tuple[float | None, str]:
    """Propose a tighter SL from the ATR risk box. Never widens. Not an order."""
    side_u = str(side or "").upper()
    if side_u not in {"BUY", "SELL"}:
        return None, "no directional side for an SL"
    k = float(_advice_cfg(cfg).get("tighten_sl_atr") or 1.0)
    if k <= 0:
        return None, "advice.tighten_sl_atr must be positive"
    from forex_lab.ui.board import research_risk  # local: avoid import cycle at module load

    probe = deepcopy(cfg or {})
    barrier = dict(probe.get("barrier") or {})
    barrier["sl_atr"] = k
    probe["barrier"] = barrier
    box = research_risk(ohlcv, probe, side_u, validity=validity)
    if not box.available or box.sl is None:
        return None, box.reason or "ATR SL n/a"
    proposed = float(box.sl)
    px = last_price
    if px is None:
        try:
            px = float(ohlcv["Close"].iloc[-1]) if ohlcv is not None and not ohlcv.empty else None
        except Exception:
            px = None
    if px is not None:
        if side_u == "BUY" and proposed >= float(px):
            return None, "proposed SL would sit at/above last close (would fill)"
        if side_u == "SELL" and proposed <= float(px):
            return None, "proposed SL would sit at/below last close (would fill)"
    if current_sl is not None:
        if side_u == "BUY" and proposed <= float(current_sl) + 1e-12:
            return None, f"current SL {_fmt(current_sl)} is already as tight or tighter"
        if side_u == "SELL" and proposed >= float(current_sl) - 1e-12:
            return None, f"current SL {_fmt(current_sl)} is already as tight or tighter"
    note = (
        f"ATR×{k:g} SL {_fmt(proposed)} vs current {_fmt(current_sl) if current_sl is not None else 'n/a'} "
        f"(same barrier path as the risk box; research only)"
    )
    return proposed, note


def _fmt(x: object, digits: int = 5) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _event_bits(event: CalendarEvent, now: datetime) -> dict[str, str]:
    when = event.when_dt()
    return {
        "event_title": event.title,
        "event_when": event.when,
        "countdown": countdown_label(when, now),
        "currencies": event.currency,
    }


def _card(
    action: str,
    title: str,
    detail: str,
    *,
    window: str,
    severity: str,
    event: CalendarEvent | None,
    now: datetime,
    suggested_sl: float | None = None,
    current_sl: float | None = None,
    sl_note: str = "",
) -> Suggestion:
    bits = _event_bits(event, now) if event is not None else {}
    return Suggestion(
        action=action,
        title=title,
        detail=detail,
        window=window,
        severity=severity,
        event_title=bits.get("event_title"),
        event_when=bits.get("event_when"),
        countdown=bits.get("countdown"),
        currencies=bits.get("currencies"),
        suggested_sl=suggested_sl,
        current_sl=current_sl,
        sl_note=sl_note,
        auto_submit=False,
    )


def _open_action(cfg: dict[str, Any] | None, window: str, *, flatten: bool) -> str:
    block = _advice_cfg(cfg)
    key = {
        WINDOW_BEFORE: "before_action_open",
        WINDOW_DURING: "during_action_open",
        WINDOW_AFTER: "after_action_open",
    }.get(window, "")
    default = {
        WINDOW_BEFORE: ACTION_TIGHTEN,
        WINDOW_DURING: ACTION_HOLD,
        WINDOW_AFTER: ACTION_HOLD,
    }.get(window, ACTION_HOLD)
    raw = str(block.get(key) or default).strip().lower()
    if flatten and window in {WINDOW_BEFORE, WINDOW_DURING}:
        raw = str(block.get("flatten_action") or ACTION_CLOSE).strip().lower()
    aliases = {
        "tighten": ACTION_TIGHTEN,
        "tighten_sl": ACTION_TIGHTEN,
        "close": ACTION_CLOSE,
        "hold": ACTION_HOLD,
        "flat": ACTION_CLOSE,
        "flatten": ACTION_CLOSE,
    }
    return aliases.get(raw, default)


def suggest_actions(
    *,
    pair: str,
    signal: str | None,
    validity: str,
    position: dict[str, Any] | None,
    events: list[CalendarEvent] | None,
    cfg: dict[str, Any] | None,
    ohlcv: pd.DataFrame | None = None,
    risk: Any | None = None,  # unused; callers may pass the ATR risk box for context
    mtf: MtfStatus | None = None,
    last_price: float | None = None,
    now: datetime | None = None,
) -> list[Suggestion]:
    """Return 0–2 advisory cards. Empty when advice is off or not applicable."""
    if not advice_enabled(cfg):
        return []
    if validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        return [
            _card(
                ACTION_NONE,
                "Advice n/a",
                "Data is stale or missing — refresh before using event/MTF suggestions.",
                window="none",
                severity="info",
                event=None,
                now=now or datetime.now(timezone.utc),
            )
        ]
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    pair_events = events_affecting(list(events or []), pair)
    event, window = _nearest_active(pair_events, clock, cfg)
    sig = str(signal or "").upper()
    side = _open_side(position)
    cards: list[Suggestion] = []

    if event is not None and window != "none":
        bits = _event_bits(event, clock)
        tag = f"{event.currency} {event.title} ({bits['countdown']}, {event.impact})"
        flatten = bool(event.highlight)
        if not side:
            cards.append(
                _card(
                    ACTION_NO_NEW,
                    "Suggest: no new opens",
                    f"High-impact window ({window}) for {tag}. "
                    "Stand aside until the release window passes — do not click Paper BUY/SELL "
                    "unless you accept the event risk.",
                    window=window,
                    severity="warn" if window == WINDOW_DURING or flatten else "caution",
                    event=event,
                    now=clock,
                )
            )
        else:
            action = _open_action(cfg, window, flatten=flatten)
            cur_sl = _current_sl(position)
            if action == ACTION_CLOSE:
                cards.append(
                    _card(
                        ACTION_CLOSE,
                        "Suggest: close (paper)",
                        f"Open {side} {pair} into {window} window for {tag}. "
                        "Advisory only — click Paper CLOSE if you want out. Not auto-submitted.",
                        window=window,
                        severity="warn",
                        event=event,
                        now=clock,
                    )
                )
            elif action == ACTION_TIGHTEN:
                proposed, sl_note = tighten_sl_from_atr(
                    ohlcv,
                    cfg,
                    side,
                    current_sl=cur_sl,
                    last_price=last_price,
                    validity=validity,
                )
                if proposed is not None:
                    cards.append(
                        _card(
                            ACTION_TIGHTEN,
                            "Suggest: tighten SL",
                            f"Open {side} {pair} ahead of {tag}. "
                            f"{sl_note}. Click **Apply paper SL** to write it locally — never auto-submitted.",
                            window=window,
                            severity="caution",
                            event=event,
                            now=clock,
                            suggested_sl=proposed,
                            current_sl=cur_sl,
                            sl_note=sl_note,
                        )
                    )
                else:
                    cards.append(
                        _card(
                            ACTION_HOLD,
                            "Suggest: hold",
                            f"Open {side} {pair} in {window} window for {tag}. "
                            f"Could not tighten SL ({sl_note}). Hold unless you click CLOSE.",
                            window=window,
                            severity="caution",
                            event=event,
                            now=clock,
                            current_sl=cur_sl,
                        )
                    )
            else:
                cards.append(
                    _card(
                        ACTION_HOLD,
                        "Suggest: hold",
                        f"Open {side} {pair} in {window} window for {tag}. "
                        "Let the paper SL/TP work; do not add. Not an order.",
                        window=window,
                        severity="caution" if window != WINDOW_AFTER else "info",
                        event=event,
                        now=clock,
                        current_sl=cur_sl,
                    )
                )

    if mtf is not None and mtf.status == MTF_CONFLICT:
        if not side and sig in {"BUY", "SELL"}:
            cards.append(
                _card(
                    ACTION_NO_NEW,
                    "Suggest: hold off (MTF conflict)",
                    f"{mtf.note}. Config can also flash HOLD/weaker "
                    f"(board.mtf_confirm.conflict_flash). Advisory only.",
                    window="mtf",
                    severity="caution",
                    event=event,
                    now=clock,
                )
            )
        elif side and (
            (side == "BUY" and mtf.direction == "down") or (side == "SELL" and mtf.direction == "up")
        ):
            cards.append(
                _card(
                    ACTION_HOLD,
                    "Suggest: hold (MTF against position)",
                    f"Paper {side} vs {mtf.note}. Not a close order — review, then click CLOSE if you agree.",
                    window="mtf",
                    severity="caution",
                    event=event,
                    now=clock,
                )
            )

    # Deduplicate by action, keep first (event window outranks MTF hold-off if same action).
    seen: set[str] = set()
    uniq: list[Suggestion] = []
    for c in cards:
        if c.action in seen:
            continue
        seen.add(c.action)
        uniq.append(c)
    return uniq[:2]
