"""Background history-pull and replay-train jobs.

One Active pair at a time. A request names that pair; it does not walk the
watchlist, and a second historic pull or replay is refused while one is running.

Live paper (``broker.store``) is never opened here. Each replay job gets its
own directory under ``data/replay/`` with separate PaperBroker JSON files.
"""
from __future__ import annotations

import csv
import json
import math
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forex_lab.clock import fmt_display
from forex_lab.history import (
    HistoryError,
    explain_failure,
    history_status,
    load_history,
    load_meta,
    normalize_interval,
    pull_history,
    replay_store_dir,
)
from forex_lab.replay import CALENDAR_ASOF_GAP, run_replay
from forex_lab.ui.watchlist import WatchlistError, normalize_pair

from api.deskdata import ASSET_ALLOW

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_ACTIVE_ID: str | None = None
_ID = re.compile(r"^[a-f0-9]{32}$")
_BATCH = {"ALL", "WATCHLIST", "*", "ANY"}
_BOOK_ORDER = ("champion", "challenger", "sma")

# Shown on the desk scoreboard. Replay never writes data/champion.
REPLAY_ADVISORY = (
    "Replay promote is advisory research and does not replace the live weekly champion "
    "until the weekly retrain gate says so. This run does not overwrite the live champion."
)


class ReplayJobError(ValueError):
    """Bad pair, interval, or unknown job."""


class ReplayBusy(ReplayJobError):
    """A historic pull or replay is already running for the Active pair."""


def reset_jobs() -> None:
    """Drop in-memory job slots. Tests only."""
    global _ACTIVE_ID
    with _LOCK:
        _ACTIVE_ID = None
        _JOBS.clear()


def parse_pair(pair: object) -> str:
    """One symbol. Lists, 'ALL', and comma-separated batches are refused."""
    if isinstance(pair, (list, tuple, set)):
        raise ReplayJobError("Historic pull and replay take one Active pair, not the watchlist.")
    text = str(pair or "").strip()
    if not text or text.upper() in _BATCH:
        raise ReplayJobError("Historic pull and replay take one Active pair, not the watchlist.")
    if any(sep in text for sep in (",", ";", "|")):
        raise ReplayJobError("Historic pull and replay take one Active pair, not a list.")
    try:
        symbol = normalize_pair(text)
    except WatchlistError as exc:
        raise ReplayJobError(str(exc)) from exc
    if symbol not in ASSET_ALLOW:
        raise ReplayJobError(f"{symbol} is not on the Decision watchlist universe")
    return symbol


def job_dir(job_id: str, cfg: dict[str, Any] | None = None) -> Path:
    if not _ID.match(job_id):
        raise ReplayJobError("unknown job")
    return replay_store_dir(cfg) / job_id


def start_pull(
    cfg: dict[str, Any],
    *,
    pair: str,
    interval: str | None,
    start: str | None,
    end: str | None,
    source: str | None = None,
) -> dict[str, Any]:
    symbol = parse_pair(pair)
    iv = _interval(interval)
    return _start(cfg, kind="pull", pair=symbol, interval=iv, start=start, end=end, pull=False, source=source)


def start_replay(
    cfg: dict[str, Any],
    *,
    pair: str,
    interval: str | None,
    start: str | None,
    end: str | None,
    pull: bool = True,
) -> dict[str, Any]:
    symbol = parse_pair(pair)
    iv = _interval(interval)
    return _start(cfg, kind="replay", pair=symbol, interval=iv, start=start, end=end, pull=pull, source=None)


