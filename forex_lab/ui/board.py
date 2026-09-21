"""Build research watch-board rows for the Streamlit UI.

No broker APIs and no invented prices. Missing data/model is reported as status.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from forex_lab.config_loader import load_config
from forex_lab.data import load_cached_ohlcv, try_yfinance_refresh
from forex_lab.features import true_range_atr
from forex_lab.signals import generate_signals
from forex_lab.ui.pipeline import artifact_status, load_signals
from forex_lab.ui.watchlist import Watchlist

NEED_FETCH_TRAIN = "need Fetch/Train"
STATUS_READY = "ready"
STATUS_NEED_FETCH = "need_fetch"
STATUS_NEED_TRAIN = "need_train"
STATUS_ERROR = "error"


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
    extra: dict[str, Any] = field(default_factory=dict)

    def as_table_dict(self) -> dict[str, str]:
        return {
            "Pair": self.pair,
            "Timeframe": self.timeframe,
            "Buy/Sell": self.buy_sell,
            "Target": self.target,
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


def _details_from_signal(last: pd.Series | dict[str, Any], *, status: str | None = None) -> str:
    if status and status != STATUS_READY:
        return status
    get = last.get if hasattr(last, "get") else lambda k, d=None: last[k] if k in last else d  # type: ignore[index]
    conf = get("confidence")
    edge = get("dir_edge")
    model = get("model") or "n/a"
    when = _fmt_when(get("datetime"))
    pb, ps, ph = get("p_buy"), get("p_sell"), get("p_hold")
    return (
        f"conf={_fmt(conf, 4)}  dir_edge={_fmt(edge, 4)}  "
        f"p_buy={_fmt(pb, 3)} p_sell={_fmt(ps, 3)} p_hold={_fmt(ph, 3)}  "
        f"{model}  {when}"
    )


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

    return BoardRow(
        pair=pair.upper(),
        timeframe=timeframe,
        buy_sell=sig,
        target=target,
        signal_details=_details_from_signal(last),
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
    )


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
) -> BoardRow:
    """One watch-board row. Does not write ``signals/latest_signals.csv``.

    ``refresh_data`` tries yfinance (never synthetic). Signals are regenerated
    when ``regenerate`` is true, or when ``refresh_data`` is true, or when no
    matching row exists in the shared signals CSV.
    """
    cfg = cfg if cfg is not None else load_config()
    pair = str(pair).upper()
    interval = str(interval or cfg.get("interval") or "1h")
    if regenerate is None:
        regenerate = bool(refresh_data)

    status = artifact_status(pair, cfg, interval=interval)
    data_source: str | None = None
    # Light yfinance refresh only when a model exists — never synthetic, never a silent fetch.
    if refresh_data and status.get("model_exists"):
        _df, reason = try_yfinance_refresh(pair, cfg, interval=interval)
        data_source = reason if _df is not None else f"cached ({reason})"
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
        )
    if not status.get("model_exists"):
        return _status_row(
            pair,
            interval,
            STATUS_NEED_TRAIN,
            NEED_FETCH_TRAIN,
            data_source=data_source or "cached",
            n_bars=n_bars,
            error="model missing",
        )

    ohlcv = load_cached_ohlcv(pair, cfg, interval)
    if ohlcv is None or ohlcv.empty:
        return _status_row(
            pair,
            interval,
            STATUS_NEED_FETCH,
            NEED_FETCH_TRAIN,
            data_source=data_source,
            error="data CSV unreadable",
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
            return _status_row(
                pair,
                interval,
                STATUS_ERROR,
                f"signal error: {exc}",
                data_source=data_source or "cached",
                n_bars=len(ohlcv),
                error=str(exc),
            )
        if sigs is None or sigs.empty:
            return _status_row(
                pair,
                interval,
                STATUS_ERROR,
                "model produced no signal rows",
                data_source=data_source or "cached",
                n_bars=len(ohlcv),
            )
        last = sigs.iloc[-1]

    return row_from_signal(
        pair,
        interval,
        last,
        ohlcv,
        cfg,
        data_source=data_source or "cached",
        n_bars=len(ohlcv),
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
    if not rows:
        return pd.DataFrame(columns=["Pair", "Timeframe", "Buy/Sell", "Target", "Signal details"])
    return pd.DataFrame([r.as_table_dict() for r in rows])


def style_board(df: pd.DataFrame):
    """Color Buy/Sell when pandas Styler is available."""
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

    try:
        styler = df.style
        if "Buy/Sell" in df.columns:
            mapper = getattr(styler, "map", None) or getattr(styler, "applymap", None)
            if mapper is not None:
                styler = mapper(_sig, subset=["Buy/Sell"])
        return styler
    except Exception:
        return df
