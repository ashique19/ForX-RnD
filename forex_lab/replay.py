"""Walk-forward replay. Bots only see bars at or before ReplayClock.

The clock starts on the cached history (default 2015). A model is fit only on
labels whose barrier scan has already finished by the clock. The decision at
bar ``t`` uses features at ``t``. The paper fill is the next bar's bid/ask (or
the next open plus configured spread). Later bars, and only those bars, close
the trade.

Champion and challenger are separate ``PaperBroker`` books on the same clock.
They do not read or write the live desk journal (``broker.store``). Promotion
uses ``forex_lab.retrain.promotion_decision`` — the same profit-factor / return
/ drawdown gate as the weekly retrain. It does not replace the saved champion.

Calendar/news are not a point-in-time history back to 2015. See
``CALENDAR_ASOF_GAP``. Bar replay does not invent prices.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pandas as pd

from forex_lab.broker import PaperBroker
from forex_lab.clock import fmt_display, to_display
from forex_lab.config_loader import pip_size_for_pair
from forex_lab.features import LABEL_MAP, build_features, build_labels, true_range_atr
from forex_lab.history import ProgressFn, replay_cfg, replay_store_dir
from forex_lab.retrain import promotion_decision

CALENDAR_ASOF_GAP = (
    "Calendar and news are not point-in-time in this replay. The desk calendar is a "
    "current-week Forex Factory dump, not a history back to the replay start, so the "
    "live event-window gate is not applied. Bar features, labels, and paper fills stay "
    "causal. No live broker orders."
)
TRADES_PER_HOUR_NOTE = (
    "trades/hour is reported only. Live promotion does not gate on it. "
    "Replay uses the retrain gate (profit factor, total return, max drawdown, min_trades) "
    "and the same min_confidence filter as signal generation."
)

SignalFn = Callable[[str, pd.Timestamp, pd.DataFrame], str]


class ReplayError(RuntimeError):
    """Replay cannot run (not enough history, train failure, bad clock move)."""

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


class LookaheadError(ReplayError):
    """A decision tried to read a bar after the clock."""


class ReplayClock:
    """As-of clock over a bar frame. ``visible()`` never includes t+1."""

    def __init__(self, frame: pd.DataFrame, as_of: object | None = None) -> None:
        if frame is None or frame.empty:
            raise ReplayError("replay frame is empty", reason="insufficient_bars")
        ordered = frame.sort_index()
        ordered = ordered[~ordered.index.duplicated(keep="last")]
        self.frame = ordered
        self.as_of = pd.Timestamp(self.frame.index[0] if as_of is None else as_of)

    def visible(self) -> pd.DataFrame:
        """Bars with timestamps <= as-of. Read-only contract — do not mutate."""
        view = self.frame.loc[self.frame.index <= self.as_of]
        if len(view) and pd.Timestamp(view.index.max()) > self.as_of:
            raise LookaheadError("visible slice passed the clock")
        return view

    def bar(self, ts: object) -> pd.Series:
        stamp = pd.Timestamp(ts)
        if stamp > self.as_of:
            raise LookaheadError(f"decision at {self.as_of} cannot see {stamp}")
        try:
            row = self.frame.loc[stamp]
        except KeyError as exc:
            raise ReplayError(f"no bar at {stamp}") from exc
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        return row

    def seek(self, ts: object) -> None:
        stamp = pd.Timestamp(ts)
        if stamp < self.as_of:
            raise ReplayError("ReplayClock only moves forward")
        if stamp > pd.Timestamp(self.frame.index.max()):
            raise ReplayError("clock is past the last bar")
        self.as_of = stamp


def label_ready_at(ts: object, bar_index: pd.DatetimeIndex, horizon: int) -> pd.Timestamp | None:
    """Timestamp of the last bar a label at ``ts`` is allowed to scan.

    Triple-barrier and forward-return labels both finish ``horizon`` bars after
    the decision (next-open scan ends at ``t + horizon``). Until the clock
    reaches that bar, the label is withheld.
    """
    stamp = pd.Timestamp(ts)
    loc = bar_index.get_loc(stamp)
    if isinstance(loc, slice):
        loc = int(loc.start)
    elif isinstance(loc, np.ndarray):
        hits = np.flatnonzero(loc) if loc.dtype == bool else loc
        if len(hits) == 0:
            return None
        loc = int(hits[0])
    else:
        loc = int(loc)
    ready_loc = loc + int(horizon)
    if ready_loc < 0 or ready_loc >= len(bar_index):
        return None
    return pd.Timestamp(bar_index[ready_loc])


def trainable_mask(
    index: pd.DatetimeIndex,
    bar_index: pd.DatetimeIndex,
    as_of: object,
    horizon: int,
) -> pd.Series:
    """True where the row's label is fully known at ``as_of`` (and the row is not in the future)."""
    bar_index = pd.DatetimeIndex(bar_index)
    idx = pd.DatetimeIndex(index)
    as_stamp = pd.Timestamp(as_of)
    locs = bar_index.get_indexer(idx)
    ready_locs = locs + int(horizon)
    ok = (locs >= 0) & (ready_locs < len(bar_index))
    ready_time = np.full(len(idx), np.datetime64("NaT"), dtype="datetime64[ns]")
    good = np.flatnonzero(ok)
    if len(good):
        ready_time[good] = bar_index.values[ready_locs[good]]
    as64 = np.datetime64(as_stamp.to_datetime64())
    flags = ok & (ready_time <= as64) & (idx.values <= as64)
    return pd.Series(flags, index=idx)


