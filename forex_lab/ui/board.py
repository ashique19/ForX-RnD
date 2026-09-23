"""Build research watch-board rows for the Streamlit UI.

No broker APIs and no invented prices. Missing data/model is reported as status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from forex_lab.calendar import CalendarEvent, event_window, next_event_for_pair, next_event_label
from forex_lab.clock import fmt_display, relabel_in_text
from forex_lab.config_loader import load_config
from forex_lab.data import csv_mtime_utc, load_cached_ohlcv, try_yfinance_refresh
from forex_lab.explain import SignalExplanation, explain_latest_signal
from forex_lab.features import true_range_atr
from forex_lab.gates import apply_open_gates, evaluate_open_gates_for_row, gates_apply_to_paper, gates_enabled
from forex_lab.freshness import (
    VALIDITY_CLOSED,
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
    Freshness,
    FAILURE_TF_LABEL,
    assess_ohlcv,
    format_failure_reason,
    now_utc,
)
from forex_lab.mtf import (
    FLASH_HOLD,
    FLASH_WEAKEN,
    MTF_CONFLICT,
    MtfStatus,
    assess_mtf,
    conflict_flash_mode,
)
from forex_lab.session import SessionState, classify_session
from forex_lab.signals import generate_signals
from forex_lab.ui.pipeline import artifact_status, load_signals
from forex_lab.ui.quote import QuoteView, quote_from_ohlcv
from forex_lab.ui.watchlist import Watchlist

NEED_FETCH_TRAIN = "need Fetch/Train"
STATUS_READY = "ready"
STATUS_NEED_FETCH = "need_fetch"
STATUS_NEED_TRAIN = "need_train"
STATUS_ERROR = "error"
STATUS_STALE = "stale"
SPARKLINE_BARS = 48
SPARK_CHARS = "▁▂▃▄▅▆▇█"
PAPER_BLOCKED_VALIDITIES = frozenset({VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR})
PAPER_STALE_CAPTION = "Paper BUY/SELL is disabled when Data● is STALE or MISSING — refresh (Fetch) first."


def validity_token(validity: object) -> str:
    """OK / STALE / … even when a caller passes ``STALE · reason``."""
    raw = str(validity or "").strip().upper()
    return raw.split()[0] if raw else ""


PAPER_GATE_CAPTION = (
    "Paper BUY/SELL can also be disabled by selective gates "
    "(MTF agree / min confidence / event window) when gates.enabled is true. "
    "Default off — not a live edge."
)


@dataclass
class RiskBox:
    """ATR barrier suggestion for the UI. Not an order ticket."""

    available: bool
    reason: str = ""
    side: str = ""
    entry: float | None = None
    entry_ref: str = ""
    sl: float | None = None
    tp: float | None = None
    rr: float | None = None
    spread_pips: float | None = None
    atr: float | None = None
    tp_atr: float | None = None
    sl_atr: float | None = None
    horizon: int | None = None


@dataclass
class BoardRow:
    pair: str
    timeframe: str
    buy_sell: str
    target: str
    signal_details: str
    status: str
    confidence: float | None = None
    dir_edge: float | None = None
    p_buy: float | None = None
    p_sell: float | None = None
    p_hold: float | None = None
    model: str | None = None
    datetime: str | None = None
    close: float | None = None
    raw_signal: str | None = None
    target_note: str | None = None
    data_source: str | None = None
    n_bars: int | None = None
    error: str | None = None
    rationale: str | None = None
    explain_method: str | None = None
    drivers: list = field(default_factory=list)
    rules: list = field(default_factory=list)
    validity: str = VALIDITY_MISSING
    validity_reason: str = ""
    last_bar_at: str | None = None
    last_fetch_at: str | None = None
    last_signal_at: str | None = None
    sparkline: list[float] = field(default_factory=list)
    sparkline_note: str = ""
    risk: RiskBox | None = None
    mtf: MtfStatus | None = None
    flash_weak: bool = False
    quote: QuoteView | None = None
    session: SessionState | None = None
    next_event: str = "—"
    next_event_warn: bool = False
    gate_blocked: bool = False
    gate_reason: str = ""
    gate_fail_soft: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_table_dict(self) -> dict[str, str]:
        mtf_label = self.mtf.status if self.mtf is not None else "n/a"
        q = self.quote
        sess = self.session.badge() if self.session is not None else "n/a"
        last = q.last_label() if q is not None else "n/a"
        spread = q.spread_label() if q is not None else "n/a"
        return {
            "Pair": self.pair,
            "TF": self.timeframe,
            "Signal": self.buy_sell,
            "Conf": conf_label(self.confidence),
            "Data●": self.validity,
            "MTF": mtf_label,
            "Session": sess,
            "Last/mid": last,
            "Spread": spread,
            "Next event": self.next_event or "—",
            "Spark": spark_ascii(self.sparkline),
            "Actions": paper_actions_label(self),
        }


def paper_submit_allowed(
    validity: str | None,
    row: BoardRow | None = None,
    cfg: dict[str, Any] | None = None,
    events: list[CalendarEvent] | None = None,
    now: Any = None,
) -> bool:
    """OK/CLOSED can paper-submit. STALE/MISSING/ERROR cannot — block bad clicks.

    When ``row`` + ``cfg`` are passed and ``gates.enabled``, also apply selective
    open gates (MTF / confidence / event window). Missing calendar or MTF
    fail-softs (does not block). Validity-only calls keep the old signature.
    """
    if validity_token(validity) in PAPER_BLOCKED_VALIDITIES:
        return False
    if row is None:
        return True
    return paper_open_allowed(row, cfg=cfg, events=events, now=now)


def paper_open_allowed(
    row: BoardRow,
    *,
    cfg: dict[str, Any] | None = None,
    events: list[CalendarEvent] | None = None,
    now: Any = None,
) -> bool:
    """Validity + optional selective gates. CLOSE is not gated here."""
    if validity_token(row.validity) in PAPER_BLOCKED_VALIDITIES:
        return False
    if getattr(row, "gate_blocked", False):
        return False
    if not gates_enabled(cfg) or not gates_apply_to_paper(cfg):
        return True
    try:
        decision = evaluate_open_gates_for_row(row, cfg, events, now)
        return bool(decision.allowed)
    except Exception:
        return True


def paper_submit_block_reason(
    validity: str | None,
    row: BoardRow | None = None,
    cfg: dict[str, Any] | None = None,
    events: list[CalendarEvent] | None = None,
    now: Any = None,
) -> str:
    v = validity_token(validity)
    if v == VALIDITY_STALE:
        return "Paper BUY/SELL disabled — data STALE, refresh required"
    if v == VALIDITY_MISSING:
        return "Paper BUY/SELL disabled — data MISSING, Fetch required"
    if v == VALIDITY_ERROR:
        return "Paper BUY/SELL disabled — data ERROR"
    if row is not None and getattr(row, "gate_reason", ""):
        return f"Paper BUY/SELL disabled — {row.gate_reason}"
    if row is not None and gates_enabled(cfg) and gates_apply_to_paper(cfg):
        try:
            decision = evaluate_open_gates_for_row(row, cfg, events, now)
            if not decision.allowed:
                return decision.caption()
        except Exception:
            return ""
    return ""


def paper_actions_label(row: BoardRow, *, has_open: bool = False) -> str:
    if has_open:
        return "CLOSE"
    if not paper_submit_allowed(row.validity):
        return f"disabled ({row.validity})"
    if getattr(row, "gate_blocked", False):
        by = (row.extra or {}).get("gate_blocked_by") or []
        tag = by[0] if by else "gate"
        return f"disabled ({tag})"
    return "BUY/SELL"


def conf_label(conf: object) -> str:
    if conf is None or (isinstance(conf, float) and pd.isna(conf)):
        return "—"
    try:
        v = float(conf)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(v):
        return "—"
    if 0.0 <= v <= 1.0:
        return f"{100.0 * v:.0f}%"
    return f"{v:.0f}%"


def spark_ascii(values: list[float] | None, n: int = 16) -> str:
    """Tiny unicode spark for the dense scan row. Empty when STALE/MISSING."""
    pts = [float(x) for x in (values or []) if x is not None and pd.notna(x)]
    if len(pts) < 2:
        return "—"
    width = max(2, int(n))
    if len(pts) > width:
        last = width - 1
        pts = [pts[int(round(i * (len(pts) - 1) / last))] for i in range(width)]
    lo, hi = min(pts), max(pts)
    span = hi - lo
    top = len(SPARK_CHARS) - 1
    if span <= 0:
        return SPARK_CHARS[0] * len(pts)
    out = []
    for x in pts:
        idx = int(round((x - lo) / span * top))
        out.append(SPARK_CHARS[max(0, min(top, idx))])
    return "".join(out)


def paper_submit_risk_defaults(
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any],
    side: str,
    validity: str,
) -> tuple[float | None, float | None]:
    """ATR SL/TP for a paper submit when the row is allowed and barriers exist."""
    if not paper_submit_allowed(validity):
        return None, None
    box = research_risk(ohlcv, cfg, side, validity=validity)
    if not box.available:
        return None, None
    return box.sl, box.tp


def _event_window_minutes(cfg: dict[str, Any] | None) -> tuple[int, int, int]:
    block = dict((cfg or {}).get("advice") or {})
    cal = dict((cfg or {}).get("calendar") or {})
    before = int(block.get("before_minutes") or cal.get("before_minutes") or 60)
    during = int(block.get("during_minutes") or cal.get("during_minutes") or 15)
    after = int(block.get("after_minutes") or cal.get("after_minutes") or 30)
    return before, during, after


def attach_next_event(
    row: BoardRow,
    events: list[CalendarEvent] | None,
    now: Any = None,
    cfg: dict[str, Any] | None = None,
) -> BoardRow:
    """Compact Next-event cell + pre-event warning flag. Not a trade instruction."""
    ev = next_event_for_pair(events, row.pair, now)
    if ev is None:
        row.next_event = "—"
        row.next_event_warn = False
        return apply_open_gates(row, cfg, events=events, now=now)
    before, during, after = _event_window_minutes(cfg)
    win = event_window(
        ev, now, before_minutes=before, during_minutes=during, after_minutes=after
    )
    row.next_event_warn = win in {"before", "during"}
    row.next_event = next_event_label(ev, now, warn=row.next_event_warn)
    return apply_open_gates(row, cfg, events=events, now=now)


def _fmt(x: object, digits: int = 4) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return str(x)


def _fmt_when(value: object, cfg: dict[str, Any] | None = None) -> str:
    return fmt_display(value, cfg)


def _barrier_levels(ohlcv: pd.DataFrame | None, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Last-close proxy + ATR TP/SL used by labels/backtest. None if not computable."""
    if ohlcv is None or ohlcv.empty or "Close" not in ohlcv.columns:
        return None
    scheme = str(cfg.get("label_scheme") or "triple_barrier").lower()
    if scheme != "triple_barrier":
        return None
    b = cfg.get("barrier") or {}
    tp_atr = float(b.get("tp_atr", 2.0))
    sl_atr = float(b.get("sl_atr", 2.0))
    atr_p = int(cfg.get("atr_period", 14))
    atr_s = true_range_atr(ohlcv, atr_p)
    atr = float(atr_s.iloc[-1]) if len(atr_s) else float("nan")
    close = float(ohlcv["Close"].iloc[-1])
    if not pd.notna(atr) or atr <= 0 or not pd.notna(close) or close <= 0:
        return None
    entry = close
    return {
        "entry": entry,
        "atr": atr,
        "tp_atr": tp_atr,
        "sl_atr": sl_atr,
        "long_tp": entry + tp_atr * atr,
        "long_sl": entry - sl_atr * atr,
        "short_tp": entry - tp_atr * atr,
        "short_sl": entry + sl_atr * atr,
        "rr": (tp_atr / sl_atr) if sl_atr else None,
        "entry_timing": str(cfg.get("entry_timing") or "next_open"),
        "horizon": int(cfg.get("horizon") or 8),
        "spread_pips": float(cfg.get("spread_pips") or 0.0),
        "scheme": scheme,
    }


