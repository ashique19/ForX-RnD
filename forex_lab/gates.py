"""Selective trading gates for flashed signals and paper-allowed opens.

Three optional checks, all config-driven:

1. ``require_mtf_agree`` — BUY/SELL must match the causal HTF SMA-slope badge.
2. ``min_confidence`` — extra floor on P(predicted class) (null = reuse
   ``signals.min_confidence`` for the UI re-check).
3. ``no_new_opens_in_event_window`` — reuse calendar/advice before/during/after
   windows; block new opens (same spirit as the "Suggest: no new opens" card).

Fail-soft: missing calendar, missing MTF slope, missing confidence, or any
exception **never crashes** and does **not** invent a block. Walk-forward has
no historical Forex Factory dump, so the event gate is a no-op there.

Default-off until a walk-forward screen shows a non-regression on profit
factor / total return / max drawdown. This is **not** a live edge and never
calls ``BrokerPort.submit``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from forex_lab.advise import WINDOW_AFTER, WINDOW_BEFORE, WINDOW_DURING, _window_minutes
from forex_lab.calendar import CalendarEvent, countdown_label, event_window, events_affecting
from forex_lab.features import INV_LABEL_MAP, LABEL_MAP
from forex_lab.mtf import MTF_AGREE, MTF_CONFLICT, MTF_NA, MtfStatus, confirm_timeframes

GATE_MTF = "mtf_agree"
GATE_CONF = "min_confidence"
GATE_EVENT = "event_window"

DEFAULT_EVENT_WINDOWS = (WINDOW_BEFORE, WINDOW_DURING, WINDOW_AFTER)


@dataclass
class GateHit:
    name: str
    blocked: bool
    reason: str
    fail_soft: bool = False


@dataclass
class GateDecision:
    allowed: bool = True
    flash: str = ""
    raw: str = ""
    hits: list[GateHit] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    fail_soft_notes: list[str] = field(default_factory=list)

    def block_reason(self) -> str:
        if self.allowed:
            return ""
        parts = [h.reason for h in self.hits if h.blocked and h.reason]
        return " · ".join(parts) if parts else "gated"

    def caption(self) -> str:
        reason = self.block_reason()
        if not reason:
            return ""
        return f"Paper BUY/SELL disabled — {reason}"


def _gates_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("gates") or {})


def gates_enabled(cfg: dict[str, Any] | None) -> bool:
    return bool(_gates_cfg(cfg).get("enabled"))


def gates_apply_to_flash(cfg: dict[str, Any] | None) -> bool:
    block = _gates_cfg(cfg)
    if "apply_to_flash" in block:
        return bool(block.get("apply_to_flash"))
    return True


def gates_apply_to_paper(cfg: dict[str, Any] | None) -> bool:
    block = _gates_cfg(cfg)
    if "apply_to_paper" in block:
        return bool(block.get("apply_to_paper"))
    return True


def gates_fail_soft(cfg: dict[str, Any] | None) -> bool:
    block = _gates_cfg(cfg)
    if "fail_soft" in block:
        return bool(block.get("fail_soft"))
    return True


def _truthy(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off", "none", "no"}
    return bool(value)


def require_mtf_agree(cfg: dict[str, Any] | None) -> bool:
    return _truthy(_gates_cfg(cfg).get("require_mtf_agree"), default=True)


def no_new_opens_in_event_window(cfg: dict[str, Any] | None) -> bool:
    return _truthy(_gates_cfg(cfg).get("no_new_opens_in_event_window"), default=True)


def gate_min_confidence(cfg: dict[str, Any] | None) -> float | None:
    """Extra confidence floor, or signals.min_confidence when null."""
    block = _gates_cfg(cfg)
    if "min_confidence" not in block or block.get("min_confidence") is None:
        sig = dict((cfg or {}).get("signals") or {})
        raw = sig.get("min_confidence")
        if raw is None:
            return 0.40
        try:
            return float(raw)
        except (TypeError, ValueError):
            return 0.40
    try:
        return float(block.get("min_confidence"))
    except (TypeError, ValueError):
        return None


def event_gate_windows(cfg: dict[str, Any] | None) -> set[str]:
    raw = _gates_cfg(cfg).get("event_windows")
    if not raw:
        return set(DEFAULT_EVENT_WINDOWS)
    out = {str(x).strip().lower() for x in raw if str(x).strip()}
    return out or set(DEFAULT_EVENT_WINDOWS)


def _as_signal(value: object) -> str:
    return str(value or "").strip().upper()


def _confidence_value(confidence: object) -> float | None:
    if confidence is None:
        return None
    try:
        v = float(confidence)
    except (TypeError, ValueError):
        return None
    if not pd.notna(v):
        return None
    return v


def _mtf_status_of(mtf: MtfStatus | None) -> str:
    if mtf is None:
        return MTF_NA
    return str(getattr(mtf, "status", None) or MTF_NA).lower()


def _nearest_event_window(
    events: list[CalendarEvent] | None,
    pair: str,
    now: datetime | None,
    cfg: dict[str, Any] | None,
) -> tuple[CalendarEvent | None, str]:
    """Reuse advice before/during/after minutes. Empty/missing calendar → none."""
    if events is None:
        return None, "none"
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    before, during, after = _window_minutes(cfg)
    ranked: list[tuple[int, float, CalendarEvent, str]] = []
    rank = {WINDOW_DURING: 0, WINDOW_BEFORE: 1, WINDOW_AFTER: 2}
    try:
        pair_events = events_affecting(list(events), pair)
    except Exception:
        return None, "none"
    for e in pair_events:
        try:
            win = event_window(
                e, clock, before_minutes=before, during_minutes=during, after_minutes=after
            )
        except Exception:
            continue
        if win == "none":
            continue
        ts = e.when_dt() if hasattr(e, "when_dt") else None
        dist = abs((ts - clock).total_seconds()) if ts is not None else 1e18
        ranked.append((rank.get(win, 9), dist, e, win))
    if not ranked:
        return None, "none"
    ranked.sort(key=lambda row: (row[0], row[1]))
    return ranked[0][2], ranked[0][3]


def evaluate_open_gates(
    *,
    pair: str = "",
    signal: str | None = None,
    raw_signal: str | None = None,
    confidence: object = None,
    mtf: MtfStatus | None = None,
    events: list[CalendarEvent] | None = None,
    cfg: dict[str, Any] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> GateDecision:
    """Decide whether a directional flash / paper open is allowed.

    ``events is None`` means calendar was not provided (fail-soft pass).
    ``events == []`` means the feed returned empty / failed (fail-soft pass).
    """
    sig = _as_signal(signal)
    raw = _as_signal(raw_signal) or sig
    decision = GateDecision(allowed=True, flash=sig or "HOLD", raw=raw)
    if not force and not gates_enabled(cfg):
        return decision

    directional = sig in {"BUY", "SELL"}
    soft = gates_fail_soft(cfg)

    try:
        if directional:
            min_conf = gate_min_confidence(cfg)
            if min_conf is not None and min_conf > 0:
                conf = _confidence_value(confidence)
                if conf is None:
                    note = "confidence n/a — fail-soft pass"
                    decision.hits.append(GateHit(GATE_CONF, False, note, fail_soft=True))
                    decision.fail_soft_notes.append(note)
                    if not soft:
                        decision.hits[-1] = GateHit(
                            GATE_CONF, True, "confidence missing (strict)", fail_soft=False
                        )
                        decision.blocked_by.append(GATE_CONF)
                elif conf < min_conf:
                    decision.hits.append(
                        GateHit(
                            GATE_CONF,
                            True,
                            f"conf={conf:.2f} < min {min_conf:.2f}",
                            fail_soft=False,
                        )
                    )
                    decision.blocked_by.append(GATE_CONF)
                else:
                    decision.hits.append(
                        GateHit(GATE_CONF, False, f"conf={conf:.2f} ≥ {min_conf:.2f}")
                    )

            if require_mtf_agree(cfg):
                status = _mtf_status_of(mtf)
                if mtf is None or status in {"", MTF_NA, "na", "n/a"}:
                    note = "MTF n/a — fail-soft pass"
                    decision.hits.append(GateHit(GATE_MTF, False, note, fail_soft=True))
                    decision.fail_soft_notes.append(note)
                    if not soft:
                        decision.hits[-1] = GateHit(
                            GATE_MTF, True, "MTF missing (strict)", fail_soft=False
                        )
                        decision.blocked_by.append(GATE_MTF)
                elif status == MTF_CONFLICT:
                    extra = ""
                    if mtf is not None and getattr(mtf, "note", None):
                        extra = f" ({mtf.note})"
                    decision.hits.append(
                        GateHit(GATE_MTF, True, f"MTF conflict{extra}", fail_soft=False)
                    )
                    decision.blocked_by.append(GATE_MTF)
                elif status == MTF_AGREE:
                    tf = getattr(mtf, "timeframe", "") or ""
                    decision.hits.append(
                        GateHit(GATE_MTF, False, f"MTF agree ({tf})" if tf else "MTF agree")
                    )
                else:
                    note = f"MTF {status} — fail-soft pass"
                    decision.hits.append(GateHit(GATE_MTF, False, note, fail_soft=True))
                    decision.fail_soft_notes.append(note)

        if no_new_opens_in_event_window(cfg):
            if events is None:
                note = "calendar not provided — fail-soft pass"
                decision.hits.append(GateHit(GATE_EVENT, False, note, fail_soft=True))
                decision.fail_soft_notes.append(note)
                if not soft:
                    decision.hits[-1] = GateHit(
                        GATE_EVENT, True, "calendar missing (strict)", fail_soft=False
                    )
                    decision.blocked_by.append(GATE_EVENT)
            else:
                event, window = _nearest_event_window(events, pair, now, cfg)
                allowed_windows = event_gate_windows(cfg)
                if event is None or window == "none" or window not in allowed_windows:
                    if not events:
                        note = "calendar empty — fail-soft pass"
                        decision.hits.append(GateHit(GATE_EVENT, False, note, fail_soft=True))
                        decision.fail_soft_notes.append(note)
                    else:
                        decision.hits.append(
                            GateHit(GATE_EVENT, False, "no high-impact window")
                        )
                else:
                    bits = []
                    title = getattr(event, "title", "") or "event"
                    ccy = getattr(event, "currency", "") or ""
                    when = event.when_dt() if hasattr(event, "when_dt") else None
                    cd = countdown_label(when, now)
                    if ccy:
                        bits.append(str(ccy))
                    bits.append(str(title))
                    if cd:
                        bits.append(f"({cd})")
                    label = " ".join(bits)
                    decision.hits.append(
                        GateHit(
                            GATE_EVENT,
                            True,
                            f"no new opens — {window} window for {label}",
                            fail_soft=False,
                        )
                    )
                    decision.blocked_by.append(GATE_EVENT)
    except Exception as exc:  # noqa: BLE001 — gates must never crash the desk
        note = f"gate error ({type(exc).__name__}) — fail-soft pass"
        decision.hits.append(GateHit("error", False, note, fail_soft=True))
        decision.fail_soft_notes.append(note)
        if not soft:
            decision.blocked_by.append("error")

    decision.allowed = not decision.blocked_by
    if not decision.allowed and directional:
        decision.flash = "HOLD"
    return decision


def evaluate_open_gates_for_row(
    row: Any,
    cfg: dict[str, Any] | None,
    events: list[CalendarEvent] | None = None,
    now: datetime | None = None,
    *,
    force: bool = False,
) -> GateDecision:
    sig = str(getattr(row, "buy_sell", None) or "")
    raw = str(getattr(row, "raw_signal", None) or sig)
    # If the flash was already rewritten to HOLD, still gate on the last model class
    # for MTF/confidence so paper cannot sneak a blocked directional open.
    directional_src = raw if sig in {"HOLD", "—", "-", "N/A", ""} else sig
    return evaluate_open_gates(
        pair=str(getattr(row, "pair", "") or ""),
        signal=directional_src if directional_src in {"BUY", "SELL"} else sig,
        raw_signal=raw,
        confidence=getattr(row, "confidence", None),
        mtf=getattr(row, "mtf", None),
        events=events,
        cfg=cfg,
        now=now,
        force=force,
    )


def apply_open_gates(
    row: Any,
    cfg: dict[str, Any] | None,
    *,
    events: list[CalendarEvent] | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> Any:
    """Annotate a board row. Optionally rewrite BUY/SELL → HOLD.

    Fail-soft: never raises. Missing calendar/MTF does not block.
    """
    try:
        decision = evaluate_open_gates_for_row(row, cfg, events, now, force=force)
    except Exception as exc:  # noqa: BLE001
        try:
            row.gate_blocked = False
            row.gate_reason = f"gate error ({type(exc).__name__}) — fail-soft pass"
            row.gate_fail_soft = [row.gate_reason]
        except Exception:
            pass
        return row

    paper_block = (force or gates_enabled(cfg)) and gates_apply_to_paper(cfg) and not decision.allowed
    try:
        row.gate_blocked = bool(paper_block)
        row.gate_reason = decision.block_reason() if paper_block else ""
        row.gate_fail_soft = list(decision.fail_soft_notes)
        extra = dict(getattr(row, "extra", None) or {})
        extra["gate_blocked_by"] = list(decision.blocked_by)
        extra["gate_fail_soft"] = list(decision.fail_soft_notes)
        row.extra = extra
    except Exception:
        pass

    live = str(getattr(row, "buy_sell", "") or "").upper()
    if (
        (force or gates_enabled(cfg))
        and gates_apply_to_flash(cfg)
        and not decision.allowed
        and live in {"BUY", "SELL"}
    ):
        if not getattr(row, "raw_signal", None):
            row.raw_signal = live
        row.buy_sell = "HOLD"
        reason = decision.block_reason()
        details = str(getattr(row, "signal_details", "") or "")
        note = f"gated HOLD ({reason})"
        row.signal_details = f"{details}  |  {note}" if details else note
    return row


def _slope_column(cfg: dict[str, Any] | None) -> str | None:
    tfs = confirm_timeframes(cfg)
    if not tfs:
        return None
    try:
        from forex_lab.features import _tf_rule

        parsed = _tf_rule(tfs[0])
    except Exception:
        parsed = None
    tag = parsed[0] if parsed else str(tfs[0])
    return f"tf_{tag}_sma_slope"


def apply_frame_gates(
    pred: pd.Series,
    frame: pd.DataFrame,
    cfg: dict[str, Any] | None,
    *,
    force: bool = False,
) -> pd.Series:
    """Force HOLD on disagreement / low confidence. Event windows skipped (no history).

    Used by walk-forward so a gated policy can be scored on the same folds.
    Missing slope or confidence fail-soft (keep the prediction).
    """
    out = pred.astype(int).copy()
    if not force and not gates_enabled(cfg):
        return out
    hold = LABEL_MAP["HOLD"]
    buy = LABEL_MAP["BUY"]
    sell = LABEL_MAP["SELL"]
    directional = out.isin([buy, sell])
    keep = pd.Series(True, index=out.index)
    soft = gates_fail_soft(cfg)

    min_conf = gate_min_confidence(cfg)
    if min_conf is not None and min_conf > 0 and "confidence" in frame.columns:
        conf = pd.to_numeric(frame["confidence"], errors="coerce")
        conf_ok = conf >= float(min_conf)
        if soft:
            conf_ok = conf_ok | conf.isna()
        keep = keep & (~directional | conf_ok.reindex(out.index).fillna(soft))

    if require_mtf_agree(cfg):
        col = _slope_column(cfg)
        extras = dict((cfg or {}).get("feature_extras") or {})
        flat_eps = float(
            dict((cfg or {}).get("board") or {}).get("mtf_confirm", {}).get("flat_eps")
            or extras.get("htf_flat_eps")
            or 1e-8
        )
        if col and col in frame.columns:
            slope = pd.to_numeric(frame[col], errors="coerce")
            agree = ((out == buy) & (slope > flat_eps)) | ((out == sell) & (slope < -flat_eps))
            flat_or_na = slope.isna() | (slope.abs() <= flat_eps)
            mtf_ok = agree | (flat_or_na if soft else pd.Series(False, index=out.index))
            keep = keep & (~directional | mtf_ok.reindex(out.index).fillna(soft))
        elif not soft:
            keep = keep & ~directional

    return out.where(keep, hold).astype(int)


def pred_name(value: object) -> str:
    try:
        return INV_LABEL_MAP.get(int(value), "HOLD")
    except (TypeError, ValueError):
        return str(value or "HOLD")
