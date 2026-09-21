"""Build research watch-board rows for the Streamlit UI.

No broker APIs and no invented prices. Missing data/model is reported as status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import csv_mtime_utc, load_cached_ohlcv, try_yfinance_refresh
from forex_lab.explain import SignalExplanation, explain_latest_signal
from forex_lab.features import true_range_atr
from forex_lab.freshness import (
    VALIDITY_CLOSED,
    VALIDITY_ERROR,
    VALIDITY_MISSING,
    VALIDITY_OK,
    VALIDITY_STALE,
    Freshness,
    assess_ohlcv,
    fmt_ts,
    now_utc,
)
from forex_lab.signals import generate_signals
from forex_lab.ui.pipeline import artifact_status, load_signals
from forex_lab.ui.watchlist import Watchlist

NEED_FETCH_TRAIN = "need Fetch/Train"
STATUS_READY = "ready"
STATUS_NEED_FETCH = "need_fetch"
STATUS_NEED_TRAIN = "need_train"
STATUS_ERROR = "error"
STATUS_STALE = "stale"


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
    extra: dict[str, Any] = field(default_factory=dict)

    def as_table_dict(self) -> dict[str, str]:
        return {
            "Pair": self.pair,
            "Timeframe": self.timeframe,
            "Validity": self.validity,
            "Buy/Sell": self.buy_sell,
            "Target": self.target,
            "Last bar": self.last_bar_at or "n/a",
            "Signal details": self.signal_details,
        }


def _fmt(x: object, digits: int = 4) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "n/a"
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return str(x)


def _fmt_when(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return str(value)
    return ts.strftime("%Y-%m-%d %H:%M")


def research_target(ohlcv: pd.DataFrame | None, cfg: dict[str, Any], signal: str | None) -> tuple[str, str]:
    """Best-effort barrier prices from the last bar + labeling config.

    Triple-barrier labels fill at the *next* open. That fill is unknown on the
    latest closed bar, so last close is used as a proxy and labeled as such.
    """
    scheme = str(cfg.get("label_scheme") or "triple_barrier").lower()
    horizon = int(cfg.get("horizon") or 8)
    entry_timing = str(cfg.get("entry_timing") or "next_open")
    if ohlcv is None or ohlcv.empty:
        return "n/a", f"no bars; scheme={scheme} horizon={horizon}"
    if scheme != "triple_barrier":
        return (
            f"n/a (scheme={scheme}, horizon={horizon})",
            "forward_return / other schemes have no ATR barrier prices on the board",
        )

    b = cfg.get("barrier") or {}
    tp_atr = float(b.get("tp_atr", 2.0))
    sl_atr = float(b.get("sl_atr", 2.0))
    atr_p = int(cfg.get("atr_period", 14))
    atr_s = true_range_atr(ohlcv, atr_p)
    atr = float(atr_s.iloc[-1]) if len(atr_s) else float("nan")
    close = float(ohlcv["Close"].iloc[-1])
    if not pd.notna(atr) or atr <= 0 or not pd.notna(close) or close <= 0:
        return "n/a", "ATR or close unavailable on last bar"

    fill_proxy = close
    long_tp = fill_proxy + tp_atr * atr
    long_sl = fill_proxy - sl_atr * atr
    short_tp = fill_proxy - tp_atr * atr
    short_sl = fill_proxy + sl_atr * atr
    sig = str(signal or "HOLD").upper()
    if sig == "BUY":
        compact = f"TP {_fmt(long_tp, 5)} / SL {_fmt(long_sl, 5)}"
    elif sig == "SELL":
        compact = f"TP {_fmt(short_tp, 5)} / SL {_fmt(short_sl, 5)}"
    else:
        compact = f"upper {_fmt(long_tp, 5)} / lower {_fmt(long_sl, 5)}"

    note = (
        f"Research barriers only (not an order). scheme=triple_barrier "
        f"tp_atr={tp_atr} sl_atr={sl_atr} ATR={_fmt(atr, 6)} horizon={horizon} bars. "
        f"Label fill is {entry_timing}; last close {_fmt(close, 5)} used as proxy "
        "because the next open is not available yet."
    )
    return compact, note


def _details_from_signal(
    last: pd.Series | dict[str, Any],
    *,
    status: str | None = None,
    explanation: SignalExplanation | None = None,
) -> str:
    if status and status != STATUS_READY:
        return status
    get = last.get if hasattr(last, "get") else lambda k, d=None: last[k] if k in last else d  # type: ignore[index]
    conf = get("confidence")
    edge = get("dir_edge")
    model = get("model") or "n/a"
    when = _fmt_when(get("datetime"))
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

    return BoardRow(
        pair=pair.upper(),
        timeframe=timeframe,
        buy_sell=sig,
        target=target,
        signal_details=_details_from_signal(last, explanation=explanation),
        status=STATUS_READY,
        confidence=_num("confidence"),
        dir_edge=_num("dir_edge"),
        p_buy=_num("p_buy"),
        p_sell=_num("p_sell"),
        p_hold=_num("p_hold"),
        model=None if get("model") is None else str(get("model")),
        datetime=_fmt_when(get("datetime")),
        close=_num("close"),
        raw_signal=None if get("raw_signal") is None else str(get("raw_signal")),
        target_note=note,
        data_source=data_source,
        n_bars=n_bars,
        rationale=None if explanation is None else explanation.rationale,
        explain_method=None if explanation is None else explanation.method,
        drivers=list(explanation.drivers) if explanation is not None else [],
        rules=list(explanation.rules) if explanation is not None else [],
        last_signal_at=_fmt_when(get("datetime")),
    )


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
) -> BoardRow:
    """Attach validity. STALE/MISSING/ERROR never flash a live BUY/SELL."""
    row.validity = fresh.validity
    row.validity_reason = fresh.reason
    row.last_bar_at = fresh.last_bar_label
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
            f"data stale — refresh required (last model {last_model}; {fresh.reason})"
        )
        if row.status == STATUS_READY:
            row.status = STATUS_STALE
        row.target = "n/a"
    elif fresh.validity in {VALIDITY_MISSING, VALIDITY_ERROR} and row.buy_sell not in {"—"}:
        row.buy_sell = "—"
    return row


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
    fetch_at = fmt_ts(csv_mtime_utc(pair, cfg, interval))
    # Light yfinance refresh only when a model exists — never synthetic, never a silent fetch.
    if refresh_data and status.get("model_exists"):
        _df, reason = try_yfinance_refresh(
            pair, cfg, interval=interval, incremental=incremental
        )
        if _df is not None:
            data_source = reason
            fetch_at = fmt_ts(clock)
        else:
            data_source = f"cached ({reason})"
        status = artifact_status(pair, cfg, interval=interval)

    n_bars = status.get("n_bars")
    if not status.get("data_exists"):
        return _status_row(
            pair,
            interval,
            STATUS_NEED_FETCH,
            NEED_FETCH_TRAIN,
            data_source=data_source,
            n_bars=n_bars,
            error="data CSV missing",
            validity=VALIDITY_MISSING,
            validity_reason="no OHLCV cache — Fetch required",
            last_fetch_at=fetch_at if fetch_at != "n/a" else None,
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
        return apply_freshness(row, fresh_m, last_fetch_at=row.last_fetch_at)

    ohlcv = load_cached_ohlcv(pair, cfg, interval)
    if ohlcv is None or ohlcv.empty:
        return _status_row(
            pair,
            interval,
            STATUS_NEED_FETCH,
            NEED_FETCH_TRAIN,
            data_source=data_source,
            error="data CSV unreadable",
            validity=VALIDITY_MISSING,
            validity_reason="data CSV unreadable",
        )

    last: pd.Series | dict[str, Any] | None = None
    if not regenerate:
        last = _latest_csv_row(pair, cfg)

    if last is None:
        try:
            sigs = generate_signals(ohlcv, cfg, pair)
        except FileNotFoundError:
            return _status_row(
                pair,
                interval,
                STATUS_NEED_TRAIN,
                NEED_FETCH_TRAIN,
                data_source=data_source or "cached",
                n_bars=len(ohlcv),
                error="model missing",
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
            return row
        if sigs is None or sigs.empty:
            return _status_row(
                pair,
                interval,
                STATUS_ERROR,
                "model produced no signal rows",
                data_source=data_source or "cached",
                n_bars=len(ohlcv),
                validity=VALIDITY_ERROR,
                validity_reason="model produced no signal rows",
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
    return apply_freshness(
        row,
        fresh,
        last_fetch_at=fetch_at if fetch_at != "n/a" else None,
    )


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


def board_table(rows: list[BoardRow]) -> pd.DataFrame:
    cols = ["Pair", "Timeframe", "Validity", "Buy/Sell", "Target", "Last bar", "Signal details"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([r.as_table_dict() for r in rows])


def style_board(df: pd.DataFrame):
    """Color Buy/Sell and Validity when pandas Styler is available."""
    if df is None or df.empty:
        return df

    def _sig(val: object) -> str:
        v = str(val).upper()
        if v == "BUY":
            return "background-color: #c8e6c9; color: #1b5e20; font-weight: 700"
        if v == "SELL":
            return "background-color: #ffcdd2; color: #b71c1c; font-weight: 700"
        if v == "HOLD":
            return "background-color: #eceff1; color: #37474f"
        return ""

    def _val(val: object) -> str:
        v = str(val).upper()
        if v == VALIDITY_OK:
            return "background-color: #dcfce7; color: #166534; font-weight: 700"
        if v == VALIDITY_CLOSED:
            return "background-color: #e2e8f0; color: #334155; font-weight: 700"
        if v == VALIDITY_STALE:
            return "background-color: #fef3c7; color: #92400e; font-weight: 700"
        if v == VALIDITY_MISSING:
            return "background-color: #f1f5f9; color: #475569; font-weight: 700"
        if v == VALIDITY_ERROR:
            return "background-color: #fee2e2; color: #991b1b; font-weight: 700"
        return ""

    try:
        styler = df.style
        mapper = getattr(styler, "map", None) or getattr(styler, "applymap", None)
        if mapper is not None:
            if "Buy/Sell" in df.columns:
                styler = mapper(_sig, subset=["Buy/Sell"])
            if "Validity" in df.columns:
                styler = mapper(_val, subset=["Validity"])
        return styler
    except Exception:
        return df