def has_bid_ask(df: pd.DataFrame) -> bool:
    needed = {"BidOpen", "AskOpen", "BidHigh", "BidLow", "AskHigh", "AskLow", "BidClose", "AskClose"}
    return needed <= set(df.columns) and bool(df["BidClose"].notna().any())


def entry_price(row: pd.Series, side: str, *, bid_ask: bool, slip: float) -> float:
    """Executable fill at the bar open. Slippage is adverse. No invented mid-spread when bid/ask exist."""
    side_u = str(side).upper()
    slip = abs(float(slip))
    if bid_ask:
        if side_u == "BUY":
            return float(row["AskOpen"]) + slip
        return float(row["BidOpen"]) - slip
    return float(row["Open"])


def touch_exit(
    side: str,
    sl: float,
    tp: float,
    row: pd.Series,
    *,
    bid_ask: bool,
    bars_seen: int,
    horizon: int,
) -> tuple[float, str] | None:
    """First barrier touch on this bar, else timeout on the horizon bar.

    Longs exit on the bid (the price they can sell). Shorts exit on the ask.
    Same-bar SL and TP is a stop, matching the paper journal.
    """
    side_u = str(side).upper()
    if bid_ask and side_u == "BUY":
        hi, lo, timeout_px = float(row["BidHigh"]), float(row["BidLow"]), float(row["BidClose"])
    elif bid_ask:
        hi, lo, timeout_px = float(row["AskHigh"]), float(row["AskLow"]), float(row["AskClose"])
    else:
        hi, lo, timeout_px = float(row["High"]), float(row["Low"]), float(row["Close"])
    if side_u == "BUY":
        hit_sl = lo <= sl
        hit_tp = hi >= tp
    else:
        hit_sl = hi >= sl
        hit_tp = lo <= tp
    if hit_sl:
        return float(sl), "sl"
    if hit_tp:
        return float(tp), "tp"
    if bars_seen >= int(horizon):
        return float(timeout_px), "timeout"
    return None


@dataclass
class _Pending:
    side: str
    atr: float
    decision_ts: pd.Timestamp
    confidence: float | None


@dataclass
class _Open:
    broker_id: str
    side: str
    sl: float
    tp: float
    bars_seen: int


def _utc_label(ts: object) -> str:
    stamp = pd.Timestamp(ts)
    return stamp.strftime("%Y-%m-%d %H:%M:%S UTC")


def _dhaka(ts: object, cfg: dict[str, Any]) -> str:
    return fmt_display(ts, cfg, seconds=False)


def _pip(pair: str, cfg: dict[str, Any]) -> float:
    return float(pip_size_for_pair(pair, cfg))


def _make_port(name: str, job_dir: Path, cfg: dict[str, Any], *, bid_ask: bool, slippage_pips: float) -> PaperBroker:
    """Separate paper book. Never the live ``broker.store`` path."""
    store = job_dir / f"paper_{name}.json"
    book_cfg = dict(cfg)
    broker = dict(book_cfg.get("broker") or {})
    broker["backend"] = "paper"
    broker["store"] = str(store)
    book_cfg["broker"] = broker
    if bid_ask:
        # Spread is the bid/ask distance. Do not charge spread_pips again.
        book_cfg["spread_pips"] = 0.0
        book_cfg["commission_pips"] = 0.0
    else:
        book_cfg["spread_pips"] = float(cfg.get("spread_pips") or 0.0) + float(slippage_pips)
        book_cfg["commission_pips"] = float(cfg.get("commission_pips") or 0.0)
    size = float(broker.get("default_size") or 1.0)
    return PaperBroker(store, default_size=size, cfg=book_cfg)


