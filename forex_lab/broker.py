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

CLOSED_OUTCOMES = ("RIGHT", "WRONG", "TIMEOUT", "FLAT")


class BrokerError(RuntimeError):
    """User-facing broker/port error (missing price, unknown backend, …)."""


class BrokerPort(ABC):
    """Minimal order gateway. UI must not call a vendor SDK directly."""

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


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def session_name(ts: object) -> str:
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(t):
        return "n/a"
    hour = int(t.tz_convert("UTC").hour)
    if 0 <= hour < 7:
        return "asia"
    if 7 <= hour < 13:
        return "london"
    if 13 <= hour < 16:
        return "overlap"
    if 16 <= hour < 21:
        return "ny"
    return "off"


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


def _spread_frac(pair: str, cfg: dict[str, Any], price: float) -> float:
    pips = float(cfg.get("spread_pips", 1.0)) + float(cfg.get("commission_pips", 0.0))
    pip = pip_size_for_pair(pair, cfg)
    return (pips * pip) / max(price, 1e-12)


def _signed_return(side: str, entry: float, exit_px: float) -> float:
    if side == "BUY":
        return (exit_px - entry) / entry
    return (entry - exit_px) / entry


def _walk_barriers(
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
    bars = 0
    for i in range(start_loc, end):
        bars += 1
        px_up = float(high[i] if use_hl else close[i])
        px_dn = float(low[i] if use_hl else close[i])
        if side == "BUY":
            hit_tp = tp is not None and px_up >= tp
            hit_sl = sl is not None and px_dn <= sl
        else:
            hit_tp = tp is not None and px_dn <= tp
            hit_sl = sl is not None and px_up >= sl
        if hit_tp and hit_sl:
            return "closed", float(sl) if sl is not None else float(close[i]), "sl", bars
        if hit_sl:
            return "closed", float(sl) if sl is not None else float(close[i]), "sl", bars
        if hit_tp:
            return "closed", float(tp) if tp is not None else float(close[i]), "tp", bars
    if bars >= int(horizon):
        return "closed", float(close[end - 1]), "timeout", bars
    return "pending", float(close[end - 1]) if bars else None, None, bars


def _outcome(reason: str | None, side: str) -> str:
    if reason == "tp":
        return "RIGHT"
    if reason in {"sl", "sl_conflict"}:
        return "WRONG"
    if reason == "timeout":
        return "TIMEOUT"
    if reason in {"manual", "flat"}:
        return "FLAT"
    return "PENDING"


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
            "horizon": int(meta.get("horizon") or self.cfg.get("horizon") or 8),
            "spread_frac": spread,
            "unrealized": -spread * qty,
            "session": session_name(meta.get("entry_bar_time") or now),
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
        realized = (_signed_return(str(pos["side"]), entry, exit_px) - spread) * qty
        outcome = _outcome(reason, str(pos["side"]))
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

    def list_closed(self) -> list[dict[str, Any]]:
        return list(self._state.get("closed") or [])

    def open_for_pair(self, pair: str) -> dict[str, Any] | None:
        key = str(pair).upper()
        for p in self.list_positions():
            if str(p.get("pair")).upper() == key:
                return p
        return None

    def refresh_from_ohlcv(
        self,
        pair: str,
        ohlcv: pd.DataFrame | None,
        cfg: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Mark-to-market and auto-close on paper SL/TP/timeout when bars exist."""
        if ohlcv is None or ohlcv.empty:
            return []
        cfg = cfg if cfg is not None else self.cfg
        path = str((cfg.get("barrier") or {}).get("path") or "high_low")
        events: list[dict[str, Any]] = []
        last_close = float(ohlcv["Close"].iloc[-1])
        last_ts = str(ohlcv.index[-1])
        for pos in list(self.list_positions()):
            if str(pos.get("pair")).upper() != str(pair).upper():
                continue
            entry_bar = pd.to_datetime(pos.get("entry_bar_time"), utc=True, errors="coerce")
            if pd.isna(entry_bar):
                u = _signed_return(str(pos["side"]), float(pos["entry_price"]), last_close)
                u -= float(pos.get("spread_frac") or 0.0)
                pos["unrealized"] = u * float(pos["size"])
                self._touch_open(pos)
                continue
            entry_naive = entry_bar.tz_convert("UTC").tz_localize(None)
            idx = pd.to_datetime(ohlcv.index, utc=True).tz_convert("UTC").tz_localize(None)
            later_idx = [i for i, ts in enumerate(idx) if ts > entry_naive]
            if not later_idx:
                u = _signed_return(str(pos["side"]), float(pos["entry_price"]), last_close)
                u -= float(pos.get("spread_frac") or 0.0)
                pos["unrealized"] = u * float(pos["size"])
                self._touch_open(pos)
                continue
            start_loc = later_idx[0]
            status, exit_px, reason, _bars = _walk_barriers(
                ohlcv,
                start_loc=start_loc,
                horizon=int(pos.get("horizon") or cfg.get("horizon") or 8),
                side=str(pos["side"]),
                entry=float(pos["entry_price"]),
                sl=pos.get("sl"),
                tp=pos.get("tp"),
                path=path,
            )
            if status == "closed" and exit_px is not None and reason:
                events.append(self._finalize(pos, float(exit_px), reason, last_ts))
            else:
                mark = float(exit_px) if exit_px is not None else last_close
                u = _signed_return(str(pos["side"]), float(pos["entry_price"]), mark)
                u -= float(pos.get("spread_frac") or 0.0)
                pos["unrealized"] = u * float(pos["size"])
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
        open_rows = [{**p, "outcome": p.get("outcome") or "PENDING"} for p in self.list_positions()]
        return list(reversed(self.list_closed())) + list(reversed(open_rows))

    def aggregates(self) -> dict[str, Any]:
        return journal_aggregates(self.list_closed(), self.list_positions())


def journal_aggregates(
    closed: list[dict[str, Any]],
    open_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    closed = list(closed or [])
    pending_n = len(open_rows or [])
    right = [r for r in closed if r.get("outcome") == "RIGHT"]
    wrong = [r for r in closed if r.get("outcome") == "WRONG"]
    timeout = [r for r in closed if r.get("outcome") == "TIMEOUT"]
    flat = [r for r in closed if r.get("outcome") == "FLAT"]
    scored = len(right) + len(wrong)
    err = (len(wrong) / scored) if scored else None

    def _rate(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
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
                    "wrong": w,
                    "right": ok,
                    "error_rate": None if n == 0 else w / n,
                }
            )
        return out

    scored_rows = [r for r in closed if r.get("outcome") in {"RIGHT", "WRONG"}]
    return {
        "n_closed": len(closed),
        "n_pending": pending_n,
        "right": len(right),
        "wrong": len(wrong),
        "timeout": len(timeout),
        "flat": len(flat),
        "error_rate": err,
        "by_pair": _rate(scored_rows, "pair"),
        "by_session": _rate(scored_rows, "session"),
        "by_validity": _rate(scored_rows, "validity_at_entry"),
        "by_confidence": _rate(scored_rows, "conf_bucket"),
        "notes": improvement_notes(closed, err, scored_rows),
    }


def improvement_notes(
    closed: list[dict[str, Any]],
    error_rate: float | None,
    scored_rows: list[dict[str, Any]],
) -> list[str]:
    notes = [
        "Paper only — not linked to any broker. Use this desk to practice the same "
        "decisions you would make live, then inspect mistakes.",
        "If the system ever grows a live backend, it should implement BrokerPort the "
        "same way PaperBroker does (submit / close / list_positions / list_fills) "
        "behind broker.backend — do not call a vendor SDK from the UI.",
    ]
    if not scored_rows:
        notes.append(
            "No scored (RIGHT/WRONG) paper trades yet. PENDING until enough bars pass "
            "to hit TP, SL, or the label horizon."
        )
        return notes
    stale = [r for r in scored_rows if str(r.get("validity_at_entry")).upper() == "STALE"]
    ok = [r for r in scored_rows if str(r.get("validity_at_entry")).upper() == "OK"]

    def _err(rows: list[dict[str, Any]]) -> float | None:
        n = len(rows)
        if not n:
            return None
        return sum(1 for r in rows if r.get("outcome") == "WRONG") / n

    se, oe = _err(stale), _err(ok)
    if se is not None and oe is not None and se > oe:
        notes.append(
            "Avoid entries when validity is STALE — that bucket was wrong more often than OK."
        )
    elif stale:
        notes.append("Prefer OK validity; STALE flashes are not live calls on the board.")
    low = [r for r in scored_rows if r.get("conf_bucket") == "<0.40"]
    high = [r for r in scored_rows if r.get("conf_bucket") == ">=0.60"]
    le, he = _err(low), _err(high)
    if le is not None and he is not None and le > he:
        notes.append("Raise signals.min_confidence — the low-confidence bucket was wrong more often.")
    if error_rate is not None and error_rate >= 0.5:
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