def research_risk(
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any],
    signal: str | None,
    *,
    validity: str = VALIDITY_OK,
) -> RiskBox:
    """SL/TP suggestion from the same ATR barriers as labels/backtest.

    Research only — not a broker ticket, no lot size, no submit.
    """
    sig = str(signal or "").upper()
    if validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR}:
        return RiskBox(
            False,
            reason=(
                "data stale — refresh required"
                if validity == VALIDITY_STALE
                else "no usable data for a risk box"
            ),
        )
    if sig in {"", "—", "-", "N/A", "HOLD"}:
        return RiskBox(False, reason="HOLD / no directional signal — no SL/TP suggestion")
    levels = _barrier_levels(ohlcv, cfg)
    if levels is None:
        scheme = str(cfg.get("label_scheme") or "triple_barrier")
        return RiskBox(False, reason=f"barriers n/a (scheme={scheme})")
    timing = levels["entry_timing"]
    if timing == "next_open":
        entry_ref = (
            "last close as proxy — lab fill is next-open (unknown on this bar)"
        )
    elif timing == "same_close":
        entry_ref = "last close (entry_timing=same_close)"
    else:
        entry_ref = f"last close (entry_timing={timing})"
    if sig == "BUY":
        sl, tp = levels["long_sl"], levels["long_tp"]
    elif sig == "SELL":
        sl, tp = levels["short_sl"], levels["short_tp"]
    else:
        return RiskBox(False, reason="HOLD / no directional signal — no SL/TP suggestion")
    return RiskBox(
        True,
        side=sig,
        entry=float(levels["entry"]),
        entry_ref=entry_ref,
        sl=float(sl),
        tp=float(tp),
        rr=None if levels["rr"] is None else float(levels["rr"]),
        spread_pips=float(levels["spread_pips"]),
        atr=float(levels["atr"]),
        tp_atr=float(levels["tp_atr"]),
        sl_atr=float(levels["sl_atr"]),
        horizon=int(levels["horizon"]),
    )


