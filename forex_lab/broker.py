"""BrokerPort + PaperBroker — practice desk, not live trading.

The Streamlit Buy/Sell/Close buttons talk only to ``BrokerPort``. The default
implementation is ``PaperBroker`` (local JSON fills at the cached last close).

A future ``mt5`` / ``oanda`` backend would implement the same four methods.
This repo does **not** store API keys, load vendor SDKs, or place live orders.
Set ``broker.backend: paper`` (the only supported value).
"""
from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from forex_lab.config_loader import pip_size_for_pair
from forex_lab.paths import resolve_under_root
from forex_lab.score import (
    conf_bucket,
    filter_journal,
    improvement_notes,
    journal_aggregates,
    normalize_outcome,
    outcome_from_exit,
    score_from_ohlcv,
    signed_return,
    walk_barriers,
)

CLOSED_OUTCOMES = ("RIGHT", "WRONG", "FLAT")


class BrokerError(RuntimeError):
    """User-facing broker/port error (missing price, unknown backend, …)."""


class BrokerPort(ABC):
    """Minimal order gateway. UI must not call a vendor SDK directly.

    Live backends (future ``mt5`` / ``oanda``) implement these four methods only.
    Paper-specific extras (mark-to-market, journal scoring) stay on PaperBroker.
    """

    @abstractmethod
    def submit(
        self,
        side: str,
        pair: str,
        size: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        **meta: Any,
    ) -> dict[str, Any]:
        """Open a position. Returns the fill record."""

    @abstractmethod
    def close(self, position_id: str, **meta: Any) -> dict[str, Any]:
        """Close an open position. Returns the close fill."""

    @abstractmethod
    def list_positions(self) -> list[dict[str, Any]]:
        """Open positions only."""

    @abstractmethod
    def list_fills(self) -> list[dict[str, Any]]:
        """All fills (open + close), oldest first."""


# Alias used in product copy. Same contract.
OrderGateway = BrokerPort


def position_for_pair(port: BrokerPort, pair: str) -> dict[str, Any] | None:
    """Lookup via list_positions() so the UI never needs a PaperBroker method."""
    key = str(pair).upper()
    for p in port.list_positions():
        if str(p.get("pair")).upper() == key:
            return p
    return None


def broker_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    return dict((cfg or {}).get("broker") or {})


def make_broker(cfg: dict[str, Any] | None = None, *, path: Path | None = None) -> BrokerPort:
    """Factory. Only ``paper`` is implemented."""
    bcfg = broker_cfg(cfg)
    backend = str(bcfg.get("backend") or "paper").lower().strip()
    if backend in {"mt5", "oanda", "live", "real"}:
        raise BrokerError(
            f"broker.backend={backend} is not implemented. Only 'paper' is supported. "
            "A future backend would subclass BrokerPort (submit/close/list_positions/"
            "list_fills) — this lab does not store credentials or wire live orders."
        )
    if backend != "paper":
        raise BrokerError(f"unknown broker.backend={backend} (supported: paper)")
    store = path or resolve_under_root(bcfg.get("store") or "data/paper_broker.json")
    size = float(bcfg.get("default_size") or 1.0)
    return PaperBroker(store, default_size=size, cfg=cfg or {})


DEFAULT_OPENS_PER_HOUR = 3
MAX_OPENS_PER_HOUR = 99
DEFAULT_MIN_CONFIDENCE = 65
MIN_CONFIDENCE_FLOOR = 50
MIN_CONFIDENCE_CAP = 90


def _clamp_opens_per_hour(value: object, default: int = DEFAULT_OPENS_PER_HOUR) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, min(MAX_OPENS_PER_HOUR, n))


def _clamp_min_confidence(value: object, default: int = DEFAULT_MIN_CONFIDENCE) -> int:
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(MIN_CONFIDENCE_FLOOR, min(MIN_CONFIDENCE_CAP, n))


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def session_name(ts: object, cfg: dict[str, Any] | None = None) -> str:
    """Journal session bucket — same UTC windows as ``classify_session`` / board badge.

    Older hard-coded hours treated 21:00–24:00 UTC as ``off`` and London∩NY as
    ``overlap``. The desk uses Asia wrap (21–07) and ``london+ny``; mismatching
    those labels skewed paper ``by_session`` aggregates vs the board clock.
    """
    from forex_lab.session import classify_session

    t = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(t):
        return "n/a"
    py = t.to_pydatetime()
    if py.tzinfo is not None:
        py = py.astimezone(timezone.utc).replace(tzinfo=None)
    return classify_session(py, cfg).name