def _metrics(closed: list[dict[str, Any]], *, hours: float) -> dict[str, Any]:
    rets = [float(row.get("realized") or 0.0) for row in closed]
    n = len(rets)
    hours = max(float(hours), 1e-9)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": None,
            "expectancy": None,
            "avg_return_per_trade": None,
            "net_pnl": 0.0,
            "total_return": 0.0,
            "max_drawdown": None,
            "profit_factor": None,
            "trades_per_hour": 0.0,
        }
    arr = np.asarray(rets, dtype=float)
    wins = arr > 0
    equity = np.cumprod(1.0 + arr)
    peak = np.maximum.accumulate(equity)
    dd = float((equity / peak - 1.0).min())
    gains = float(arr[wins].sum()) if wins.any() else 0.0
    losses = float(-arr[~wins & (arr < 0)].sum()) if np.any(arr < 0) else 0.0
    if losses > 0:
        pf: float | None = gains / losses
    elif gains > 0:
        pf = float("inf")
    else:
        pf = None
    return {
        "n_trades": int(n),
        "win_rate": float(wins.mean()),
        "expectancy": float(arr.mean()),
        "avg_return_per_trade": float(arr.mean()),
        "net_pnl": float(arr.sum()),
        "total_return": float(equity[-1] - 1.0),
        "max_drawdown": dd,
        "profit_factor": pf,
        "trades_per_hour": float(n / hours),
    }


def _equity_frame(closed: list[dict[str, Any]]) -> pd.DataFrame:
    if not closed:
        return pd.DataFrame(columns=["exit_time", "equity", "drawdown", "net_return"])
    rows = sorted(closed, key=lambda r: str(r.get("exit_time") or ""))
    rets = np.asarray([float(r.get("realized") or 0.0) for r in rows], dtype=float)
    equity = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return pd.DataFrame(
        {
            "exit_time": [r.get("exit_time") for r in rows],
            "equity": equity,
            "drawdown": dd,
            "net_return": rets,
        }
    )


def _write_equity_chart(path: Path, curves: dict[str, pd.DataFrame], cfg: dict[str, Any], pair: str) -> None:
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 1, figsize=(8.2, 5.2), sharex=True)
    colors = {"champion": "#175cd3", "challenger": "#b54708", "sma": "#667085"}
    any_line = False
    for name, frame in curves.items():
        if frame is None or frame.empty:
            continue
        any_line = True
        xs = []
        for raw in frame["exit_time"]:
            local = to_display(raw, cfg)
            xs.append(local.replace(tzinfo=None) if local is not None else None)
        axes[0].plot(xs, frame["equity"], label=name, color=colors.get(name, "#344054"), linewidth=1.4)
        axes[1].plot(xs, frame["drawdown"], label=name, color=colors.get(name, "#344054"), linewidth=1.2)
    axes[0].set_ylabel("Equity (start 1)")
    axes[1].set_ylabel("Drawdown")
    axes[1].set_xlabel("Exit time (Asia/Dhaka)")
    axes[0].set_title(f"{pair} replay — net of costs")
    if any_line:
        axes[0].legend(loc="best", fontsize=8)
    else:
        axes[0].text(0.5, 0.5, "No closed trades", ha="center", va="center", transform=axes[0].transAxes)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _xlsx_cell(value: object) -> str:
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        text = "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)
    else:
        text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def write_xlsx(path: Path, sheets: dict[str, list[list[object]]]) -> None:
    """Minimal xlsx (one inline-string sheet each). Enough for the scoreboard."""

    def sheet_xml(rows: list[list[object]]) -> str:
        body = ["<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
                "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\"><sheetData>"]
        for r_i, row in enumerate(rows, start=1):
            body.append(f"<row r=\"{r_i}\">")
            for c_i, value in enumerate(row):
                col = _col_name(c_i)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value is not None and np.isfinite(value):
                    body.append(f"<c r=\"{col}{r_i}\"><v>{value}</v></c>")
                else:
                    body.append(f"<c r=\"{col}{r_i}\" t=\"inlineStr\"><is><t>{_xlsx_cell(value)}</t></is></c>")
            body.append("</row>")
        body.append("</sheetData></worksheet>")
        return "".join(body)

    names = list(sheets)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(names) + 1)
        )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f"{overrides}</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        sheet_tags = []
        rels = []
        for i, name in enumerate(names, start=1):
            sheet_tags.append(f'<sheet name="{_xlsx_cell(name)}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(
                f'<Relationship Id="rId{i}" '
                f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{i}.xml"/>'
            )
            zf.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(sheets[name]))
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(sheet_tags)}</sheets></workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(rels)}</Relationships>",
        )


def _col_name(index: int) -> str:
    n = index + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _fmt_num(value: object, digits: int = 6) -> str:
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if np.isnan(num):
        return ""
    if np.isinf(num):
        return "inf"
    return f"{num:.{digits}f}"


