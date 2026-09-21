"""Awareness panel: every feed the desk fetches or observes.

Reuses the v0 health-strip builders. One row per watchlist OHLCV, news RSS,
and model file, plus calendar, optional FRED pack, and the yfinance gate when
backing off. STALE / FAIL / MISSING never render as OK. CLOSED (weekend) is
not a panic. Paper BrokerPort is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any, Iterable, Mapping

from forex_lab.clock import fmt_display, relabel, relabel_in_text
from forex_lab.freshness import FetchGate
from forex_lab.news import NewsBundle

AWARENESS_COLS = ("Source", "Observing", "Cadence", "Last OK", "Status")
UNHEALTHY = frozenset({"STALE", "FAIL", "ERROR", "MISSING"})
_OKISH = frozenset({"OK", "STALE", "CLOSED"})
_DETAIL_MAX = 72


def status_token(row_or_status: object) -> str:
    """OK / STALE / FAIL / … even when Status carries a short error after ·."""
    if isinstance(row_or_status, Mapping):
        raw = str(row_or_status.get("Status") or "")
    else:
        raw = str(row_or_status or "")
    raw = raw.strip()
    if not raw:
        return ""
    token = raw.split("·", 1)[0].strip().split()[0].upper()
    return "FAIL" if token == "ERROR" else token


def _short(text: object, n: int = _DETAIL_MAX) -> str:
    s = " ".join(relabel_in_text(text).split())
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


def format_status(token: str, detail: object | None = None) -> str:
    """Status cell: ``OK`` or ``STALE · short error``. ERROR maps to FAIL."""
    tok = str(token or "MISSING").upper()
    if tok == "ERROR":
        tok = "FAIL"
    note = _short(detail) if detail else ""
    if tok in {"OK", "OFF"} or not note:
        return tok
    return f"{tok} · {note}"


def _last_ok_label(value: object, *, show: bool) -> str:
    """Last successful update in the UI clock (Asia/Dhaka). n/a if never OK."""
    if not show:
        return "n/a"
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        return fmt_display(value, seconds=True)
    raw = str(value).strip()
    if not raw or raw.lower() in {"n/a", "none", "-"}:
        return "n/a"
    return relabel(value, seconds=True)


def _row(
    source: str,
    observing: str,
    cadence: str,
    last_ok: str,
    status: str,
    detail: object | None = None,
) -> dict[str, str]:
    return {
        "Source": source,
        "Observing": observing,
        "Cadence": cadence,
        "Last OK": last_ok or "n/a",
        "Status": format_status(status, detail),
    }


def _yf_last_label(gate: FetchGate | None, pair: str) -> str:
    if gate is None:
        return ""
    ts = (gate.last_yf_ok or {}).get(str(pair).upper())
    if not ts:
        return ""
    try:
        return fmt_display(datetime.fromtimestamp(float(ts), tz=timezone.utc), seconds=True)
    except (OSError, OverflowError, ValueError):
        return ""


def _yf_best_ok(gate: FetchGate | None) -> str:
    if gate is None or not gate.last_yf_ok:
        return "n/a"
    try:
        ts = max(float(v) for v in gate.last_yf_ok.values())
        return fmt_display(datetime.fromtimestamp(ts, tz=timezone.utc), seconds=True)
    except (OSError, OverflowError, ValueError, TypeError):
        return "n/a"


def _ohlcv_row(
    row: Any,
    *,
    gate: FetchGate | None,
    cadence: str,
) -> dict[str, str]:
    pair = str(getattr(row, "pair", "") or "")
    tf = str(getattr(row, "timeframe", "") or "")
    validity = str(getattr(row, "validity", "MISSING") or "MISSING")
    status = "FAIL" if validity.upper() == "ERROR" else validity.upper()
    yf_ok = _yf_last_label(gate, pair)
    last_fetch = getattr(row, "last_fetch_at", None) or yf_ok or getattr(row, "last_bar_at", None)
    show_ok = status in _OKISH
    detail = getattr(row, "validity_reason", None) or getattr(row, "data_source", None)
    if status == "OK":
        detail = None
    return _row(
        f"{pair} {tf} OHLCV".strip(),
        "price",
        cadence,
        _last_ok_label(last_fetch, show=show_ok),
        status,
        detail,
    )


def _news_row(pair: str, bundle: NewsBundle | None, cadence: str) -> dict[str, str]:
    source = f"{pair} news RSS"
    if bundle is None:
        return _row(
            source,
            "headlines",
            cadence,
            "n/a",
            "MISSING",
            "news not fetched this tick",
        )
    err = bundle.error
    if err and str(err).lower() == "disabled":
        return _row(source, "headlines", "off (news lane disabled)", "n/a", "OFF", None)
    if err and not bundle.headlines:
        return _row(source, "headlines", cadence, "n/a", "FAIL", err)
    if err and bundle.headlines:
        # Cached headlines after a failed refresh must not look OK.
        return _row(
            source,
            "headlines",
            cadence,
            _last_ok_label(bundle.fetched_at, show=True),
            "STALE",
            err,
        )
    if bundle.headlines:
        return _row(
            source,
            "headlines",
            cadence,
            _last_ok_label(bundle.fetched_at, show=True),
            "OK",
            None,
        )
    return _row(
        source,
        "headlines",
        cadence,
        "n/a",
        "MISSING",
        err or "empty feed",
    )


def _model_info(info: Any) -> dict[str, Any]:
    if info is None:
        return {}
    if isinstance(info, Mapping):
        return dict(info)
    return {
        "exists": bool(getattr(info, "exists", False)),
        "path": getattr(info, "path", None),
        "fetched_at": getattr(info, "fetched_at", None),
        "type": getattr(info, "type", None) or getattr(info, "model_type", None),
        "empty": bool(getattr(info, "empty", False)),
    }


def _model_row(pair: str, info: Any | None) -> dict[str, str]:
    data = _model_info(info)
    mtype = str(data.get("type") or "xgboost")
    source = f"{pair} {mtype} model"
    exists = bool(data.get("exists"))
    empty = bool(data.get("empty"))
    path = data.get("path")
    fetched = data.get("fetched_at")
    if exists and not empty:
        return _row(
            source,
            "signals",
            "manual (Train)",
            _last_ok_label(fetched, show=True),
            "OK",
            None,
        )
    if empty or (path and Path(str(path)).exists() and Path(str(path)).stat().st_size == 0):
        return _row(source, "signals", "manual (Train)", "n/a", "FAIL", "empty model file")
    return _row(
        source,
        "signals",
        "manual (Train)",
        "n/a",
        "MISSING",
        "no joblib — Train required",
    )


def model_status_map(
    pairs: Iterable[str],
    cfg: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """On-disk model presence for awareness rows. No training, no broker."""
    from forex_lab.config_loader import load_config
    from forex_lab.model import model_paths

    cfg = cfg if cfg is not None else load_config()
    mtype = str((cfg.get("model") or {}).get("type") or "xgboost")
    out: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        key = str(pair).upper()
        if not key:
            continue
        path = model_paths(key, cfg, mtype)["model"]
        exists = False
        empty = False
        fetched: datetime | None = None
        try:
            if path.exists():
                size = path.stat().st_size
                empty = size == 0
                exists = size > 0
                if exists:
                    fetched = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            exists, empty, fetched = False, False, None
        out[key] = {
            "exists": exists,
            "empty": empty,
            "path": path,
            "fetched_at": fetched,
            "type": mtype,
        }
    return out


def _calendar_health(calendar: Any | None, ttl_s: int) -> dict[str, str] | None:
    if calendar is None:
        return None
    cadence = f"weekly JSON · cache TTL {int(ttl_s)}s"
    events = list(getattr(calendar, "events", None) or [])
    err = getattr(calendar, "error", None)
    stale = bool(getattr(calendar, "stale_cache", False))
    fetched = getattr(calendar, "fetched_at", None)
    source = "calendar"
    if err and not events:
        return _row(source, "events", cadence, "n/a", "FAIL", err)
    if stale:
        return _row(
            source,
            "events",
            cadence,
            _last_ok_label(fetched, show=True),
            "STALE",
            err or "stale calendar cache",
        )
    if events:
        return _row(
            source,
            "events",
            cadence,
            _last_ok_label(fetched, show=True),
            "OK",
            None,
        )
    return _row(source, "events", cadence, "n/a", "MISSING", err or "no high-impact events")


def _fred_health(status: Any | None) -> dict[str, str] | None:
    if status is None:
        return None
    enabled = bool(getattr(status, "enabled", False))
    series = list(getattr(status, "series", None) or [])
    source_kind = str(getattr(status, "source", "") or "missing")
    err = getattr(status, "error", None)
    fetched = getattr(status, "fetched_at", None)
    if not enabled:
        return _row(
            "FRED",
            "macro",
            "off (feature pack disabled)",
            "n/a",
            "OFF",
            None,
        )
    key_note = "API key set" if getattr(status, "used_api_key", False) else "no API key (CSV ok)"
    cadence = f"daily · as-of lag · {key_note}"
    if err and not series:
        return _row("FRED", "macro", cadence, "n/a", "FAIL", err)
    if source_kind == "missing" and not series:
        return _row(
            "FRED",
            "macro",
            cadence,
            "n/a",
            "MISSING",
            err or "FRED pack enabled, no series yet",
        )
    if series:
        tok = "STALE" if err else "OK"
        return _row(
            "FRED",
            "macro",
            cadence,
            _last_ok_label(fetched, show=True),
            tok,
            err,
        )
    return _row("FRED", "macro", cadence, "n/a", "MISSING", err or source_kind)


def build_health_rows(
    board_rows: Iterable[Any],
    *,
    news_map: dict[str, NewsBundle] | None = None,
    calendar: Any | None = None,
    calendar_ttl_s: int = 1800,
    fred: Any | None = None,
    gate: FetchGate | None = None,
    realtime: bool = False,
    refresh_s: int = 60,
    news_ttl_s: int = 300,
    yf_min_interval_s: int = 60,
    models: Mapping[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Awareness rows: watchlist OHLCV + news + models, calendar, optional FRED."""
    cadence = (
        f"realtime {int(refresh_s)}s (local signals; yfinance when due, "
        f"1 pair/tick, min {int(yf_min_interval_s)}s)"
        if realtime
        else "manual only (Realtime off)"
    )
    news_cadence = f"Google News RSS · cache TTL {int(news_ttl_s)}s"
    out: list[dict[str, str]] = []
    news_map = news_map or {}
    for row in board_rows:
        pair = str(getattr(row, "pair", "") or "")
        out.append(_ohlcv_row(row, gate=gate, cadence=cadence))
        out.append(_news_row(pair, news_map.get(pair), news_cadence))
        if models is not None:
            out.append(_model_row(pair, models.get(pair) or models.get(pair.upper())))
    if gate is not None and gate.last_error and not gate.allowed(
        datetime.now(timezone.utc).timestamp()
    ):
        out.insert(
            0,
            _row(
                "yfinance gate",
                "price",
                cadence,
                _yf_best_ok(gate),
                "FAIL",
                gate.last_error,
            ),
        )
    cal_row = _calendar_health(calendar, calendar_ttl_s)
    if cal_row is not None:
        out.append(cal_row)
    fred_row = _fred_health(fred)
    if fred_row is not None:
        out.append(fred_row)
    return out