def get_job(job_id: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _ID.match(str(job_id or "")):
        raise ReplayJobError("unknown job")
    with _LOCK:
        current = _JOBS.get(job_id)
        data = _public(current) if current is not None else None
    if data is None:
        path = job_dir(job_id, cfg) / "status.json"
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReplayJobError("job status is unreadable") from exc
            if isinstance(loaded, dict):
                data = loaded
    if data is None:
        raise ReplayJobError("unknown job")
    return present_job(data, cfg)


def latest_replay(
    cfg: dict[str, Any] | None,
    *,
    pair: str,
    interval: str | None = None,
) -> dict[str, Any]:
    """Last successful replay for one pair, plus a newer failure or a live run.

    ``interval`` prefers that timeframe. If the only finished replay used another
    timeframe, that scoreboard is still returned and ``interval_match`` is false.
    A status file left at ``running`` after the desk process died is reported as
    an error so the failure is not silent.
    """
    symbol = parse_pair(pair)
    iv = _interval(interval) if interval else None
    found: dict[str, tuple[float, dict[str, Any]]] = {}
    root = replay_store_dir(cfg)
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            path = folder / "status.json"
            if not path.is_file():
                continue
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(loaded, dict) or loaded.get("kind") != "replay":
                continue
            if str(loaded.get("pair") or "") != symbol:
                continue
            job_id = str(loaded.get("job_id") or "")
            if not _ID.match(job_id):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            found[job_id] = (_stamp(loaded, mtime), loaded)
    with _LOCK:
        memory = [
            (job_id, _public(job))
            for job_id, job in _JOBS.items()
            if job.get("kind") == "replay" and job.get("pair") == symbol
        ]
    for job_id, job in memory:
        previous = found.get(job_id)
        fallback = previous[0] if previous else time.time()
        found[job_id] = (_stamp(job, fallback), job)

    presented = [(stamp, present_job(payload, cfg)) for stamp, payload in found.values()]
    done = _newest(presented, "done", iv if iv else None)
    if iv and done is None:
        done = _newest(presented, "done", None)
        interval_match = False
    else:
        interval_match = done is not None
    running = _newest(presented, "running", iv if iv else None) or _newest(presented, "running", None)
    if iv and done is not None and done[1].get("interval") == iv:
        error_pool = [item for item in presented if item[1].get("status") == "error" and item[1].get("interval") == iv]
    else:
        error_pool = [item for item in presented if item[1].get("status") == "error"]
    recent_error = None
    if error_pool:
        err_stamp, err_job = max(error_pool, key=lambda item: item[0])
        done_stamp = done[0] if done else -1.0
        if err_stamp >= done_stamp:
            recent_error = err_job
    return {
        "pair": symbol,
        "interval": iv,
        "interval_match": bool(interval_match and done and (iv is None or done[1].get("interval") == iv)),
        "advisory": REPLAY_ADVISORY,
        "job": done[1] if done else None,
        "recent_error": recent_error,
        "running": running[1] if running else None,
    }


def compact_scoreboard(rows: object) -> list[dict[str, Any]]:
    """JSON-safe book rows. Non-finite profit factor becomes ``inf`` / ``-inf``."""
    if not isinstance(rows, list):
        return []
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = _compact_row(row)
        if item is not None:
            compact.append(item)
    return _order_books(compact)


def present_job(data: dict[str, Any], cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public job payload. Done replays include the scoreboard without a file download."""
    out = {key: value for key, value in data.items() if key != "thread"}
    out.pop("updated_at", None)
    if out.get("kind") != "replay":
        return out
    out["advisory"] = REPLAY_ADVISORY
    job_id = str(out.get("job_id") or "")
    if out.get("status") == "running" and not _job_is_live(job_id):
        note = "Replay stopped before it finished (the desk restarted). Run Pull and replay again."
        out["status"] = "error"
        out["phase"] = "error"
        out["reason"] = "error"
        out["error"] = note
        out["message"] = note
        return out
    if out.get("status") != "done":
        return out
    folder: Path | None
    try:
        folder = job_dir(job_id, cfg)
    except ReplayJobError:
        folder = None
    rows = compact_scoreboard(out.get("scoreboard"))
    if not rows and folder is not None:
        rows = _read_scoreboard_csv(folder)
    out["scoreboard"] = rows
    promo = _compact_promotion(out.get("promotion"))
    if promo is None and folder is not None:
        promo = _read_promotion(folder)
    if promo is not None:
        out["promotion"] = promo
    return out


def wait_job(job_id: str, timeout: float = 30, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Block until the worker finishes. Tests only."""
    with _LOCK:
        thread = (_JOBS.get(job_id) or {}).get("thread")
    if isinstance(thread, threading.Thread):
        thread.join(timeout)
    return get_job(job_id, cfg)


def job_file(job_id: str, name: str, cfg: dict[str, Any] | None = None) -> Path:
    root = job_dir(job_id, cfg).resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ReplayJobError("report file is not ready")
    return path


def _job_is_live(job_id: str) -> bool:
    if not _ID.match(job_id or ""):
        return False
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return False
        return job.get("status") not in {"done", "error"}


def _stamp(data: dict[str, Any], fallback: float) -> float:
    try:
        return float(data.get("updated_at"))
    except (TypeError, ValueError):
        return fallback


def _newest(
    items: list[tuple[float, dict[str, Any]]],
    status: str,
    interval: str | None,
) -> tuple[float, dict[str, Any]] | None:
    pool = [item for item in items if item[1].get("status") == status]
    if interval is not None:
        pool = [item for item in pool if item[1].get("interval") == interval]
    if not pool:
        return None
    return max(pool, key=lambda item: item[0])


def _json_number(value: object) -> float | None:
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"none", "nan", "null", "inf", "infinity", "+inf", "+infinity", "-inf", "-infinity"}:
            return None
        value = text
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _profit_factor(value: object) -> float | str | None:
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"inf", "infinity", "+inf", "+infinity"}:
            return "inf"
        if text in {"-inf", "-infinity"}:
            return "-inf"
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _json_number(value)
    if math.isnan(number):
        return None
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    return number