def run_replay(
    df: pd.DataFrame,
    cfg: dict[str, Any],
    pair: str,
    *,
    interval: str = "1h",
    job_dir: Path | None = None,
    source: str = "cache",
    progress: ProgressFn | None = None,
    signal_at: SignalFn | None = None,
    collect_decisions: bool = False,
) -> dict[str, Any]:
    """Replay ``df`` forward. Writes the scoreboard under ``job_dir``.

    ``signal_at`` is a test seam. Production books learn from a model fit on
    labels that are ready at the clock, or from a causal SMA.
    """
    if df is None or df.empty:
        raise ReplayError("no bars to replay", reason="insufficient_bars")
    pair_u = str(pair).upper()
    frame = df.sort_index()
    frame = frame[~frame.index.duplicated(keep="last")]
    for col in ("Open", "High", "Low", "Close"):
        if col not in frame.columns:
            raise ReplayError(f"history is missing {col}", reason="insufficient_bars")
    cfg = cfg or {}
    rc = replay_cfg(cfg)
    wf = dict(cfg.get("walk_forward") or {})
    horizon = int(cfg.get("horizon") or 8)
    step_bars = int(rc.get("step_bars") or wf.get("step_bars") or 250)
    train_bars = int(rc.get("train_bars") or wf.get("train_bars") or 2000)
    min_train = int(rc.get("min_train_bars") or wf.get("min_train_bars") or 500)
    step_bars = max(1, step_bars)
    bid_ask = bool(rc.get("use_bid_ask", True)) and has_bid_ask(frame)
    slippage_pips = float(rc.get("slippage_pips") or 0.0)
    slip_px = slippage_pips * _pip(pair_u, cfg) if bid_ask else 0.0
    tp_atr = float((cfg.get("barrier") or {}).get("tp_atr") or 2.0)
    sl_atr = float((cfg.get("barrier") or {}).get("sl_atr") or 2.0)
    min_conf = (cfg.get("signals") or {}).get("min_confidence")
    champion_type = str((cfg.get("model") or {}).get("type") or "xgboost").lower()
    challenger_type = "logistic"
    if job_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        job_dir = replay_store_dir(cfg) / f"manual-{stamp}"
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    clock = ReplayClock(frame, as_of=frame.index[0])
    atr = true_range_atr(frame, int(cfg.get("atr_period") or 14))
    book_names = ("champion", "challenger", "sma")
    ports = {
        name: _make_port(name, job_dir, cfg, bid_ask=bid_ask, slippage_pips=slippage_pips)
        for name in book_names
    }
    pending: dict[str, _Pending | None] = {name: None for name in book_names}
    opens: dict[str, _Open | None] = {name: None for name in book_names}
    decisions: list[dict[str, Any]] = []

    feats = None
    labels = None
    pred_until = -1
    predictions: dict[str, pd.DataFrame] = {}
    sma_sig = _sma_signals(frame["Close"])
    first_i = 0
    if signal_at is None:
        feats = build_features(frame, cfg, pair=pair_u)
        feats = feats.dropna(axis=1, how="all").dropna(axis=0, how="any")
        labels = build_labels(frame, cfg)
        if feats.empty:
            raise ReplayError(f"Not enough bars to build features for {pair_u}", reason="insufficient_bars")
        first_i = _first_decision_index(feats.index, frame.index, horizon, min_train)
        if first_i is None:
            raise ReplayError(
                f"Not enough settled labels for {pair_u} (need {min_train} trainable rows, horizon {horizon}). "
                "Pull a longer range. No prices were invented.",
                reason="insufficient_bars",
            )

    n = len(frame)
    if progress:
        progress({"phase": "replay", "fraction": 0.0, "message": f"Replay {pair_u} from {frame.index[first_i]}", "bars": n})

    for i in range(first_i, n):
        ts = pd.Timestamp(frame.index[i])
        clock.seek(ts)
        visible = clock.visible()
        if visible.empty or pd.Timestamp(visible.index.max()) > clock.as_of:
            raise LookaheadError(f"clock at {ts} exposed a future bar")
        row = clock.bar(ts)
        if signal_at is None and feats is not None and labels is not None and i >= pred_until:
            _refit(
                ts=ts,
                bar_i=i,
                step_bars=step_bars,
                train_bars=train_bars,
                min_train=min_train,
                horizon=horizon,
                frame=frame,
                feats=feats,
                labels=labels,
                cfg=cfg,
                pair=pair_u,
                champion_type=champion_type,
                challenger_type=challenger_type,
                predictions=predictions,
            )
            pred_until = min(n, i + step_bars)

        for name in book_names:
            just_opened = _fill_pending(
                name,
                ports[name],
                pending,
                opens,
                row,
                ts,
                clock,
                bid_ask=bid_ask,
                slip_px=slip_px,
                tp_atr=tp_atr,
                sl_atr=sl_atr,
                horizon=horizon,
                pair=pair_u,
                interval=interval,
            )
            if not just_opened:
                _mark_open(
                    name,
                    ports[name],
                    opens,
                    row,
                    ts,
                    bid_ask=bid_ask,
                    horizon=horizon,
                )
            if opens[name] is not None or pending[name] is not None:
                continue
            signal, confidence = _signal_for(
                name,
                ts,
                visible,
                predictions,
                sma_sig,
                signal_at,
            )
            if collect_decisions:
                decisions.append(
                    {
                        "book": name,
                        "as_of": _utc_label(ts),
                        "visible_max": _utc_label(visible.index.max()),
                        "signal": signal,
                    }
                )
            if signal not in {"BUY", "SELL"}:
                continue
            try:
                atr_v = float(atr.loc[ts])
            except (KeyError, TypeError, ValueError):
                atr_v = float("nan")
            if not np.isfinite(atr_v) or atr_v <= 0:
                continue
            if i + 1 >= n:
                continue  # no next bar to fill — do not invent one
            pending[name] = _Pending(side=signal, atr=atr_v, decision_ts=ts, confidence=confidence)

        if progress and (i == first_i or i == n - 1 or (i - first_i) % 48 == 0):
            progress(
                {
                    "phase": "replay",
                    "fraction": (i - first_i + 1) / max(1, n - first_i),
                    "message": f"Replay {_dhaka(ts, cfg)}",
                    "as_of": _utc_label(ts),
                    "as_of_dhaka": _dhaka(ts, cfg),
                }
            )

    hours = max((pd.Timestamp(frame.index[-1]) - pd.Timestamp(frame.index[first_i])).total_seconds() / 3600.0, 1e-9)
    return _publish(
        pair=pair_u,
        interval=interval,
        cfg=cfg,
        job_dir=job_dir,
        ports=ports,
        hours=hours,
        source=source,
        bid_ask=bid_ask,
        frame=frame,
        first_i=first_i,
        min_conf=min_conf,
        champion_type=champion_type,
        challenger_type=challenger_type,
        decisions=decisions if collect_decisions else None,
        progress=progress,
    )


