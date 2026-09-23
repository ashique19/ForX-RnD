"""Background history-pull and replay-train jobs.

One Active pair at a time. A request names that pair; it does not walk the
watchlist, and a second historic pull or replay is refused while one is running.

Live paper (``broker.store``) is never opened here. Each replay job gets its
own directory under ``data/replay/`` with separate PaperBroker JSON files.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from pathlib import Path
from typing import Any

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
from forex_lab.replay import CALENDAR_ASOF_GAP, ReplayError, run_replay
from forex_lab.ui.watchlist import WatchlistError, normalize_pair

from api.deskdata import ASSET_ALLOW

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_ACTIVE_ID: str | None = None
_ID = re.compile(r"^[a-f0-9]{32}$")
_BATCH = {"ALL", "WATCHLIST", "*", "ANY"}


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
        if current is not None:
            return _public(current)
    path = job_dir(job_id, cfg) / "status.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReplayJobError("job status is unreadable") from exc
        if isinstance(data, dict):
            return data
    raise ReplayJobError("unknown job")


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
            "source": None,
            "bid_ask": None,
            "rows": None,
            "report": None,
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
            report=_links(job_id),
            as_of_dhaka=result.get("end_dhaka"),
        )
    except Exception as exc:  # noqa: BLE001 — surface the reason; do not invent a scoreboard
        reason, text = explain_failure(exc)
        _update(job_id, folder, status="error", phase="error", error=text, message=text, reason=reason)


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
            if value is not None or key in {"error", "promotion_line", "as_of_dhaka"}:
                current[key] = value
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