build_awareness_rows = build_health_rows


def health_unhealthy(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Sources that should be obvious: stale, failed, missing, or error.

    CLOSED (weekend) and OFF (optional pack disabled) are not a panic.
    """
    return [r for r in rows if status_token(r) in UNHEALTHY]


def _source(row: Mapping[str, str]) -> str:
    return str(row.get("Source") or row.get("Feed") or "?")


def health_strip(rows: list[dict[str, str]]) -> str:
    """One-line source status, always visible above the panel."""
    if not rows:
        return "Awareness: no sources — watchlist empty."
    bits = [f"{_source(r)} {status_token(r)}" for r in rows]
    return "Awareness: " + " · ".join(bits)


def awareness_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        tok = status_token(r) or "?"
        counts[tok] = counts.get(tok, 0) + 1
    return counts


def awareness_summary(rows: list[dict[str, str]]) -> str:
    """Compact ``8 OK · 1 STALE · 0 FAIL`` scan line."""
    if not rows:
        return "no sources"
    counts = awareness_counts(rows)
    order = ("OK", "STALE", "FAIL", "MISSING", "CLOSED", "OFF")
    parts = [f"{counts[k]} {k}" for k in order if counts.get(k)]
    extra = [f"{counts[k]} {k}" for k in sorted(counts) if k not in order]
    return " · ".join(parts + extra) if (parts or extra) else "no sources"


def awareness_status_html(rows: list[dict[str, str]]) -> str:
    """Colored count bar for the dense terminal theme."""
    counts = awareness_counts(rows)
    n_fail = int(counts.get("FAIL", 0)) + int(counts.get("ERROR", 0))
    n_miss = int(counts.get("MISSING", 0))
    n_stale = int(counts.get("STALE", 0))
    if n_fail or n_miss:
        cls = "bad"
    elif n_stale:
        cls = "warn"
    else:
        cls = "ok"
    summary = awareness_summary(rows)
    return (
        f'<div class="fx-status fx-awareness-status">'
        f'<span class="fx-awareness-kicker">Awareness</span> '
        f'<b class="{cls}">{summary}</b></div>'
    )


def awareness_table_html(rows: list[dict[str, str]]) -> str:
    """Dense HTML table so Status colors survive Streamlit's dataframe renderer."""
    from forex_lab.ui.theme import (
        BORDER,
        BUY,
        BUY_BG,
        ELEVATED,
        MUTED,
        NEUTRAL_BG,
        SELL,
        SELL_BG,
        SURFACE,
        TEXT,
        WARN,
        WARN_BG,
    )

    tone = {
        "OK": (BUY_BG, BUY),
        "STALE": (WARN_BG, WARN),
        "FAIL": (SELL_BG, SELL),
        "ERROR": (SELL_BG, SELL),
        "MISSING": (NEUTRAL_BG, MUTED),
        "CLOSED": (NEUTRAL_BG, MUTED),
        "OFF": (NEUTRAL_BG, MUTED),
    }
    head = "".join(
        f'<th style="text-align:left;padding:3px 8px;color:{MUTED};'
        f'font-size:0.62rem;letter-spacing:0.08em;text-transform:uppercase;'
        f'border-bottom:1px solid {BORDER}">{escape(col)}</th>'
        for col in AWARENESS_COLS
    )
    body_parts: list[str] = []
    for row in rows:
        tok = status_token(row)
        bg, fg = tone.get(tok, (SURFACE, TEXT))
        cells: list[str] = []
        for col in AWARENESS_COLS:
            val = escape(str(row.get(col) or ""))
            if col == "Status":
                cells.append(
                    f'<td style="padding:4px 8px;background:{bg};color:{fg};'
                    f'font-weight:800">{val}</td>'
                )
            elif col == "Last OK":
                cells.append(
                    f'<td style="padding:4px 8px;color:{TEXT};font-variant-numeric:tabular-nums;'
                    f'white-space:nowrap">{val}</td>'
                )
            else:
                cells.append(f'<td style="padding:4px 8px;color:{TEXT}">{val}</td>')
        body_parts.append(f"<tr>{''.join(cells)}</tr>")
    body = "".join(body_parts) or (
        f'<tr><td colspan="5" style="padding:8px;color:{MUTED}">No sources — watchlist empty.</td></tr>'
    )
    return (
        f'<div class="fx-awareness-table" style="overflow-x:auto;border:1px solid {BORDER};'
        f'background:{ELEVATED};border-radius:4px">'
        f'<table style="width:100%;border-collapse:collapse;font-size:0.78rem;'
        f'font-variant-numeric:tabular-nums"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def style_awareness(df: Any):
    """Color the Status column. Falls back to a plain frame if Styler is missing."""
    if df is None or getattr(df, "empty", True):
        return df
    cols = [c for c in AWARENESS_COLS if c in df.columns]
    rest = [c for c in df.columns if c not in cols]
    ordered = df[cols + rest] if cols else df
    try:
        from forex_lab.ui.theme import awareness_status_style

        styler = ordered.style
        mapper = getattr(styler, "map", None) or getattr(styler, "applymap", None)
        if mapper is not None and "Status" in ordered.columns:
            styler = mapper(awareness_status_style, subset=["Status"])
        return styler
    except Exception:
        return ordered