def research_target(ohlcv: pd.DataFrame | None, cfg: dict[str, Any], signal: str | None) -> tuple[str, str]:
    """Best-effort barrier prices from the last bar + labeling config.

    Triple-barrier labels fill at the *next* open. That fill is unknown on the
    latest closed bar, so last close is used as a proxy and labeled as such.
    """
    scheme = str(cfg.get("label_scheme") or "triple_barrier").lower()
    horizon = int(cfg.get("horizon") or 8)
    if ohlcv is None or ohlcv.empty:
        return "n/a", f"no bars; scheme={scheme} horizon={horizon}"
    levels = _barrier_levels(ohlcv, cfg)
    if levels is None:
        return (
            f"n/a (scheme={scheme}, horizon={horizon})",
            "forward_return / other schemes have no ATR barrier prices on the board"
            if scheme != "triple_barrier"
            else "ATR or close unavailable on last bar",
        )
    sig = str(signal or "HOLD").upper()
    if sig == "BUY":
        compact = f"TP {_fmt(levels['long_tp'], 5)} / SL {_fmt(levels['long_sl'], 5)}"
    elif sig == "SELL":
        compact = f"TP {_fmt(levels['short_tp'], 5)} / SL {_fmt(levels['short_sl'], 5)}"
    else:
        compact = f"upper {_fmt(levels['long_tp'], 5)} / lower {_fmt(levels['long_sl'], 5)}"
    timing = levels["entry_timing"]
    note = (
        f"Research barriers only (not an order). scheme=triple_barrier "
        f"tp_atr={levels['tp_atr']} sl_atr={levels['sl_atr']} ATR={_fmt(levels['atr'], 6)} "
        f"horizon={levels['horizon']} bars. "
        f"Label fill is {timing}; last close {_fmt(levels['entry'], 5)} used as proxy "
        "because the next open is not available yet."
    )
    return compact, note


