"""Signal brief — horizon suggestions from lab data only.

Hourly/Intraday + Daily cards in the form:

    Hourly: Potential buy: now at 1.14863, stop loss 1.14850, target 1.14890, duration 3 hours.

Not a vendor note, not an order, not a live edge. STALE / MISSING block the brief.
SL/TP come from existing ATR barriers; missing TF → need Fetch/Train, never fake levels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from forex_lab.data import load_cached_ohlcv
from forex_lab.features import ema, rsi
from forex_lab.freshness import VALIDITY_ERROR, VALIDITY_MISSING, VALIDITY_STALE
from forex_lab.mtf import MTF_CONFLICT
from forex_lab.ui.board import research_risk

_BLOCKED = frozenset({VALIDITY_STALE, VALIDITY_MISSING, VALIDITY_ERROR, "FAIL"})
_INTRADAY = frozenset({"1m", "5m", "15m", "30m", "1h", "4h"})
_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}

DISCLAIMER = (
    "Research suggestions on cached yfinance last/mid — not an order, not financial advice, "
    "not a broker quote. Paper BUY/SELL is a local journal via BrokerPort. "
    "STALE or MISSING is not a live call."
)


@dataclass
class HorizonCard:
    kicker: str
    interval: str
    side: str
    line: str
    now_at: str
    stop_loss: str
    target: str
    duration: str
    available: bool = False
    missing_reason: str = ""
    resampled: bool = False


@dataclass
class AdviceView:
    """Legacy alias used by older HTML helpers — maps onto a HorizonCard."""

    kicker: str
    side: str
    action: str
    take_profit: str
    stop_loss: str
    timeline: str
    note: str
    available: bool = False
    line: str = ""


@dataclass
class AdviceBlock:
    """Bearish / Bullish reading block — heading + bullets, DailyForex-like hierarchy."""

    title: str
    tone: str
    bullets: list[str] = field(default_factory=list)


@dataclass
class SignalBrief:
    pair: str
    pair_slash: str
    headline: str
    bias: str
    clock: str
    blocked: bool
    block_reason: str
    byline: str = ""
    horizons: list[HorizonCard] = field(default_factory=list)
    blocks: list[AdviceBlock] = field(default_factory=list)
    primary: AdviceView | None = None
    alternate: AdviceView | None = None
    why: str = ""
    tech_bullets: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER


def pair_slash(pair: str) -> str:
    p = str(pair or "").upper().replace("/", "").replace("-", "")
    if len(p) == 6 and p.isalpha():
        return f"{p[:3]}/{p[3:]}"
    return str(pair or "").upper() or "—"


def bias_label(signal: object, *, validity: object = "") -> str:
    token = str(validity or "").strip().upper().split()[0] if validity else ""
    if token in _BLOCKED:
        return "No live call"
    sig = str(signal or "").upper()
    if sig == "SELL":
        return "Bearish outlook"
    if sig == "BUY":
        return "Bullish outlook"
    if sig == "HOLD":
        return "Neutral outlook"
    return "No live call"


def kicker_for_interval(interval: str) -> str:
    iv = str(interval or "").lower()
    if iv in {"1d", "d", "1day", "day"}:
        return "Daily"
    if iv in {"1m", "5m", "15m", "30m"}:
        return "Intraday"
    return "Hourly"


def duration_phrase(interval: str, horizon_bars: int) -> str:
    iv = str(interval or "1h").lower()
    bars = max(1, int(horizon_bars or 1))
    if iv in {"1d", "d", "1day", "day"}:
        return "1-2 days" if bars <= 3 else f"{bars} days"
    minutes = _MINUTES.get(iv)
    if minutes is None:
        return f"{bars} × {interval} bars"
    total_m = bars * minutes
    if total_m < 60:
        return f"{total_m} minutes" if total_m != 1 else "1 minute"
    hours = total_m / 60.0
    if hours < 24:
        whole = int(round(hours))
        return "1 hour" if whole == 1 else f"{whole} hours"
    days = hours / 24.0
    if days <= 2:
        return "1-2 days"
    return f"{int(round(days))} days"


def potential_action(side: str) -> str:
    sig = str(side or "").upper()
    if sig == "BUY":
        return "Potential buy"
    if sig == "SELL":
        return "Potential sell"
    return "No trade (HOLD)"


def format_horizon_line(
    *,
    kicker: str,
    side: str,
    now_at: str,
    stop_loss: str,
    target: str,
    duration: str,
) -> str:
    action = potential_action(side)
    return (
        f"{kicker}: {action}: now at {now_at}, stop loss {stop_loss}, "
        f"target {target}, duration {duration}."
    )


def _px(value: object, pair: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    digits = 3 if "JPY" in str(pair).upper() else 5
    return f"{v:.{digits}f}"


def _last_price(row: Any) -> float | None:
    close = getattr(row, "close", None)
    try:
        if close is not None and pd.notna(float(close)):
            return float(close)
    except (TypeError, ValueError):
        pass
    q = getattr(row, "quote", None)
    if q is not None:
        for attr in ("last", "close", "mid"):
            raw = getattr(q, attr, None)
            try:
                if raw is not None and pd.notna(float(raw)):
                    return float(raw)
            except (TypeError, ValueError):
                continue
    return None


def _last_label(row: Any, pair: str = "") -> str:
    px = _last_price(row)
    if px is not None:
        return _px(px, pair)
    q = getattr(row, "quote", None)
    if q is not None and hasattr(q, "last_label"):
        return str(q.last_label())
    return "n/a"


def _resample_daily(ohlcv: Any) -> pd.DataFrame | None:
    if ohlcv is None or getattr(ohlcv, "empty", True):
        return None
    need = ("Open", "High", "Low", "Close")
    if any(c not in ohlcv.columns for c in need):
        return None
    frame = ohlcv.copy()
    try:
        frame.index = pd.to_datetime(frame.index)
    except Exception:
        return None
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in frame.columns:
        agg["Volume"] = "sum"
    out = frame.resample("1D").agg(agg).dropna(subset=["Open", "High", "Low", "Close"])
    return out if len(out) >= 16 else None


def _load_horizon_frame(
    pair: str,
    cfg: dict[str, Any],
    interval: str,
    fallback: Any,
) -> tuple[Any, bool]:
    native = None
    try:
        native = load_cached_ohlcv(pair, cfg, interval)
    except Exception:
        native = None
    if native is not None and not getattr(native, "empty", True):
        return native, False
    if interval in {"1d", "1D"}:
        daily = _resample_daily(fallback)
        if daily is not None:
            return daily, True
    if fallback is not None and str(interval) == str(getattr(fallback, "attrs", {}).get("interval", "")):
        return fallback, False
    return None, False


def _horizon_card(
    *,
    interval: str,
    row: Any,
    cfg: dict[str, Any],
    ohlcv: Any,
    resampled: bool,
    now_at: str,
    now_price: float | None,
) -> HorizonCard:
    pair = str(getattr(row, "pair", "") or "")
    kicker = kicker_for_interval(interval)
    side = str(getattr(row, "buy_sell", "") or "HOLD").upper()
    if side not in {"BUY", "SELL", "HOLD"}:
        side = "HOLD"
    horizon_bars = 2 if kicker == "Daily" else int(cfg.get("horizon") or 8)
    duration = duration_phrase(interval, horizon_bars)
    if ohlcv is None or getattr(ohlcv, "empty", True):
        reason = f"need Fetch/Train for {interval}"
        line = f"{kicker}: {reason}."
        return HorizonCard(
            kicker=kicker,
            interval=interval,
            side=side,
            line=line,
            now_at=now_at,
            stop_loss="n/a",
            target="n/a",
            duration=duration,
            available=False,
            missing_reason=reason,
            resampled=resampled,
        )
    if side == "HOLD":
        line = format_horizon_line(
            kicker=kicker,
            side="HOLD",
            now_at=now_at,
            stop_loss="n/a",
            target="n/a",
            duration=duration,
        )
        return HorizonCard(
            kicker=kicker,
            interval=interval,
            side="HOLD",
            line=line,
            now_at=now_at,
            stop_loss="n/a",
            target="n/a",
            duration=duration,
            available=False,
            missing_reason="HOLD — no stop loss / target",
            resampled=resampled,
        )
    validity = str(getattr(row, "validity", "") or VALIDITY_OK)
    risk = research_risk(ohlcv, cfg, side, validity=validity)
    if not risk.available or risk.atr is None or risk.sl_atr is None or risk.tp_atr is None:
        reason = risk.reason if risk is not None else f"need Fetch/Train for {interval}"
        if not reason:
            reason = f"need Fetch/Train for {interval}"
        line = f"{kicker}: {reason}."
        return HorizonCard(
            kicker=kicker,
            interval=interval,
            side=side,
            line=line,
            now_at=now_at,
            stop_loss="n/a",
            target="n/a",
            duration=duration,
            available=False,
            missing_reason=reason,
            resampled=resampled,
        )
    entry = now_price if now_price is not None else float(risk.entry or 0)
    sl_d = float(risk.sl_atr) * float(risk.atr)
    tp_d = float(risk.tp_atr) * float(risk.atr)
    if side == "BUY":
        sl, tp = entry - sl_d, entry + tp_d
    else:
        sl, tp = entry + sl_d, entry - tp_d
    sl_s, tp_s = _px(sl, pair), _px(tp, pair)
    line = format_horizon_line(
        kicker=kicker,
        side=side,
        now_at=now_at,
        stop_loss=sl_s,
        target=tp_s,
        duration=duration,
    )
    note_src = "resampled daily from watchlist cache" if resampled else f"{interval} cache"
    return HorizonCard(
        kicker=kicker,
        interval=interval,
        side=side,
        line=line,
        now_at=now_at,
        stop_loss=sl_s,
        target=tp_s,
        duration=duration,
        available=True,
        missing_reason=f"ATR on {note_src} — research suggestion, not an order.",
        resampled=resampled,
    )


def advice_blocks(
    cards: list[HorizonCard],
    *,
    pair_slash: str = "",
    board_side: str = "",
) -> list[AdviceBlock]:
    """Bearish view then Bullish view. Opposite side reuses the same ATR pair, swapped.

    Does not invent levels: missing TF stays ``need Fetch/Train`` with n/a.
    """
    del pair_slash, board_side
    out: list[AdviceBlock] = []
    for title, want, tone in (
        ("Bearish view", "SELL", "sell"),
        ("Bullish view", "BUY", "buy"),
    ):
        bullets: list[str] = []
        for card in cards or []:
            kicker = card.kicker or "Hourly"
            if card.side == "HOLD" and (not card.available or card.stop_loss in {"", "n/a"}):
                bullets.append(f"{kicker}: No trade (HOLD) at {card.now_at}.")
                bullets.append(f"Timeline: {card.duration}.")
                continue
            if not card.available or card.stop_loss in {"", "n/a"} or card.target in {"", "n/a"}:
                reason = card.missing_reason or f"need Fetch/Train for {card.interval}"
                bullets.append(f"{kicker}: {reason}.")
                continue
            if card.side == want:
                sl, tp, act_side = card.stop_loss, card.target, card.side
            else:
                sl, tp, act_side = card.target, card.stop_loss, want
            action = potential_action(act_side)
            bullets.append(
                f"{kicker}: {action}: now at {card.now_at}, stop loss {sl}, target {tp}."
            )
            bullets.append(f"Timeline: {card.duration}.")
        if not bullets:
            bullets.append(f"No {title.lower()} on this cache.")
        out.append(AdviceBlock(title=title, tone=tone, bullets=bullets))
    return out


def _as_advice_view(card: HorizonCard | None) -> AdviceView | None:
    if card is None:
        return None
    action = potential_action(card.side)
    return AdviceView(
        kicker=card.kicker,
        side=card.side,
        action=action,
        take_profit=card.target,
        stop_loss=card.stop_loss,
        timeline=f"duration {card.duration}",
        note=card.missing_reason or card.line,
        available=card.available,
        line=card.line,
    )


def invalidation_lines(
    row: Any,
    cards: list[HorizonCard],
    *,
    calendar: Any = None,
) -> list[str]:
    """Plain-English flip / invalidation from SL, MTF, STALE, calendar — no invented levels."""
    lines: list[str] = []
    used = next((c for c in cards if c.available and c.stop_loss not in {"", "n/a"}), None)
    if used is not None:
        if used.side == "BUY":
            lines.append(
                f"A break below the {used.kicker.lower()} stop-loss {used.stop_loss} "
                "invalidates the potential buy."
            )
        elif used.side == "SELL":
            lines.append(
                f"A break above the {used.kicker.lower()} stop-loss {used.stop_loss} "
                "invalidates the potential sell."
            )
    mtf = getattr(row, "mtf", None)
    status = str(getattr(mtf, "status", "") or "").lower() if mtf is not None else ""
    if status == MTF_CONFLICT or status == "conflict":
        tf = str(getattr(mtf, "timeframe", "") or "higher TF")
        note = str(getattr(mtf, "note", "") or "higher-TF slope disagrees with the flash")
        lines.append(f"MTF conflict ({tf}): {note.rstrip('.')}. Treat the flash as weak, not a live call.")
    nxt = str(getattr(row, "next_event", "") or "").strip()
    if getattr(row, "next_event_warn", False) and nxt and nxt not in {"—", "-"}:
        lines.append(
            f"High-impact calendar window ({nxt}) — do not treat the suggestion as live through the print."
        )
    elif calendar is not None and getattr(calendar, "error", None) and not getattr(calendar, "events", None):
        lines.append("Calendar feed is unavailable this tick; event invalidation is unknown.")
    lines.append(
        "If Data● turns STALE or MISSING, this is not a live call — Fetch first "
        "(Lab → Run pipeline with Also fetch, or Fetch)."
    )
    return lines[:4]


def _driver_clause(row: Any) -> str:
    drivers = list(getattr(row, "drivers", None) or [])
    if not drivers:
        return ""
    parts = []
    for d in drivers[:3]:
        name = getattr(d, "hint", None) or getattr(d, "feature", None) or str(d)
        feat = getattr(d, "feature", "")
        parts.append(str(name or feat))
    if not parts:
        return ""
    return "Local drivers on this bar: " + ", ".join(parts) + "."


def synthesize_why(
    row: Any,
    *,
    news: Any = None,
    clock_label: str = "",
) -> str:
    """2–4 plain sentences from rationale, SHAP, news bias, and next event."""
    pair = pair_slash(getattr(row, "pair", ""))
    tf = str(getattr(row, "timeframe", "") or "")
    last = _last_label(row, getattr(row, "pair", ""))
    sig = str(getattr(row, "buy_sell", "") or "").upper()
    clock = clock_label or "Asia/Dhaka"
    sentences: list[str] = []

    if sig == "SELL":
        sentences.append(
            f"{pair} is flashing SELL at {last} on the {tf} cache ({clock}). "
            "That is a research label, not an order."
        )
    elif sig == "BUY":
        sentences.append(
            f"{pair} is flashing BUY at {last} on the {tf} cache ({clock}). "
            "That is a research label, not an order."
        )
    elif sig == "HOLD":
        sentences.append(
            f"{pair} is HOLD at {last} on the {tf} cache ({clock}). "
            "No directional flash on this bar."
        )
    else:
        sentences.append(f"{pair} has no live BUY/SELL flash at {last} ({clock}).")

    rationale = str(getattr(row, "rationale", "") or "").strip()
    if rationale:
        first = rationale.split(". ")[0].strip()
        if first and len(first) < 320:
            sentences.append(first if first.endswith(".") else first + ".")
    else:
        clause = _driver_clause(row)
        if clause:
            sentences.append(clause)

    bias = str(getattr(news, "bias", "") or "").lower() if news is not None else ""
    if bias in {"bullish", "bearish", "mixed"}:
        sentences.append(
            f"Fetched headlines lean {bias} — news context, not a trade instruction."
        )
    elif news is not None and getattr(news, "error", None):
        sentences.append(
            "News feed was empty this tick; the math label does not wait on headlines."
        )

    nxt = str(getattr(row, "next_event", "") or "").strip()
    if nxt and nxt not in {"—", "-", "n/a", "N/A"}:
        extra = " Pre-event window is live." if getattr(row, "next_event_warn", False) else ""
        sentences.append(f"Next high-impact print: {nxt}.{extra}")

    return " ".join(sentences[:4])


def tech_bullets(
    row: Any,
    ohlcv: Any,
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    """2–3 bullets from causal EMA/RSI on this cache plus MTF — no invented patterns."""
    cfg = cfg or {}
    pair = str(getattr(row, "pair", "") or "")
    if ohlcv is None or getattr(ohlcv, "empty", True) or "Close" not in getattr(ohlcv, "columns", []):
        return ["No cached OHLC for technical notes — Fetch first."]
    close = pd.to_numeric(ohlcv["Close"], errors="coerce")
    last = float(close.iloc[-1]) if close.notna().any() else None
    if last is None or pd.isna(last):
        return ["No last close on this cache."]
    bullets: list[str] = []
    e50 = ema(close, 50)
    if e50.notna().iloc[-1]:
        v = float(e50.iloc[-1])
        rel = "above" if last > v else "below"
        bullets.append(
            f"Last close is {rel} EMA(50) at {_px(v, pair)} (causal EMA on this cache)."
        )
    e200 = ema(close, 200)
    if e200.notna().iloc[-1]:
        v = float(e200.iloc[-1])
        rel = "above" if last > v else "below"
        bullets.append(f"Price is {rel} EMA(200) at {_px(v, pair)}.")
    period = int(cfg.get("rsi_period") or 14)
    r = rsi(close, period)
    if r.notna().iloc[-1]:
        rv = float(r.iloc[-1])
        zone = "overbought" if rv >= 70 else ("oversold" if rv <= 30 else "mid-range")
        bullets.append(
            f"RSI({period}) is {rv:.0f} ({zone}) — same Wilder RSI as model features."
        )
    mtf = getattr(row, "mtf", None)
    if mtf is not None and len(bullets) < 3:
        status = str(getattr(mtf, "status", "") or "")
        note = str(getattr(mtf, "note", "") or "causal higher-TF SMA slope on the same CSV")
        if status:
            bullets.append(f"MTF {status}: {note}.")
    return bullets[:3]


def build_signal_brief(
    row: Any,
    *,
    cfg: dict[str, Any] | None = None,
    ohlcv: Any = None,
    daily_ohlcv: Any = None,
    news: Any = None,
    calendar: Any = None,
    clock_label: str = "",
) -> SignalBrief:
    """Assemble the pair note. STALE/MISSING/ERROR → blocked (Fetch, no live call)."""
    cfg = cfg or {}
    pair = str(getattr(row, "pair", "") or "")
    slash = pair_slash(pair)
    validity = str(getattr(row, "validity", "") or "")
    token = validity.strip().upper().split()[0] if validity else ""
    clock = clock_label or "Asia/Dhaka"
    bias = bias_label(getattr(row, "buy_sell", None), validity=validity)
    headline = f"{slash} Forex Signal: {bias}"
    byline = f"Cached research note · {clock} · not an order"
    if token in _BLOCKED:
        reason = (
            str(getattr(row, "validity_reason", "") or "")
            or str(getattr(row, "signal_details", "") or "")
            or f"Data● is {token}. Refresh (Fetch) first — not a live call."
        )
        return SignalBrief(
            pair=pair,
            pair_slash=slash,
            headline=headline,
            bias=bias,
            clock=clock,
            byline=byline,
            blocked=True,
            block_reason=reason,
            why="",
            tech_bullets=[],
            invalidation=[
                "Data is STALE or MISSING — not a live call. Fetch first, then re-read the brief."
            ],
        )
    watch_tf = str(getattr(row, "timeframe", "") or cfg.get("interval") or "1h")
    intra_tf = watch_tf if watch_tf in _INTRADAY else "1h"
    now_price = _last_price(row)
    now_at = _last_label(row, pair)

    intra_frame = ohlcv
    intra_resampled = False
    if intra_frame is None or getattr(intra_frame, "empty", True):
        intra_frame, intra_resampled = _load_horizon_frame(pair, cfg, intra_tf, None)

    daily_frame = daily_ohlcv
    daily_resampled = False
    if daily_frame is None or getattr(daily_frame, "empty", True):
        fallback = ohlcv if ohlcv is not None else intra_frame
        daily_frame, daily_resampled = _load_horizon_frame(pair, cfg, "1d", fallback)

    hourly = _horizon_card(
        interval=intra_tf,
        row=row,
        cfg=cfg,
        ohlcv=intra_frame,
        resampled=intra_resampled,
        now_at=now_at,
        now_price=now_price,
    )
    daily = _horizon_card(
        interval="1d",
        row=row,
        cfg=cfg,
        ohlcv=daily_frame,
        resampled=daily_resampled,
        now_at=now_at,
        now_price=now_price,
    )
    horizons = [hourly, daily]
    return SignalBrief(
        pair=pair,
        pair_slash=slash,
        headline=headline,
        bias=bias,
        clock=clock,
        byline=byline,
        blocked=False,
        block_reason="",
        horizons=horizons,
        blocks=advice_blocks(
            horizons,
            pair_slash=slash,
            board_side=str(getattr(row, "buy_sell", "") or ""),
        ),
        primary=_as_advice_view(hourly),
        alternate=_as_advice_view(daily),
        why=synthesize_why(row, news=news, clock_label=clock),
        tech_bullets=tech_bullets(row, ohlcv if ohlcv is not None else intra_frame, cfg),
        invalidation=invalidation_lines(row, horizons, calendar=calendar),
    )