def _json_int(value: object) -> int | None:
    number = _json_number(value)
    if number is None:
        return None
    return int(number)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return False
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False


def _compact_row(row: dict[str, Any]) -> dict[str, Any] | None:
    book = str(row.get("book") or "").strip()
    if not book:
        return None
    promotion = str(row.get("promotion") or "").strip()
    return {
        "book": book,
        "n_trades": _json_int(row.get("n_trades")),
        "win_rate": _json_number(row.get("win_rate")),
        "expectancy": _json_number(row.get("expectancy")),
        "net_pnl": _json_number(row.get("net_pnl")),
        "max_drawdown": _json_number(row.get("max_drawdown")),
        "profit_factor": _profit_factor(row.get("profit_factor")),
        "total_return": _json_number(row.get("total_return")),
        "promotion": promotion or None,
    }


def _order_books(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> int:
        book = str(row.get("book") or "")
        try:
            return _BOOK_ORDER.index(book)
        except ValueError:
            return len(_BOOK_ORDER)

    return sorted(rows, key=key)


def _compact_promotion(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    verdict = raw.get("verdict")
    if verdict is None and "promote" not in raw and not raw.get("reasons"):
        return None
    reasons = raw.get("reasons") or []
    if isinstance(reasons, str):
        reasons = [part.strip() for part in reasons.split(";") if part.strip()]
    if not isinstance(reasons, (list, tuple)):
        reasons = []
    mode = raw.get("mode")
    return {
        "verdict": None if verdict is None else str(verdict),
        "promote": _as_bool(raw.get("promote")),
        "reasons": [str(item) for item in reasons],
        "mode": None if mode is None else str(mode),
    }


def _read_scoreboard_csv(folder: Path) -> list[dict[str, Any]]:
    path = folder / "scoreboard.csv"
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    return compact_scoreboard(rows)


def _read_promotion(folder: Path) -> dict[str, Any] | None:
    path = folder / "promotion.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _compact_promotion(loaded)


def _interval(interval: str | None) -> str:
    try:
        return normalize_interval(interval or "1h")
    except HistoryError as exc:
        raise ReplayJobError(str(exc)) from exc


def _start(
    cfg: dict[str, Any],
    *,
    kind: str,
    pair: str,
    interval: str,
    start: str | None,
    end: str | None,
    pull: bool,
    source: str | None,
) -> dict[str, Any]:
    global _ACTIVE_ID
    with _LOCK:
        current = _running_locked()
        if current is not None:
            same = (
                current.get("pair") == pair
                and current.get("interval") == interval
                and current.get("kind") == kind
            )
            if same:
                return _public(current)
            raise ReplayBusy(
                f"A historic {current.get('kind')} is already running for {current.get('pair')} "
                f"{current.get('interval')}. One Active pair at a time."
            )
        job_id = uuid.uuid4().hex
        folder = job_dir(job_id, cfg)
        folder.mkdir(parents=True, exist_ok=True)
        status = {
            "job_id": job_id,
            "kind": kind,
            "status": "running",
            "phase": "pull" if kind == "pull" or pull else "replay",
            "pair": pair,
            "interval": interval,
            "start": start,
            "end": end,
            "fraction": 0.0,
            "message": "Starting",
            "as_of_dhaka": None,
            "error": None,
            "reason": None,
            "calendar_note": CALENDAR_ASOF_GAP,
            "promotion_line": None,
            "finished_at_dhaka": None,
            "source": None,
            "bid_ask": None,
            "rows": None,
            "report": None,
            "updated_at": time.time(),
        }
        _JOBS[job_id] = status
        _ACTIVE_ID = job_id
    _write(status, folder)
    thread = threading.Thread(
        target=_worker,
        args=(job_id, cfg, kind, pair, interval, start, end, pull, source),
        name=f"forx-{kind}-{pair}",
        daemon=True,
    )
    with _LOCK:
        _JOBS[job_id]["thread"] = thread
    thread.start()
    return _public(status)


def _worker(
    job_id: str,
    cfg: dict[str, Any],
    kind: str,
    pair: str,
    interval: str,
    start: str | None,
    end: str | None,
    pull: bool,
    source: str | None,
) -> None:
    folder = job_dir(job_id, cfg)
    try:
        if kind == "pull" or pull:
            stale = True
            if kind == "replay":
                info = history_status(pair, interval, cfg, start, end)
                stale = bool(info.get("stale"))
                if not stale:
                    _update(job_id, folder, phase="pull", fraction=1.0, message="History cache covers this range", source=info.get("source"), rows=info.get("rows"), bid_ask=info.get("bid_ask"))
            if kind == "pull" or stale:
                pulled = pull_history(
                    pair,
                    cfg,
                    interval=interval,
                    start=start,
                    end=end,
                    source=source,
                    progress=lambda payload: _on_progress(job_id, folder, payload),
                )
                _update(
                    job_id,
                    folder,
                    source=pulled.get("source"),
                    rows=pulled.get("rows"),
                    bid_ask=pulled.get("bid_ask"),
                    message=f"History {pulled.get('rows')} bars from {pulled.get('source')}",
                )
        if kind == "pull":
            _update(job_id, folder, status="done", phase="done", fraction=1.0)
            return
        meta = load_meta(pair, interval, cfg)
        frame = load_history(pair, cfg, interval, start=start, end=end)
        result = run_replay(
            frame,
            cfg,
            pair,
            interval=interval,
            job_dir=folder,
            source=str(meta.get("source") or "cache"),
            progress=lambda payload: _on_progress(job_id, folder, payload),
        )
        _update(
            job_id,
            folder,
            status="done",
            phase="done",
            fraction=1.0,
            message="Scoreboard ready",
            source=result.get("source"),
            bid_ask=result.get("bid_ask"),
            rows=result.get("rows"),
            promotion_line=result.get("promotion_line"),
            promotion=_compact_promotion(result.get("promotion")),
            scoreboard=compact_scoreboard(result.get("scoreboard")),
            advisory=REPLAY_ADVISORY,
            finished_at_dhaka=fmt_display(datetime.now(timezone.utc), cfg),
            report=_links(job_id),
            as_of_dhaka=result.get("end_dhaka"),
        )
    except Exception as exc:  # noqa: BLE001 — surface the reason; do not invent a scoreboard
        reason, text = explain_failure(exc)
        _update(
            job_id,
            folder,
            status="error",
            phase="error",
            error=text,
            message=text,
            reason=reason,
            finished_at_dhaka=fmt_display(datetime.now(timezone.utc), cfg),
        )


def _on_progress(job_id: str, folder: Path, payload: dict[str, Any]) -> None:
    _update(
        job_id,
        folder,
        phase=payload.get("phase") or "replay",
        fraction=payload.get("fraction"),
        message=payload.get("message"),
        as_of_dhaka=payload.get("as_of_dhaka"),
        rows=payload.get("rows"),
    )


def _links(job_id: str) -> dict[str, str]:
    return {
        "csv": f"/replay/jobs/{job_id}/scoreboard?format=csv",
        "xlsx": f"/replay/jobs/{job_id}/scoreboard?format=xlsx",
        "equity_png": f"/replay/jobs/{job_id}/equity",
        "report_md": f"/replay/jobs/{job_id}/report",
    }


def _running_locked() -> dict[str, Any] | None:
    if _ACTIVE_ID is None:
        return None
    job = _JOBS.get(_ACTIVE_ID)
    if job is None or job.get("status") in {"done", "error"}:
        return None
    return job


def _update(job_id: str, folder: Path, **fields: Any) -> None:
    global _ACTIVE_ID
    with _LOCK:
        current = _JOBS.get(job_id)
        if current is None:
            current = {"job_id": job_id}
            _JOBS[job_id] = current
        for key, value in fields.items():
            if value is not None or key in {"error", "promotion_line", "promotion", "as_of_dhaka", "scoreboard"}:
                current[key] = value
        current["updated_at"] = time.time()
        if current.get("status") in {"done", "error"} and _ACTIVE_ID == job_id:
            _ACTIVE_ID = None
        snapshot = _public(current)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")


def _write(status: dict[str, Any], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps(_public(status), indent=2, default=str), encoding="utf-8")


def _public(status: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in status.items() if k != "thread"}