def sparkline_closes(ohlcv: pd.DataFrame | None, n: int = SPARKLINE_BARS) -> list[float]:
    if ohlcv is None or ohlcv.empty or "Close" not in getattr(ohlcv, "columns", []):
        return []
    s = pd.to_numeric(ohlcv["Close"], errors="coerce").dropna()
    if s.empty:
        return []
    return [float(x) for x in s.tail(max(2, int(n))).tolist()]


def attach_quote_session(
    row: BoardRow,
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any],
    *,
    now: Any = None,
) -> BoardRow:
    """Last/mid + config spread + clock session. No invented bid/ask."""
    row.quote = quote_from_ohlcv(ohlcv, row.pair, cfg)
    row.session = classify_session(now, cfg)
    return row


def attach_visuals(
    row: BoardRow,
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any],
    *,
    now: Any = None,
) -> BoardRow:
    """Sparkline + risk box + quote/session from cached bars. Never invents prices."""
    n = int((cfg.get("board") or {}).get("sparkline_bars") or SPARKLINE_BARS)
    if row.validity in {VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR} or ohlcv is None or ohlcv.empty:
        row.sparkline = []
        row.sparkline_note = (
            "no sparkline — data stale or missing (cached path not shown as live)"
            if row.validity == VALIDITY_STALE
            else "no sparkline — no cached OHLCV"
        )
    else:
        row.sparkline = sparkline_closes(ohlcv, n=n)
        row.sparkline_note = (
            f"last {len(row.sparkline)} {row.timeframe} closes from local cache"
            if row.sparkline
            else "no sparkline — close series empty"
        )
    row.risk = research_risk(ohlcv, cfg, row.buy_sell, validity=row.validity)
    attach_quote_session(row, ohlcv, cfg, now=now)
    return attach_mtf(row, ohlcv, cfg)