def _fill_pending(
    name: str,
    port: PaperBroker,
    pending: dict[str, _Pending | None],
    opens: dict[str, _Open | None],
    row: pd.Series,
    ts: pd.Timestamp,
    clock: ReplayClock,
    *,
    bid_ask: bool,
    slip_px: float,
    tp_atr: float,
    sl_atr: float,
    horizon: int,
    pair: str,
    interval: str,
) -> bool:
    """Fill a resting order at this bar's open. Returns True when a position was opened here."""
    order = pending.get(name)
    if order is None or order.decision_ts >= ts:
        return False
    # The decision bar is behind the clock. The fill may use only this bar's open.
    if order.decision_ts >= clock.as_of:
        raise LookaheadError("fill happened on the decision bar")
    px = entry_price(row, order.side, bid_ask=bid_ask, slip=slip_px)
    if order.side == "BUY":
        tp = px + tp_atr * order.atr
        sl = px - sl_atr * order.atr
    else:
        tp = px - tp_atr * order.atr
        sl = px + sl_atr * order.atr
    fill = port.submit(
        order.side,
        pair,
        price=px,
        sl=sl,
        tp=tp,
        timestamp=_utc_label(ts),
        entry_bar_time=str(ts),
        timeframe=interval,
        model_signal=order.side,
        confidence=order.confidence,
        horizon=horizon,
        entry_ref="replay next-bar bid/ask" if bid_ask else "replay next-bar open; spread via spread_pips",
        note=f"replay book {name}",
    )
    pending[name] = None
    opens[name] = _Open(broker_id=str(fill["position_id"]), side=order.side, sl=sl, tp=tp, bars_seen=0)
    _mark_open(name, port, opens, row, ts, bid_ask=bid_ask, horizon=horizon)
    return True


def _mark_open(
    name: str,
    port: PaperBroker,
    opens: dict[str, _Open | None],
    row: pd.Series,
    ts: pd.Timestamp,
    *,
    bid_ask: bool,
    horizon: int,
) -> None:
    pos = opens.get(name)
    if pos is None:
        return
    pos.bars_seen += 1
    hit = touch_exit(pos.side, pos.sl, pos.tp, row, bid_ask=bid_ask, bars_seen=pos.bars_seen, horizon=horizon)
    if hit is None:
        return
    px, reason = hit
    port.close(pos.broker_id, price=px, reason=reason, timestamp=_utc_label(ts))
    opens[name] = None


