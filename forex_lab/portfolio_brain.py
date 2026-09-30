"""Portfolio brain — post-signal risk overlay (desk / paper).

Design: `_PORTFOLIO_BRAIN_DESIGN_20261001.txt`.

This is **not** a second alpha. It never rewrites P(class) or the Buy/Sell
model flash score. When `portfolio_brain.enabled` is false (default), every
check is a pure no-op: allow + scale=1.0 + empty notes (bit-identical path).

When enabled, it may:
- block a new open (max concurrent / corr cluster / DD budget / book units),
- scale default_size down,
- emit desk notes / blocked_by tags.

Manual CLOSE always bypasses the brain. Replay stays off unless
`apply_to_replay: true` (default false). Fail-soft on missing peer CSV.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

NOTE_MAX_CONCURRENT = "portfolio:max_concurrent"
NOTE_CORR_CLUSTER = "portfolio:corr_cluster"
NOTE_DD_BUDGET = "portfolio:dd_budget"
NOTE_BOOK_UNITS = "portfolio:book_units"
NOTE_FAIL_SOFT = "portfolio:fail_soft"

# Explicit USD-risk map (v0). Long = +1 long-USD, -1 short-USD. Invert for SELL.
# Unknown pairs skip correlation veto (fail-soft allow).
_USD_RISK_LONG: dict[str, int] = {
    "EURUSD": -1,
    "GBPUSD": -1,
    "AUDUSD": -1,
    "NZDUSD": -1,
    "USDJPY": +1,
    "USDCAD": +1,
    "USDCHF": +1,
}

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "fail_soft": True,
    "max_concurrent_opens": 2,
    "corr_bars": 120,
    "corr_threshold": 0.70,
    "corr_skip_pairs": ["BTCUSD"],
    "dd_budget_pct": -0.05,
    "dd_scale": False,
    "max_book_units": 2.0,
    "min_size": 0.25,
    "apply_to_paper": True,
    "apply_to_flash": True,
    "apply_to_replay": False,
}


@dataclass
class PortfolioDecision:
    """Overlay verdict. Model score / raw flash are never mutated here."""

    allowed: bool = True
    scale: float = 1.0
    blocked_by: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    open_count: int = 0
    book_dd: float | None = None
    clusters: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "scale": float(self.scale),
            "blocked_by": list(self.blocked_by),
            "notes": list(self.notes),
            "open_count": int(self.open_count),
            "book_dd": self.book_dd,
            "clusters": list(self.clusters),
        }


def portfolio_brain_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("portfolio_brain") or {})
    out = dict(DEFAULTS)
    out.update({k: v for k, v in raw.items() if v is not None})
    return out


def portfolio_brain_enabled(cfg: dict[str, Any] | None) -> bool:
    return bool(portfolio_brain_cfg(cfg).get("enabled"))


def portfolio_brain_fail_soft(cfg: dict[str, Any] | None) -> bool:
    block = portfolio_brain_cfg(cfg)
    if "fail_soft" in block:
        return bool(block.get("fail_soft"))
    return True


def apply_to_paper(cfg: dict[str, Any] | None) -> bool:
    block = portfolio_brain_cfg(cfg)
    if "apply_to_paper" in block:
        return bool(block.get("apply_to_paper"))
    return True


def apply_to_flash(cfg: dict[str, Any] | None) -> bool:
    block = portfolio_brain_cfg(cfg)
    if "apply_to_flash" in block:
        return bool(block.get("apply_to_flash"))
    return True


def apply_to_replay(cfg: dict[str, Any] | None) -> bool:
    return bool(portfolio_brain_cfg(cfg).get("apply_to_replay"))


def normalize_pair(pair: object) -> str:
    p = str(pair or "").upper().replace("/", "").replace("=", "")
    if p.endswith("X") and len(p) == 7 and p[:6].isalpha():
        p = p[:-1]
    return p


def usd_risk_direction(pair: object, side: object) -> int | None:
    """Return +1 long-USD, -1 short-USD, or None if pair unknown / side bad.

    Long EURUSD/GBPUSD/AUDUSD/NZDUSD = short USD (-1).
    Long USDJPY/USDCAD/USDCHF = long USD (+1).
    SELL inverts the long map.
    """
    p = normalize_pair(pair)
    base = _USD_RISK_LONG.get(p)
    if base is None:
        return None
    s = str(side or "").upper().strip()
    if s == "BUY":
        return int(base)
    if s == "SELL":
        return int(-base)
    return None


def _open_rows(opens: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in opens or []:
        if not isinstance(raw, Mapping):
            continue
        status = str(raw.get("status") or "open").lower()
        if status and status not in {"open", "opened", "active"}:
            continue
        pair = normalize_pair(raw.get("pair") or raw.get("symbol"))
        side = str(raw.get("side") or "").upper()
        if not pair or side not in {"BUY", "SELL"}:
            continue
        try:
            size = float(
                raw.get("size") if raw.get("size") is not None else raw.get("qty") or 0.0
            )
        except (TypeError, ValueError):
            size = 0.0
        rows.append(
            {
                "pair": pair,
                "side": side,
                "size": size,
                "unrealized": _as_float(raw.get("unrealized_pnl", raw.get("unrealized"))),
                "realized": _as_float(raw.get("realized_pnl", raw.get("pnl"))),
            }
        )
    return rows


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def log_returns(closes: pd.Series, *, bars: int) -> pd.Series:
    """Causal log-returns on the last `bars` *completed* closes (drop incomplete tip)."""
    s = pd.to_numeric(closes, errors="coerce").dropna()
    if len(s) < 3:
        return pd.Series(dtype=float)
    # Drop last bar (in-progress / incomplete) when enough history exists.
    s = s.iloc[:-1]
    rets = np.log(s / s.shift(1)).dropna()
    if bars > 0 and len(rets) > bars:
        rets = rets.iloc[-bars:]
    return rets


def rolling_return_corr(
    a_closes: pd.Series | None,
    b_closes: pd.Series | None,
    *,
    bars: int = 120,
) -> float | None:
    """Pearson corr of aligned causal log-returns. None if insufficient overlap."""
    if a_closes is None or b_closes is None:
        return None
    try:
        ra = log_returns(a_closes, bars=bars)
        rb = log_returns(b_closes, bars=bars)
    except Exception:
        return None
    if ra.empty or rb.empty:
        return None
    joined = pd.concat([ra.rename("a"), rb.rename("b")], axis=1, join="inner").dropna()
    if len(joined) < max(20, bars // 4):
        return None
    if bars > 0 and len(joined) > bars:
        joined = joined.iloc[-bars:]
    if float(joined["a"].std()) == 0.0 or float(joined["b"].std()) == 0.0:
        return None
    try:
        c = float(joined["a"].corr(joined["b"]))
    except Exception:
        return None
    if c != c:  # NaN
        return None
    return c


def book_drawdown_pct(
    opens: Sequence[Mapping[str, Any]] | None,
    *,
    closed: Sequence[Mapping[str, Any]] | None = None,
    session_start_equity: float = 1.0,
) -> float | None:
    """Book DD vs session start using realized + unrealized PnL.

    Equity = session_start + sum(closed pnl) + sum(open unrealized).
    Returns current drawdown as a negative fraction (e.g. -0.03), or 0.0 if flat/up.
    None when no usable PnL marks (caller fail-softs).
    """
    start = float(session_start_equity) if session_start_equity else 1.0
    if start <= 0:
        start = 1.0
    realized = 0.0
    have_mark = False
    for row in closed or []:
        if not isinstance(row, Mapping):
            continue
        pnl = _as_float(row.get("pnl") if row.get("pnl") is not None else row.get("realized_pnl"))
        if pnl is None:
            continue
        realized += pnl
        have_mark = True
    unrealized = 0.0
    for row in _open_rows(opens):
        u = row.get("unrealized")
        if u is None:
            continue
        unrealized += float(u)
        have_mark = True
    if not have_mark and not (opens or closed):
        return 0.0
    if not have_mark:
        return None
    equity = start + realized + unrealized
    peak = max(start, equity)
    if peak <= 0:
        return None
    dd = equity / peak - 1.0
    return float(dd) if dd < 0 else 0.0


def check_open_intent(
    *,
    pair: object,
    side: object,
    size: float | None = None,
    opens: Sequence[Mapping[str, Any]] | None = None,
    closed: Sequence[Mapping[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
    peer_closes: Mapping[str, pd.Series] | None = None,
    session_start_equity: float = 1.0,
    proposed_size: float | None = None,
) -> PortfolioDecision:
    """Post-signal overlay check. No-op when disabled."""
    block = portfolio_brain_cfg(cfg)
    open_rows = _open_rows(opens)
    decision = PortfolioDecision(open_count=len(open_rows))

    if not bool(block.get("enabled")):
        # Bit-identical noop: allow, scale 1, no notes / blocks.
        return decision

    if not apply_to_paper(cfg) and not apply_to_flash(cfg):
        decision.notes.append("portfolio_brain enabled but apply_to_paper/flash both false")
        return decision

    symbol = normalize_pair(pair)
    action = str(side or "").upper().strip()
    if action not in {"BUY", "SELL"}:
        decision.notes.append("portfolio_brain: ignored non BUY/SELL intent")
        return decision

    qty = proposed_size if proposed_size is not None else size
    if qty is None:
        qty = float(((cfg or {}).get("broker") or {}).get("default_size") or 1.0)
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        qty = 1.0

    fail_soft = bool(block.get("fail_soft", True))

    # --- 1) Max concurrent opens ---
    try:
        max_conc = int(block.get("max_concurrent_opens", 2) or 2)
    except (TypeError, ValueError):
        max_conc = 2
    if max_conc < 0:
        max_conc = 0
    if len(open_rows) >= max_conc:
        decision.allowed = False
        decision.blocked_by.append(NOTE_MAX_CONCURRENT)
        decision.notes.append(
            f"{NOTE_MAX_CONCURRENT}: open_count={len(open_rows)} >= max={max_conc}"
        )

    # --- 2) Correlation veto (same USD-risk direction) ---
    try:
        corr_bars = int(block.get("corr_bars", 120) or 120)
    except (TypeError, ValueError):
        corr_bars = 120
    try:
        corr_thr = float(block.get("corr_threshold", 0.70) or 0.70)
    except (TypeError, ValueError):
        corr_thr = 0.70
    skip_raw = block.get("corr_skip_pairs") or ["BTCUSD"]
    if isinstance(skip_raw, str):
        skip_pairs = {normalize_pair(skip_raw)}
    else:
        skip_pairs = {normalize_pair(x) for x in skip_raw}

    intent_dir = usd_risk_direction(symbol, action)
    active_closes = None
    if peer_closes and symbol in peer_closes:
        active_closes = peer_closes.get(symbol)

    if intent_dir is not None and symbol not in skip_pairs:
        for row in open_rows:
            peer = row["pair"]
            if peer == symbol or peer in skip_pairs:
                continue
            peer_dir = usd_risk_direction(peer, row["side"])
            if peer_dir is None or peer_dir != intent_dir:
                continue  # opposite / unknown USD risk -> no corr block
            peer_series = None
            if peer_closes is not None:
                peer_series = peer_closes.get(peer)
            if peer_series is None or active_closes is None:
                if fail_soft:
                    decision.notes.append(
                        f"{NOTE_FAIL_SOFT}: missing peer closes for corr {symbol}/{peer}"
                    )
                continue
            corr = rolling_return_corr(active_closes, peer_series, bars=corr_bars)
            if corr is None:
                if fail_soft:
                    decision.notes.append(
                        f"{NOTE_FAIL_SOFT}: corr unavailable {symbol}/{peer}"
                    )
                continue
            cluster = {
                "pair": peer,
                "corr": round(float(corr), 4),
                "peer_side": row["side"],
                "usd_risk": intent_dir,
            }
            decision.clusters.append(cluster)
            if abs(corr) >= corr_thr:
                decision.allowed = False
                if NOTE_CORR_CLUSTER not in decision.blocked_by:
                    decision.blocked_by.append(NOTE_CORR_CLUSTER)
                decision.notes.append(
                    f"{NOTE_CORR_CLUSTER}: |corr({symbol},{peer})|={abs(corr):.3f} "
                    f">= {corr_thr} same usd_risk={intent_dir}"
                )

    # --- 3) Drawdown budget ---
    try:
        dd_budget = float(block.get("dd_budget_pct", -0.05))
    except (TypeError, ValueError):
        dd_budget = -0.05
    dd_scale = bool(block.get("dd_scale", False))
    book_dd = book_drawdown_pct(
        open_rows, closed=closed, session_start_equity=session_start_equity
    )
    decision.book_dd = book_dd
    if book_dd is None:
        if fail_soft:
            decision.notes.append(f"{NOTE_FAIL_SOFT}: book DD unavailable")
    else:
        if book_dd <= dd_budget:
            decision.allowed = False
            decision.blocked_by.append(NOTE_DD_BUDGET)
            decision.notes.append(
                f"{NOTE_DD_BUDGET}: book_dd={book_dd:.4f} <= budget={dd_budget:.4f}"
            )
        elif dd_scale and dd_budget < 0 and book_dd < 0:
            ratio = min(1.0, max(0.0, 1.0 - (book_dd / dd_budget)))
            decision.scale = min(decision.scale, ratio)
            decision.notes.append(
                f"portfolio:dd_scale scale={decision.scale:.3f} (dd={book_dd:.4f})"
            )

    # --- 4) Book units / size ---
    try:
        max_units = float(block.get("max_book_units", 2.0) or 2.0)
    except (TypeError, ValueError):
        max_units = 2.0
    try:
        min_size = float(block.get("min_size", 0.25) or 0.25)
    except (TypeError, ValueError):
        min_size = 0.25
    used = sum(abs(float(r["size"])) for r in open_rows)
    remaining = max_units - used
    if remaining < min_size:
        decision.allowed = False
        decision.blocked_by.append(NOTE_BOOK_UNITS)
        decision.notes.append(
            f"{NOTE_BOOK_UNITS}: remaining={remaining:.3f} < min_size={min_size}"
        )
    else:
        sized = min(qty, remaining)
        if qty > 0 and sized < qty:
            decision.scale = min(decision.scale, sized / qty)
            decision.notes.append(
                f"portfolio:size_scale scale={decision.scale:.3f} "
                f"(remaining_units={remaining:.3f})"
            )

    return decision


def overlay_flash(flash: object, decision: PortfolioDecision, cfg: dict[str, Any] | None) -> str:
    """Force HOLD when brain blocks and apply_to_flash. Never changes model P(class)."""
    raw = str(flash or "").upper().strip()
    if not portfolio_brain_enabled(cfg) or not apply_to_flash(cfg):
        return raw
    if decision.allowed:
        return raw
    if raw in {"BUY", "SELL"}:
        return "HOLD"
    return raw


def status_payload(
    *,
    cfg: dict[str, Any] | None = None,
    opens: Sequence[Mapping[str, Any]] | None = None,
    closed: Sequence[Mapping[str, Any]] | None = None,
    last_decision: PortfolioDecision | None = None,
    session_start_equity: float = 1.0,
) -> dict[str, Any]:
    """Desk / GET /portfolio/brain surface."""
    block = portfolio_brain_cfg(cfg)
    open_rows = _open_rows(opens)
    dd = book_drawdown_pct(
        open_rows, closed=closed, session_start_equity=session_start_equity
    )
    last_block = None
    clusters: list[dict[str, Any]] = []
    if last_decision is not None:
        clusters = list(last_decision.clusters)
        if last_decision.blocked_by:
            last_block = {
                "blocked_by": list(last_decision.blocked_by),
                "notes": list(last_decision.notes),
            }
    return {
        "enabled": bool(block.get("enabled")),
        "fail_soft": bool(block.get("fail_soft", True)),
        "open_count": len(open_rows),
        "max_concurrent_opens": int(block.get("max_concurrent_opens", 2) or 2),
        "book_dd": dd,
        "dd_budget_pct": float(block.get("dd_budget_pct", -0.05)),
        "corr_threshold": float(block.get("corr_threshold", 0.70) or 0.70),
        "clusters": clusters,
        "last_block": last_block,
        "tooltip": (
            "Portfolio brain does not change Buy/Sell model — risk overlay only."
        ),
        "apply_to_paper": bool(block.get("apply_to_paper", True)),
        "apply_to_flash": bool(block.get("apply_to_flash", True)),
        "apply_to_replay": bool(block.get("apply_to_replay", False)),
    }


def peer_close_map_from_cfg(
    pairs: Iterable[str],
    cfg: dict[str, Any] | None,
    *,
    interval: str | None = None,
) -> dict[str, pd.Series]:
    """Load Close series for corr peers. Missing CSV => omitted (fail-soft upstream)."""
    out: dict[str, pd.Series] = {}
    try:
        from forex_lab.data import load_cached_ohlcv
    except Exception:
        return out
    iv = interval or str((cfg or {}).get("interval") or "1h")
    for raw in pairs:
        p = normalize_pair(raw)
        if not p:
            continue
        try:
            frame = load_cached_ohlcv(p, cfg or {}, iv)
        except Exception:
            continue
        if frame is None or frame.empty or "Close" not in frame.columns:
            continue
        out[p] = frame["Close"]
    return out