def attach_mtf(
    row: BoardRow,
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any],
) -> BoardRow:
    """Causal HTF SMA-slope badge. Does not change the flash unless configured."""
    sig = row.raw_signal or row.buy_sell
    row.mtf = assess_mtf(ohlcv, cfg, sig, validity=row.validity)
    return row


def apply_mtf_flash(row: BoardRow, cfg: dict[str, Any]) -> BoardRow:
    """Optional: flash HOLD or weaker when MTF conflicts. Default off."""
    mode = conflict_flash_mode(cfg)
    mtf = row.mtf
    if mtf is None or mtf.status != MTF_CONFLICT:
        return row
    live = str(row.buy_sell or "").upper()
    if live not in {"BUY", "SELL"}:
        return row
    if mode == FLASH_HOLD:
        row.raw_signal = row.raw_signal or row.buy_sell
        row.buy_sell = "HOLD"
        row.flash_weak = False
        row.signal_details = (
            f"MTF conflict — flashed HOLD (last model {row.raw_signal}; {mtf.note})"
        )
        # Keep the ATR box for the raw directional class so SL advice still has a level.
        return row
    if mode == FLASH_WEAKEN:
        row.flash_weak = True
        row.signal_details = (row.signal_details or "") + f"  |  weak — {mtf.note}"
    return row