def _signal_for(
    name: str,
    ts: pd.Timestamp,
    visible: pd.DataFrame,
    predictions: dict[str, pd.DataFrame],
    sma_sig: pd.Series,
    signal_at: SignalFn | None,
) -> tuple[str, float | None]:
    if pd.Timestamp(visible.index.max()) > ts:
        raise LookaheadError("signal saw a future bar")
    if signal_at is not None:
        raw = str(signal_at(name, ts, visible) or "HOLD").upper()
        if raw not in {"BUY", "SELL", "HOLD"}:
            raw = "HOLD"
        return raw, None
    if name == "sma":
        try:
            raw = str(sma_sig.loc[ts]).upper()
        except KeyError:
            raw = "HOLD"
        return raw if raw in {"BUY", "SELL"} else "HOLD", None
    series = predictions.get(name)
    if series is None or ts not in series.index:
        return "HOLD", None
    row = series.loc[ts]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    side = str(row.get("side") or "HOLD").upper()
    conf = row.get("confidence")
    try:
        conf_f = None if conf is None or (isinstance(conf, float) and np.isnan(conf)) else float(conf)
    except (TypeError, ValueError):
        conf_f = None
    if side not in {"BUY", "SELL"}:
        return "HOLD", conf_f
    return side, conf_f


def _sma_signals(close: pd.Series) -> pd.Series:
    fast = close.astype(float).rolling(10, min_periods=10).mean()
    slow = close.astype(float).rolling(50, min_periods=50).mean()
    sig = pd.Series("HOLD", index=close.index)
    sig = sig.mask(fast > slow, "BUY")
    sig = sig.mask(fast < slow, "SELL")
    return sig


def _first_decision_index(
    feat_index: pd.DatetimeIndex,
    bar_index: pd.DatetimeIndex,
    horizon: int,
    min_train: int,
) -> int | None:
    locs = pd.DatetimeIndex(bar_index).get_indexer(pd.DatetimeIndex(feat_index))
    ready_locs = locs + int(horizon)
    ok = (locs >= 0) & (ready_locs < len(bar_index))
    if not np.any(ok):
        return None
    ready_time = pd.DatetimeIndex(bar_index.values[ready_locs[ok]]).sort_values()
    counts = np.searchsorted(ready_time.values, pd.DatetimeIndex(bar_index).values, side="right")
    hits = np.flatnonzero(counts >= int(min_train))
    if len(hits) == 0:
        return None
    return int(hits[0])


def _refit(
    *,
    ts: pd.Timestamp,
    bar_i: int,
    step_bars: int,
    train_bars: int,
    min_train: int,
    horizon: int,
    frame: pd.DataFrame,
    feats: pd.DataFrame,
    labels: pd.Series,
    cfg: dict[str, Any],
    pair: str,
    champion_type: str,
    challenger_type: str,
    predictions: dict[str, pd.DataFrame],
) -> None:
    mask = trainable_mask(feats.index, frame.index, ts, horizon)
    y = labels.reindex(feats.index)
    mask = mask & y.notna()
    x_all = feats.loc[mask]
    y_all = y.loc[mask].astype(int)
    if len(x_all) < min_train:
        return
    if len(x_all):
        last_ready = label_ready_at(x_all.index[-1], frame.index, horizon)
        if last_ready is None or last_ready > ts:
            raise LookaheadError("training label is not settled at the clock")
    if len(x_all) > train_bars:
        x_all = x_all.iloc[-train_bars:]
        y_all = y_all.iloc[-train_bars:]
    end_i = min(len(frame.index) - 1, bar_i + step_bars - 1)
    window_end = pd.Timestamp(frame.index[end_i])
    x_te = feats.loc[(feats.index >= ts) & (feats.index <= window_end)]
    if x_te.empty:
        return
    types = {"champion": champion_type, "challenger": challenger_type}
    fitted: dict[str, pd.DataFrame] = {}
    if champion_type == challenger_type:
        frame_pred = _predict_window(cfg, x_all, y_all, x_te, champion_type, frame, pair)
        if frame_pred is not None:
            fitted["champion"] = frame_pred
            fitted["challenger"] = frame_pred
    else:
        for name, mtype in types.items():
            pred = _predict_window(cfg, x_all, y_all, x_te, mtype, frame, pair)
            if pred is not None:
                fitted[name] = pred
    for name, pred in fitted.items():
        side = pred["pred"].map(lambda code: {LABEL_MAP["BUY"]: "BUY", LABEL_MAP["SELL"]: "SELL"}.get(int(code), "HOLD"))
        out = pd.DataFrame({"side": side, "confidence": pred["confidence"] if "confidence" in pred.columns else np.nan})
        predictions[name] = out