def _spread_frac(pair: str, cfg: dict[str, Any], price: float) -> float:
    pips = float(cfg.get("spread_pips", 1.0)) + float(cfg.get("commission_pips", 0.0))
    pip = pip_size_for_pair(pair, cfg)
    return (pips * pip) / max(price, 1e-12)


class PaperBroker(BrokerPort):
    """Local JSON practice broker. Fills at the caller-supplied cached last close.

    Never invents a price. Never talks to a venue.
    """

    def __init__(
        self,
        store: str | Path,
        *,
        default_size: float = 1.0,
        cfg: dict[str, Any] | None = None,
    ) -> None:
        self.store = Path(store)
        self.default_size = float(default_size)
        self.cfg = cfg or {}
        self._state = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "backend": "paper",
            "positions": [],
            "fills": [],
            "closed": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.store.exists() or self.store.stat().st_size == 0:
            return self._empty()
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        data.setdefault("backend", "paper")
        data.setdefault("positions", [])
        data.setdefault("fills", [])
        data.setdefault("closed", [])
        return data

    def _save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.store.with_suffix(self.store.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.store)

    def reload(self) -> None:
        self._state = self._load()

    def read_auto(self) -> dict[str, Any]:
        """Paper-only auto switch. Missing key means enabled — old journals stay on.

        ``max_opens_per_hour`` defaults to 3 when the journal has no setting.
        The cap is one shared budget for every pair.
        ``min_confidence`` is a percent from 50 to 90 and defaults to 65.
        It is also one shared setting for every pair.
        """
        auto = self._state.get("auto")
        if not isinstance(auto, dict):
            auto = {}
        seen_raw = auto.get("seen") if isinstance(auto.get("seen"), dict) else {}
        seen = {str(k).upper(): "" if v is None else str(v) for k, v in seen_raw.items()}
        enabled = auto.get("enabled", True)
        return {
            "enabled": bool(enabled),
            "seen": seen,
            "max_opens_per_hour": _clamp_opens_per_hour(auto.get("max_opens_per_hour", DEFAULT_OPENS_PER_HOUR)),
            "min_confidence": _clamp_min_confidence(auto.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
        }

    def write_auto(
        self,
        *,
        enabled: bool | None = None,
        seen: dict[str, str] | None = None,
        max_opens_per_hour: int | None = None,
        min_confidence: int | None = None,
    ) -> dict[str, Any]:
        """Persist auto flags without touching open or closed rows."""
        auto = self._state.get("auto")
        if not isinstance(auto, dict):
            auto = {}
        if enabled is not None:
            auto["enabled"] = bool(enabled)
        if seen is not None:
            auto["seen"] = {str(k).upper(): "" if v is None else str(v) for k, v in seen.items()}
        if max_opens_per_hour is not None:
            auto["max_opens_per_hour"] = _clamp_opens_per_hour(max_opens_per_hour)
        if min_confidence is not None:
            auto["min_confidence"] = _clamp_min_confidence(min_confidence)
        self._state["auto"] = auto
        self._save()
        return self.read_auto()

    def submit(
        self,
        side: str,
        pair: str,
        size: float | None = None,
        sl: float | None = None,
        tp: float | None = None,
        **meta: Any,
    ) -> dict[str, Any]:
        side_u = str(side or "").upper()
        if side_u not in {"BUY", "SELL"}:
            raise BrokerError("paper side must be BUY or SELL")
        pair_u = str(pair).upper()
        price = meta.get("price")
        try:
            px = float(price) if price is not None else float("nan")
        except (TypeError, ValueError):
            px = float("nan")
        if not pd.notna(px) or px <= 0:
            raise BrokerError("no reference price — cannot paper-fill (no invented quotes)")
        for pos in self._state["positions"]:
            if str(pos.get("pair")).upper() == pair_u and str(pos.get("status")) == "open":
                raise BrokerError(f"{pair_u} already has an open paper position — close it first")
        qty = float(size if size is not None else self.default_size)
        if qty <= 0:
            raise BrokerError("paper size must be positive")
        now = meta.get("timestamp") or _now_label()
        pid = _new_id("pos")
        fid = _new_id("fill")
        spread = _spread_frac(pair_u, self.cfg, px)
        raw_horizon = meta.get("horizon", None)
        if raw_horizon is None:
            horizon = int(self.cfg.get("horizon") or 8)
        else:
            try:
                horizon = int(raw_horizon)
            except (TypeError, ValueError):
                horizon = int(self.cfg.get("horizon") or 8)
        source = str(meta.get("source") or "manual")
        pos = {
            "id": pid,
            "pair": pair_u,
            "side": side_u,
            "size": qty,
            "entry_price": px,
            "entry_time": now,
            "entry_ref": str(meta.get("entry_ref") or "last close (paper fill; not a broker quote)"),
            "timeframe": str(meta.get("timeframe") or ""),
            "sl": None if sl is None else float(sl),
            "tp": None if tp is None else float(tp),
            "status": "open",
            "validity_at_entry": str(meta.get("validity") or ""),
            "model_signal": str(meta.get("model_signal") or ""),
            "confidence": meta.get("confidence"),
            "dir_edge": meta.get("dir_edge"),
            "p_buy": meta.get("p_buy"),
            "p_sell": meta.get("p_sell"),
            "p_hold": meta.get("p_hold"),
            "rationale": str(meta.get("rationale") or "")[:400],
            "drivers": str(meta.get("drivers") or "")[:300],
            "entry_bar_time": str(meta.get("entry_bar_time") or ""),
            "horizon": horizon,
            "source": source,
            "spread_frac": spread,
            "unrealized": -spread * qty,
            "session": session_name(meta.get("entry_bar_time") or now, self.cfg),
            "conf_bucket": conf_bucket(meta.get("confidence")),
            "note": str(meta.get("note") or ""),
            "news_bias": str(meta.get("news_bias") or ""),
            "news_note": str(meta.get("news_note") or "")[:160],
            "outcome": "PENDING",
        }
        fill = {
            "id": fid,
            "position_id": pid,
            "pair": pair_u,
            "side": side_u,
            "qty": qty,
            "price": px,
            "time": now,
            "kind": "open",
            "backend": "paper",
            "confidence": meta.get("confidence"),
            "model_signal": str(meta.get("model_signal") or ""),
            "source": source,
        }
        self._state["positions"].append(pos)
        self._state["fills"].append(fill)
        self._save()
        return fill

    def close(self, position_id: str, **meta: Any) -> dict[str, Any]:
        pid = str(position_id)
        pos = next((p for p in self._state["positions"] if p.get("id") == pid), None)
        if pos is None:
            raise BrokerError(f"no open paper position {pid}")
        price = meta.get("price")
        try:
            px = float(price) if price is not None else float("nan")
        except (TypeError, ValueError):
            px = float("nan")
        if not pd.notna(px) or px <= 0:
            raise BrokerError("no exit price — cannot close paper position")
        reason = str(meta.get("reason") or "manual")
        return self._finalize(pos, px, reason, meta.get("timestamp") or _now_label())

    def _finalize(
        self,
        pos: dict[str, Any],
        exit_px: float,
        reason: str,
        when: str,
    ) -> dict[str, Any]:
        entry = float(pos["entry_price"])
        qty = float(pos["size"])
        spread = float(pos.get("spread_frac") or 0.0)
        realized = (signed_return(str(pos["side"]), entry, exit_px) - spread) * qty
        outcome = outcome_from_exit(
            reason,
            side=str(pos["side"]),
            entry=entry,
            exit_px=float(exit_px),
            spread_frac=spread,
        )
        fid = _new_id("fill")
        fill = {
            "id": fid,
            "position_id": pos["id"],
            "pair": pos["pair"],
            "side": "SELL" if pos["side"] == "BUY" else "BUY",
            "qty": qty,
            "price": float(exit_px),
            "time": when,
            "kind": "close",
            "reason": reason,
            "backend": "paper",
            "confidence": pos.get("confidence"),
            "model_signal": str(pos.get("model_signal") or ""),
            "source": str(pos.get("source") or ""),
        }
        closed = dict(pos)
        closed.update(
            {
                "status": "closed",
                "exit_price": float(exit_px),
                "exit_time": when,
                "exit_reason": reason,
                "realized": realized,
                "unrealized": 0.0,
                "outcome": outcome,
            }
        )
        self._state["positions"] = [p for p in self._state["positions"] if p.get("id") != pos["id"]]
        self._state["closed"].append(closed)
        self._state["fills"].append(fill)
        self._save()
        return fill

    def list_positions(self) -> list[dict[str, Any]]:
        return list(self._state.get("positions") or [])

    def list_fills(self) -> list[dict[str, Any]]:
        return list(self._state.get("fills") or [])

    def modify_sl(
        self,
        position_id: str,
        sl: float,
        **meta: Any,
    ) -> dict[str, Any]:
        """Paper-only extra (not on BrokerPort). Tightens SL; never auto-called.

        The UI must only invoke this after an explicit click. A live backend
        would need its own modify path; this repo does not add it to the
        four-method BrokerPort contract.
        """
        pid = str(position_id)
        pos = next(
            (p for p in self._state["positions"] if p.get("id") == pid and p.get("status") == "open"),
            None,
        )
        if pos is None:
            raise BrokerError(f"no open paper position {pid}")
        try:
            new_sl = float(sl)
        except (TypeError, ValueError):
            raise BrokerError("paper SL must be a number") from None
        if not pd.notna(new_sl) or new_sl <= 0:
            raise BrokerError("paper SL must be a positive price")
        side = str(pos.get("side") or "").upper()
        price = meta.get("price")
        try:
            px = float(price) if price is not None else float("nan")
        except (TypeError, ValueError):
            px = float("nan")
        if pd.notna(px) and px > 0:
            if side == "BUY" and new_sl >= px:
                raise BrokerError("BUY SL must sit below the last close")
            if side == "SELL" and new_sl <= px:
                raise BrokerError("SELL SL must sit above the last close")
        old = pos.get("sl")
        try:
            old_f = float(old) if old is not None else None
        except (TypeError, ValueError):
            old_f = None
        if old_f is not None:
            if side == "BUY" and new_sl <= old_f:
                raise BrokerError("refusing to widen BUY SL (paper tighten-only)")
            if side == "SELL" and new_sl >= old_f:
                raise BrokerError("refusing to widen SELL SL (paper tighten-only)")
        pos["sl_prev"] = old
        pos["sl"] = new_sl
        pos["sl_note"] = str(meta.get("note") or "advisory tighten (user click)")[:200]
        pos["sl_modified_at"] = meta.get("timestamp") or _now_label()
        self._touch_open(pos)
        return dict(pos)

    def list_closed(self) -> list[dict[str, Any]]:
        return list(self._state.get("closed") or [])

    def open_for_pair(self, pair: str) -> dict[str, Any] | None:
        return position_for_pair(self, pair)

    def refresh_from_ohlcv(
        self,
        pair: str,
        ohlcv: pd.DataFrame | None,
        cfg: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Mark-to-market and auto-close on paper SL/TP/horizon when bars exist."""
        if ohlcv is None or ohlcv.empty:
            return []
        cfg = cfg if cfg is not None else self.cfg
        events: list[dict[str, Any]] = []
        last_close = float(ohlcv["Close"].iloc[-1])
        last_ts = str(ohlcv.index[-1])
        for pos in list(self.list_positions()):
            if str(pos.get("pair")).upper() != str(pair).upper():
                continue
            scored = score_from_ohlcv(pos, ohlcv, cfg)
            mark = float(scored.exit_px) if scored.exit_px is not None else last_close
            u = signed_return(str(pos["side"]), float(pos["entry_price"]), mark)
            u -= float(pos.get("spread_frac") or 0.0)
            pos["unrealized"] = u * float(pos["size"])
            pos["bars_held"] = scored.bars_seen
            pos["outcome"] = scored.outcome
            if scored.status == "closed" and scored.exit_px is not None and scored.reason:
                events.append(self._finalize(pos, float(scored.exit_px), scored.reason, last_ts))
            else:
                self._touch_open(pos)
        return events

    def _touch_open(self, updated: dict[str, Any]) -> None:
        rows = self._state["positions"]
        for i, p in enumerate(rows):
            if p.get("id") == updated.get("id"):
                rows[i] = updated
                break
        self._save()

    def journal(self) -> list[dict[str, Any]]:
        closed = [{**c, "outcome": normalize_outcome(c)} for c in self.list_closed()]
        open_rows = [{**p, "outcome": normalize_outcome(p)} for p in self.list_positions()]
        return list(reversed(closed)) + list(reversed(open_rows))

    def aggregates(self) -> dict[str, Any]:
        return journal_aggregates(self.list_closed(), self.list_positions())


__all__ = [
    "BrokerError",
    "BrokerPort",
    "CLOSED_OUTCOMES",
    "OrderGateway",
    "PaperBroker",
    "broker_cfg",
    "conf_bucket",
    "filter_journal",
    "improvement_notes",
    "journal_aggregates",
    "make_broker",
    "normalize_outcome",
    "position_for_pair",
    "score_from_ohlcv",
    "session_name",
    "walk_barriers",
]