def _details_from_signal(
    last: pd.Series | dict[str, Any],
    *,
    status: str | None = None,
    explanation: SignalExplanation | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    if status and status != STATUS_READY:
        return status
    get = last.get if hasattr(last, "get") else lambda k, d=None: last[k] if k in last else d  # type: ignore[index]
    conf = get("confidence")
    edge = get("dir_edge")
    model = get("model") or "n/a"
    when = _fmt_when(get("datetime"), cfg)
    pb, ps, ph = get("p_buy"), get("p_sell"), get("p_hold")
    base = (
        f"conf={_fmt(conf, 4)}  dir_edge={_fmt(edge, 4)}  "
        f"p_buy={_fmt(pb, 3)} p_sell={_fmt(ps, 3)} p_hold={_fmt(ph, 3)}  "
        f"{model}  {when}"
    )
    if explanation is None:
        return base
    extra = []
    drv = explanation.compact_drivers(3)
    if drv:
        extra.append(drv)
    rules = explanation.compact_rules()
    if rules:
        extra.append(rules)
    if explanation.rationale:
        extra.append(explanation.rationale[:180] + ("…" if len(explanation.rationale) > 180 else ""))
    return base + ("  |  " + "  |  ".join(extra) if extra else "")


def row_from_signal(
    pair: str,
    timeframe: str,
    last: pd.Series | dict[str, Any],
    ohlcv: pd.DataFrame | None,
    cfg: dict[str, Any],
    *,
    data_source: str | None = None,
    n_bars: int | None = None,
) -> BoardRow:
    get = last.get if hasattr(last, "get") else lambda k, d=None: last[k] if k in last else d  # type: ignore[index]
    sig = str(get("signal") or "n/a").upper()
    target, note = research_target(ohlcv, cfg, sig)

    def _num(key: str) -> float | None:
        v = get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    explanation = None
    if ohlcv is not None and not ohlcv.empty:
        explanation = explain_latest_signal(pair, ohlcv, cfg, last, target=target)

    row = BoardRow(
        pair=pair.upper(),
        timeframe=timeframe,
        buy_sell=sig,
        target=target,
        signal_details=_details_from_signal(last, explanation=explanation, cfg=cfg),
        status=STATUS_READY,
        confidence=_num("confidence"),
        dir_edge=_num("dir_edge"),
        p_buy=_num("p_buy"),
        p_sell=_num("p_sell"),
        p_hold=_num("p_hold"),
        model=None if get("model") is None else str(get("model")),
        datetime=_fmt_when(get("datetime"), cfg),
        close=_num("close"),
        raw_signal=None if get("raw_signal") is None else str(get("raw_signal")),
        target_note=note,
        data_source=data_source,
        n_bars=n_bars,
        rationale=None if explanation is None else explanation.rationale,
        explain_method=None if explanation is None else explanation.method,
        drivers=list(explanation.drivers) if explanation is not None else [],
        rules=list(explanation.rules) if explanation is not None else [],
        last_signal_at=_fmt_when(get("datetime"), cfg),
        validity=VALIDITY_OK,
    )
    return apply_open_gates(apply_mtf_flash(attach_visuals(row, ohlcv, cfg, now=None), cfg), cfg)


def _status_row(
    pair: str,
    timeframe: str,
    status: str,
    details: str,
    *,
    data_source: str | None = None,
    n_bars: int | None = None,
    error: str | None = None,
    target_note: str | None = None,
    validity: str = VALIDITY_MISSING,
    validity_reason: str = "",
    last_bar_at: str | None = None,
    last_fetch_at: str | None = None,
) -> BoardRow:
    return BoardRow(
        pair=pair.upper(),
        timeframe=timeframe,
        buy_sell="—",
        target="n/a",
        signal_details=details,
        status=status,
        data_source=data_source,
        n_bars=n_bars,
        error=error,
        target_note=target_note or "No signal until Fetch + Train have produced data and a model.",
        validity=validity,
        validity_reason=validity_reason or details,
        last_bar_at=last_bar_at,
        last_fetch_at=last_fetch_at,
    )


def apply_freshness(
    row: BoardRow,
    fresh: Freshness,
    *,
    last_fetch_at: str | None = None,
    cfg: dict[str, Any] | None = None,
) -> BoardRow:
    """Attach validity. STALE/MISSING/ERROR never flash a live BUY/SELL."""
    row.validity = fresh.validity
    row.validity_reason = relabel_in_text(fresh.reason, cfg) if fresh.reason else fresh.reason
    row.last_bar_at = fmt_display(fresh.last_bar, cfg) if fresh.last_bar else fresh.last_bar_label
    if last_fetch_at:
        row.last_fetch_at = last_fetch_at
    if row.status in {STATUS_NEED_FETCH, STATUS_NEED_TRAIN}:
        return row
    if not fresh.suppress_live_signal():
        return row
    if row.buy_sell and row.buy_sell not in {"—", "-", "n/a"}:
        row.raw_signal = row.raw_signal or row.buy_sell
    if fresh.validity == VALIDITY_STALE:
        row.buy_sell = "—"
        last_model = row.raw_signal or "n/a"
        row.signal_details = (
            f"data stale — refresh required (last model {last_model}; {row.validity_reason})"
        )
        if row.status == STATUS_READY:
            row.status = STATUS_STALE
        row.target = "n/a"
        row.sparkline = []
        row.sparkline_note = "no sparkline — data stale or missing (cached path not shown as live)"
        row.risk = RiskBox(False, reason="data stale — refresh required")
    elif fresh.validity in {VALIDITY_MISSING, VALIDITY_ERROR} and row.buy_sell not in {"—"}:
        row.buy_sell = "—"
    return row


def _cached_bars_without_signal(
    pair: str,
    interval: str,
    ohlcv: pd.DataFrame,
    cfg: dict[str, Any],
    *,
    lab_interval: str,
    data_source: str | None,
    fetch_at: str,
    now: Any,
) -> BoardRow:
    """Bars for this timeframe exist. The saved signal is for another timeframe."""
    fresh = assess_ohlcv(ohlcv, interval, cfg, now=now)
    label = FAILURE_TF_LABEL.get(str(interval), str(interval))
    note = (
        f"{label} bars are cached. No {interval} signal — "
        f"the saved signal is for {lab_interval} and was not copied."
    )
    if fresh.reason and fresh.validity in {VALIDITY_STALE, VALIDITY_ERROR, VALIDITY_CLOSED}:
        note = f"{note} {fresh.reason}"
    row = _status_row(
        pair,
        interval,
        STATUS_NEED_TRAIN,
        note,
        data_source=data_source or "cached",
        n_bars=len(ohlcv),
        validity=fresh.validity,
        validity_reason=note,
        last_bar_at=fresh.last_bar_label,
        last_fetch_at=fetch_at if fetch_at != "n/a" else None,
    )
    try:
        row.close = float(ohlcv["Close"].iloc[-1])
    except (TypeError, ValueError, IndexError):
        row.close = None
    return attach_visuals(row, ohlcv, cfg, now=now)


def _latest_csv_row(pair: str, cfg: dict[str, Any]) -> pd.Series | None:
    df = load_signals(cfg)
    if df is None or df.empty or "pair" not in df.columns:
        return None
    sub = df[df["pair"].astype(str).str.upper() == pair.upper()]
    if sub.empty:
        return None
    return sub.iloc[-1]


def build_board_row(
    pair: str,
    cfg: dict[str, Any] | None = None,
    *,
    interval: str | None = None,
    refresh_data: bool = False,
    regenerate: bool | None = None,
    now: Any = None,
    incremental: bool = True,
) -> BoardRow:
    """One watch-board row. Does not write ``signals/latest_signals.csv``.

    ``refresh_data`` tries yfinance (never synthetic). Signals are regenerated
    when ``regenerate`` is true, or when ``refresh_data`` is true, or when no
    matching row exists in the shared signals CSV.

    Stale/missing caches never flash BUY/SELL as a live call.
    """
    cfg = cfg if cfg is not None else load_config()
    pair = str(pair).upper()
    interval = str(interval or cfg.get("interval") or "1h")
    clock = now_utc(now)
    if regenerate is None:
        regenerate = bool(refresh_data)

    status = artifact_status(pair, cfg, interval=interval)
    data_source: str | None = None
    fetch_at = fmt_display(csv_mtime_utc(pair, cfg, interval), cfg, seconds=True)
    # Light yfinance refresh only when a model exists — never synthetic, never a silent fetch.
    if refresh_data and status.get("model_exists"):
        _df, reason = try_yfinance_refresh(
            pair, cfg, interval=interval, incremental=incremental
        )
        if _df is not None:
            data_source = reason
            fetch_at = fmt_display(clock, cfg, seconds=True)
        else:
            data_source = f"cached ({reason})"
        status = artifact_status(pair, cfg, interval=interval)

    n_bars = status.get("n_bars")
    if not status.get("data_exists"):
        return attach_visuals(
            _status_row(
                pair,
                interval,
                STATUS_NEED_FETCH,
                NEED_FETCH_TRAIN,
                data_source=data_source,
                n_bars=n_bars,
                error="data CSV missing",
                validity=VALIDITY_MISSING,
                validity_reason=format_failure_reason(
                    interval,
                    "no OHLCV cache",
                    last_ok=fetch_at if fetch_at != "n/a" else None,
                ),
                last_fetch_at=fetch_at if fetch_at != "n/a" else None,
            ),
            None,
            cfg,
            now=clock,
        )
    if not status.get("model_exists"):
        ohlcv_only = load_cached_ohlcv(pair, cfg, interval)
        fresh_m = assess_ohlcv(ohlcv_only, interval, cfg, now=clock)
        row = _status_row(
            pair,
            interval,
            STATUS_NEED_TRAIN,
            NEED_FETCH_TRAIN,
            data_source=data_source or "cached",
            n_bars=n_bars,
            error="model missing",
            validity=fresh_m.validity,
            validity_reason=fresh_m.reason,
            last_bar_at=fresh_m.last_bar_label,
            last_fetch_at=fetch_at if fetch_at != "n/a" else None,
        )
        return attach_visuals(
            apply_freshness(row, fresh_m, last_fetch_at=row.last_fetch_at, cfg=cfg),
            ohlcv_only,
            cfg,
            now=clock,
        )

    ohlcv = load_cached_ohlcv(pair, cfg, interval)
    if ohlcv is None or ohlcv.empty:
        return attach_visuals(
            _status_row(
                pair,
                interval,
                STATUS_NEED_FETCH,
                NEED_FETCH_TRAIN,
                data_source=data_source,
                error="data CSV unreadable",
                validity=VALIDITY_MISSING,
                validity_reason=format_failure_reason(interval, "data CSV unreadable"),
            ),
            None,
            cfg,
            now=clock,
        )

    lab_iv = str(cfg.get("interval") or "1h")
    if str(interval) != lab_iv:
        # The on-disk signal and model belong to the lab timeframe. Do not
        # replay that BUY/SELL on another interval, and do not score these
        # bars with a model trained on a different bar size.
        return _cached_bars_without_signal(
            pair,
            interval,
            ohlcv,
            cfg,
            lab_interval=lab_iv,
            data_source=data_source,
            fetch_at=fetch_at,
            now=clock,
        )

    last: pd.Series | dict[str, Any] | None = None
    if not regenerate:
        last = _latest_csv_row(pair, cfg)

    if last is None:
        try:
            sigs = generate_signals(ohlcv, cfg, pair)
        except FileNotFoundError:
            return attach_visuals(
                _status_row(
                    pair,
                    interval,
                    STATUS_NEED_TRAIN,
                    NEED_FETCH_TRAIN,
                    data_source=data_source or "cached",
                    n_bars=len(ohlcv),
                    error="model missing",
                ),
                ohlcv,
                cfg,
                now=clock,
            )
        except Exception as exc:  # noqa: BLE001 — surface as row status
            fresh_e = assess_ohlcv(ohlcv, interval, cfg, now=clock)
            row = _status_row(
                pair,
                interval,
                STATUS_ERROR,
                f"signal error: {exc}",
                data_source=data_source or "cached",
                n_bars=len(ohlcv),
                error=str(exc),
                validity=VALIDITY_ERROR,
                validity_reason=str(exc),
                last_bar_at=fresh_e.last_bar_label,
                last_fetch_at=fetch_at if fetch_at != "n/a" else None,
            )
            return attach_visuals(row, ohlcv, cfg, now=clock)
        if sigs is None or sigs.empty:
            return attach_visuals(
                _status_row(
                    pair,
                    interval,
                    STATUS_ERROR,
                    "model produced no signal rows",
                    data_source=data_source or "cached",
                    n_bars=len(ohlcv),
                    validity=VALIDITY_ERROR,
                    validity_reason="model produced no signal rows",
                ),
                ohlcv,
                cfg,
                now=clock,
            )
        last = sigs.iloc[-1]

    row = row_from_signal(
        pair,
        interval,
        last,
        ohlcv,
        cfg,
        data_source=data_source or "cached",
        n_bars=len(ohlcv),
    )
    fresh = assess_ohlcv(ohlcv, interval, cfg, now=clock)
    row = attach_visuals(
        apply_freshness(row, fresh, last_fetch_at=fetch_at if fetch_at != "n/a" else None, cfg=cfg),
        ohlcv,
        cfg,
        now=clock,
    )
    return apply_open_gates(apply_mtf_flash(row, cfg), cfg)


def build_board_rows(
    watchlist: Watchlist,
    cfg: dict[str, Any] | None = None,
    *,
    refresh_data: bool = False,
    regenerate: bool | None = None,
) -> list[BoardRow]:
    cfg = cfg if cfg is not None else load_config()
    lab_iv = watchlist.lab_interval(cfg)
    rows: list[BoardRow] = []
    for item in watchlist.pairs:
        iv = item.resolved_interval(lab_iv)
        rows.append(
            build_board_row(
                item.pair,
                cfg,
                interval=iv,
                refresh_data=refresh_data,
                regenerate=regenerate,
            )
        )
    return rows


BOARD_TABLE_COLS = [
    "Pair",
    "TF",
    "Signal",
    "Conf",
    "Data●",
    "MTF",
    "Session",
    "Last/mid",
    "Spread",
    "Next event",
    "Spark",
    "Actions",
]


def board_table(
    rows: list[BoardRow],
    *,
    events: list[CalendarEvent] | None = None,
    now: Any = None,
    cfg: dict[str, Any] | None = None,
) -> pd.DataFrame:
    cols = list(BOARD_TABLE_COLS)
    if not rows:
        return pd.DataFrame(columns=cols)
    records = []
    for r in rows:
        if events is not None:
            attach_next_event(r, events, now=now, cfg=cfg)
        else:
            apply_open_gates(r, cfg, events=None, now=now)
        records.append(r.as_table_dict())
    return pd.DataFrame(records)


def style_board(df: pd.DataFrame):
    """Color Signal, Data●, MTF, Session, and pre-event Next event cells."""
    if df is None or df.empty:
        return df

    from forex_lab.ui.theme import (
        action_cell_style,
        event_cell_style,
        mtf_cell_style,
        session_cell_style,
        signal_cell_style,
        validity_cell_style,
    )

    try:
        styler = df.style
        mapper = getattr(styler, "map", None) or getattr(styler, "applymap", None)
        if mapper is not None:
            sig_col = "Signal" if "Signal" in df.columns else ("Buy/Sell" if "Buy/Sell" in df.columns else None)
            if sig_col:
                styler = mapper(signal_cell_style, subset=[sig_col])
            data_col = "Data●" if "Data●" in df.columns else ("Validity" if "Validity" in df.columns else None)
            if data_col:
                styler = mapper(validity_cell_style, subset=[data_col])
            if "MTF" in df.columns:
                styler = mapper(mtf_cell_style, subset=["MTF"])
            if "Session" in df.columns:
                styler = mapper(session_cell_style, subset=["Session"])
            if "Next event" in df.columns:
                styler = mapper(event_cell_style, subset=["Next event"])
            if "Actions" in df.columns:
                styler = mapper(action_cell_style, subset=["Actions"])
        return styler
    except Exception:
        return df