def _predict_window(
    cfg: dict[str, Any],
    x_tr: pd.DataFrame,
    y_tr: pd.Series,
    x_te: pd.DataFrame,
    model_type: str,
    ohlcv: pd.DataFrame,
    pair: str,
) -> pd.DataFrame | None:
    from forex_lab.backtest import _attach_policy_columns
    from forex_lab.model import apply_signal_filters, fit_predict_bundle

    if y_tr.nunique() < 2:
        return None
    balanced = bool((cfg.get("model") or {}).get("class_weight_balanced", True))
    try:
        _model, pred, _cols = fit_predict_bundle(
            cfg, x_tr, y_tr, x_te, model_type=model_type, balanced=balanced
        )
        pred = _attach_policy_columns(pred, x_te, ohlcv, cfg, pair)
        pred["pred"] = apply_signal_filters(pred, cfg)
    except ReplayError:
        raise
    except Exception as exc:
        detail = " ".join(str(exc).split()).strip() or exc.__class__.__name__
        raise ReplayError(f"Train failed ({model_type}): {detail}", reason="train") from exc
    return pred


def _publish(
    *,
    pair: str,
    interval: str,
    cfg: dict[str, Any],
    job_dir: Path,
    ports: dict[str, PaperBroker],
    hours: float,
    source: str,
    bid_ask: bool,
    frame: pd.DataFrame,
    first_i: int,
    min_conf: object,
    champion_type: str,
    challenger_type: str,
    decisions: list[dict[str, Any]] | None,
    progress: ProgressFn | None,
) -> dict[str, Any]:
    if progress:
        progress({"phase": "report", "fraction": 1.0, "message": "Writing scoreboard"})
    curves: dict[str, pd.DataFrame] = {}
    board_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, Any]] = {}
    for name, port in ports.items():
        closed = port.list_closed()
        metrics[name] = _metrics(closed, hours=hours)
        metrics[name]["n_open"] = len(port.list_positions())
        curves[name] = _equity_frame(closed)
        board_rows.append({"book": name, **metrics[name]})
        for row in closed:
            trade_rows.append(
                {
                    "book": name,
                    "pair": row.get("pair"),
                    "side": row.get("side"),
                    "entry_time_utc": row.get("entry_time"),
                    "entry_time_dhaka": _dhaka(row.get("entry_time"), cfg),
                    "exit_time_utc": row.get("exit_time"),
                    "exit_time_dhaka": _dhaka(row.get("exit_time"), cfg),
                    "entry_price": row.get("entry_price"),
                    "exit_price": row.get("exit_price"),
                    "exit_reason": row.get("exit_reason"),
                    "net_pnl": row.get("realized"),
                    "outcome": row.get("outcome"),
                }
            )
    decision = promotion_decision(metrics.get("champion"), metrics.get("challenger"), cfg)
    line = _promotion_line(decision.as_dict(), min_conf)
    board = pd.DataFrame(board_rows)
    board["promotion"] = ""
    if len(board):
        board.loc[board["book"] == "challenger", "promotion"] = decision.verdict
        board.loc[board["book"] == "champion", "promotion"] = "incumbent"
        board.loc[board["book"] == "sma", "promotion"] = "baseline"
    csv_path = job_dir / "scoreboard.csv"
    board.to_csv(csv_path, index=False)
    trades = pd.DataFrame(trade_rows)
    trades_path = job_dir / "trades.csv"
    trades.to_csv(trades_path, index=False)
    equity_csv = job_dir / "equity.csv"
    equity_parts = []
    for name, curve in curves.items():
        if curve.empty:
            continue
        part = curve.copy()
        part.insert(0, "book", name)
        part["exit_time_dhaka"] = [_dhaka(t, cfg) for t in part["exit_time"]]
        equity_parts.append(part)
    equity_all = pd.concat(equity_parts) if equity_parts else pd.DataFrame()
    equity_all.to_csv(equity_csv, index=False)
    png_path = job_dir / "equity.png"
    _write_equity_chart(png_path, curves, cfg, pair)
    xlsx_path = job_dir / "scoreboard.xlsx"
    promo_rows = [
        ["field", "value"],
        ["verdict", decision.verdict],
        ["promote", decision.promote],
        ["mode", decision.mode],
        ["reasons", "; ".join(decision.reasons)],
        ["line", line],
        ["calendar", CALENDAR_ASOF_GAP],
        ["trades_per_hour", TRADES_PER_HOUR_NOTE],
        ["min_confidence", "" if min_conf is None else min_conf],
        ["champion_model", champion_type],
        ["challenger_model", challenger_type],
        ["bid_ask", bid_ask],
        ["source", source],
    ]
    for key, value in decision.deltas.items():
        promo_rows.append([f"delta_{key}", value])
    header = list(board.columns)
    sheet_rows = [header] + board.astype(object).where(pd.notna(board), None).values.tolist()
    write_xlsx(xlsx_path, {"scoreboard": sheet_rows, "promotion": promo_rows})
    (job_dir / "promotion.json").write_text(
        json.dumps({"line": line, **decision.as_dict(), "calendar_asof": "incomplete", "trades_per_hour_gate": False}, indent=2, default=str),
        encoding="utf-8",
    )
    report = _report_md(
        pair=pair,
        interval=interval,
        cfg=cfg,
        frame=frame,
        first_i=first_i,
        source=source,
        bid_ask=bid_ask,
        board=board,
        line=line,
        min_conf=min_conf,
        champion_type=champion_type,
        challenger_type=challenger_type,
    )
    (job_dir / "report.md").write_text(report, encoding="utf-8")
    return {
        "ok": True,
        "pair": pair,
        "interval": interval,
        "source": source,
        "bid_ask": bid_ask,
        "rows": int(len(frame)),
        "start": _utc_label(frame.index[0]),
        "end": _utc_label(frame.index[-1]),
        "start_dhaka": _dhaka(frame.index[0], cfg),
        "end_dhaka": _dhaka(frame.index[-1], cfg),
        "decision_start_dhaka": _dhaka(frame.index[first_i], cfg),
        "job_dir": str(job_dir),
        "promotion": decision.as_dict(),
        "promotion_line": line,
        "calendar_note": CALENDAR_ASOF_GAP,
        "scoreboard": board.to_dict(orient="records"),
        "files": {
            "csv": str(csv_path),
            "xlsx": str(xlsx_path),
            "equity_png": str(png_path),
            "equity_csv": str(equity_csv),
            "trades": str(trades_path),
            "report": str(job_dir / "report.md"),
        },
        "decisions": decisions,
    }


def _promotion_line(payload: dict[str, Any], min_conf: object) -> str:
    reasons = "; ".join(payload.get("reasons") or [])
    conf = "off" if min_conf is None else min_conf
    return (
        f"Promotion {payload.get('verdict')}: {reasons}. "
        f"Same retrain gate as live ({payload.get('mode')}) on profit factor, total return, and max drawdown. "
        f"min_confidence={conf}. {TRADES_PER_HOUR_NOTE}"
    )


def _report_md(
    *,
    pair: str,
    interval: str,
    cfg: dict[str, Any],
    frame: pd.DataFrame,
    first_i: int,
    source: str,
    bid_ask: bool,
    board: pd.DataFrame,
    line: str,
    min_conf: object,
    champion_type: str,
    challenger_type: str,
) -> str:
    lines = [
        f"# Replay scoreboard — {pair} {interval}",
        "",
        "Research only. Paper books. No live orders.",
        "",
        f"- Range: {_dhaka(frame.index[0], cfg)} -> {_dhaka(frame.index[-1], cfg)}",
        f"- First decision: {_dhaka(frame.index[first_i], cfg)}",
        f"- Source: `{source}` ({'bid/ask fills' if bid_ask else 'mid fills + spread_pips'})",
        f"- Champion model: `{champion_type}` · Challenger model: `{challenger_type}` · SMA 10/50 baseline",
        f"- min_confidence: {min_conf}",
        "",
        "## Promotion",
        "",
        line,
        "",
        "This does **not** overwrite `data/champion` or the live paper journal.",
        "",
        "## Books",
        "",
        "| Book | Trades | Win rate | Expectancy | Net P/L | Max DD | Profit factor | Trades/hour |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in board.iterrows():
        lines.append(
            "| {book} | {n} | {wr} | {ex} | {pnl} | {dd} | {pf} | {tph} |".format(
                book=row["book"],
                n=int(row["n_trades"]),
                wr=_fmt_num(row["win_rate"], 4),
                ex=_fmt_num(row["expectancy"], 6),
                pnl=_fmt_num(row["net_pnl"], 6),
                dd=_fmt_num(row["max_drawdown"], 4),
                pf=_fmt_num(row["profit_factor"], 4),
                tph=_fmt_num(row["trades_per_hour"], 6),
            )
        )
    lines.extend(["", "## Gap", "", CALENDAR_ASOF_GAP, "", TRADES_PER_HOUR_NOTE, ""])
    return "\n".join(lines)

