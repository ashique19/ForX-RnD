"""Aggregate stored learnings for the Decision desk.

Reads artifacts the lab already writes. Missing files are omitted.
Nothing here is synthesized, scored, or backfilled.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from forex_lab.clock import fmt_display, parse_ts, timezone_name
from forex_lab.digest import DEFAULT_PERSIST, digest_cfg
from forex_lab.paths import project_root, resolve_under_root
from forex_lab.retrain import retrain_cfg
from forex_lab.score import journal_aggregates, normalize_outcome
from forex_lab.ui.alerts import persist_path

SOURCE_PAPER = "paper"
SOURCE_DIGEST = "digest"
SOURCE_MODEL = "model"
SOURCE_AWARENESS = "awareness"
SOURCES = (SOURCE_PAPER, SOURCE_DIGEST, SOURCE_MODEL, SOURCE_AWARENESS)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Standing reminders from journal_aggregates, not a new observation.
_NOTE_SKIP_PREFIXES = (
    "Paper lookback only",
    "If a live backend",
    "No scored (RIGHT/WRONG)",
    "News remains context only",
)

DEFAULT_REPORTS: tuple[Path, ...] = (project_root() / "reports" / "gate_screen.md",)


def learnings_payload(
    limit: int = DEFAULT_LIMIT,
    cfg: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """JSON for GET /learnings. Env overrides match the rest of the desk."""
    from api.deskdata import app_config

    base = dict(cfg if cfg is not None else app_config())
    return collect_learnings(_apply_env(base), limit=limit, now=now)


def collect_learnings(
    cfg: dict[str, Any] | None = None,
    *,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
    reports: Sequence[Path] | None = None,
) -> dict[str, Any]:
    cfg = cfg if cfg is not None else {}
    clock = _as_utc(now or datetime.now(timezone.utc))
    cap = _clamp_limit(limit)
    report_paths = tuple(DEFAULT_REPORTS if reports is None else reports)

    items: list[dict[str, Any]] = []
    feeds: list[dict[str, Any]] = []

    awareness, awareness_feed, flip_keys = _awareness_items(cfg)
    items.extend(awareness)
    feeds.append(awareness_feed)

    paper, paper_feed = _paper_items(cfg)
    items.extend(paper)
    feeds.append(paper_feed)

    digest, digest_feed = _digest_items(cfg, flip_keys)
    items.extend(digest)
    feeds.append(digest_feed)

    retrain, retrain_feed = _retrain_items(cfg)
    items.extend(retrain)
    feeds.append(retrain_feed)

    gates, gate_feed = _gate_items(report_paths, cfg)
    items.extend(gates)
    feeds.append(gate_feed)

    items.sort(key=lambda row: (-float(row["_ts"]), int(row["_seq"])))
    total = len(items)
    trimmed = items[:cap]
    for row in trimmed:
        row.pop("_ts", None)
        row.pop("_seq", None)

    latest = trimmed[0] if trimmed else None
    return {
        "timezone": timezone_name(cfg),
        "generated_at": clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_dhaka": fmt_display(clock, cfg, seconds=True),
        "count": len(trimmed),
        "total": total,
        "limit": cap,
        "latest_at": None if latest is None else latest["at"],
        "latest_at_dhaka": None if latest is None else latest["at_dhaka"],
        "items": trimmed,
        "feeds": feeds,
    }


def _apply_env(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    paper = os.environ.get("FORX_PAPER_STORE")
    if paper:
        broker = dict(out.get("broker") or {})
        broker["store"] = paper
        out["broker"] = broker
    alert = os.environ.get("FORX_ALERT_STATE")
    if alert:
        board = dict(out.get("board") or {})
        alerts = dict(board.get("alerts") or {})
        alerts["persist_file"] = alert
        board["alerts"] = alerts
        out["board"] = board
    return out


def _clamp_limit(limit: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, n))


def _as_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _hid(*parts: object) -> str:
    raw = "\n".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _clip(text: str, n: int) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= n:
        return clean
    return clean[: n - 1].rstrip() + "…"


def _feed(
    feed_id: str,
    label: str,
    source: str,
    path: Path,
    *,
    present: bool,
    count: int,
    error: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": feed_id,
        "label": label,
        "source": source,
        "path": str(path),
        "present": bool(present),
        "count": int(count),
    }
    if error:
        row["error"] = error
    return row


class _Seq:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> int:
        self.n += 1
        return self.n


def _stamp(
    *,
    cfg: dict[str, Any],
    seq: _Seq,
    ident: str,
    title: str,
    detail: str,
    at: datetime,
    source: str,
) -> dict[str, Any]:
    clock = _as_utc(at)
    return {
        "id": ident,
        "title": _clip(title, 140),
        "detail": _clip(detail, 360),
        "at": clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "at_dhaka": fmt_display(clock, cfg, seconds=True),
        "source": source,
        "_ts": clock.timestamp(),
        "_seq": seq.next(),
    }


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return None, None
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return None, f"unreadable ({exc})"


def _paper_path(cfg: dict[str, Any]) -> Path:
    rel = str((cfg.get("broker") or {}).get("store") or "data/paper_broker.json")
    return resolve_under_root(rel)


def _paper_items(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _paper_path(cfg)
    raw, err = _read_json(path)
    present = path.exists() and path.stat().st_size > 0 if path.exists() else False
    if err or not isinstance(raw, dict):
        return [], _feed("paper", "Paper", SOURCE_PAPER, path, present=present, count=0, error=err)
    closed = [row for row in (raw.get("closed") or []) if isinstance(row, dict)]
    open_rows = [row for row in (raw.get("positions") or []) if isinstance(row, dict)]
    fallback = _mtime(path)
    seq = _Seq()
    items: list[dict[str, Any]] = []
    latest: datetime | None = None
    for row in closed:
        outcome = normalize_outcome(row)
        if outcome not in {"RIGHT", "WRONG", "FLAT"}:
            continue
        at = parse_ts(row.get("exit_time") or row.get("entry_time") or row.get("time")) or fallback
        if at is None:
            continue
        at = _as_utc(at)
        if latest is None or at > latest:
            latest = at
        pair = str(row.get("pair") or "?").upper()
        side = str(row.get("side") or "").upper()
        title = f"{pair} {side} paper {outcome}".strip()
        items.append(
            _stamp(
                cfg=cfg,
                seq=seq,
                ident=_hid("paper", row.get("id"), pair, side, outcome, row.get("exit_time")),
                title=title,
                detail=_paper_detail(row, outcome),
                at=at,
                source=SOURCE_PAPER,
            )
        )
    if latest is not None:
        for note in _scoring_notes(closed, open_rows):
            items.append(
                _stamp(
                    cfg=cfg,
                    seq=seq,
                    ident=_hid("paper-note", note),
                    title="Scoring note",
                    detail=note,
                    at=latest,
                    source=SOURCE_PAPER,
                )
            )
    return items, _feed("paper", "Paper", SOURCE_PAPER, path, present=present, count=len(items))


def _paper_detail(row: Mapping[str, Any], outcome: str) -> str:
    reason = str(row.get("exit_reason") or "").strip()
    bits = [f"Closed on {reason}" if reason else f"Closed {outcome}"]
    session = str(row.get("session") or "").strip()
    if session and session.lower() != "n/a":
        bits.append(f"session {session}")
    validity = str(row.get("validity_at_entry") or "").strip()
    if validity:
        bits.append(f"entry validity {validity}")
    bucket = str(row.get("conf_bucket") or "").strip()
    if bucket and bucket.lower() != "n/a":
        bits.append(f"conf {bucket}")
    realized = row.get("realized")
    try:
        if realized is not None and realized != "":
            bits.append(f"signed return {float(realized):+.4f}")
    except (TypeError, ValueError):
        pass
    return " · ".join(bits)


def _scoring_notes(
    closed: list[dict[str, Any]],
    open_rows: list[dict[str, Any]],
) -> list[str]:
    notes = list(journal_aggregates(closed, open_rows).get("notes") or [])
    out: list[str] = []
    for note in notes:
        text = " ".join(str(note).split())
        if not text or text.startswith(_NOTE_SKIP_PREFIXES):
            continue
        out.append(text)
    return out


def _digest_path(cfg: dict[str, Any]) -> Path:
    rel = digest_cfg(cfg).get("persist_file") or DEFAULT_PERSIST
    return resolve_under_root(rel)


def _digest_items(
    cfg: dict[str, Any],
    flip_keys: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = _digest_path(cfg)
    raw, err = _read_json(path)
    present = bool(path.exists() and path.stat().st_size > 0) if path.exists() else False
    if err or not isinstance(raw, dict):
        return [], _feed("digest", "Digest", SOURCE_DIGEST, path, present=present, count=0, error=err)
    at = parse_ts(raw.get("generated_at")) or _mtime(path)
    if at is None:
        return [], _feed("digest", "Digest", SOURCE_DIGEST, path, present=True, count=0, error="no timestamp")
    at = _as_utc(at)
    paper = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}
    flips = [row for row in (raw.get("flips") or []) if isinstance(row, dict)]
    awareness = raw.get("awareness") if isinstance(raw.get("awareness"), dict) else {}
    freshness = [row for row in (raw.get("freshness") or []) if isinstance(row, dict)]
    parts: list[str] = []
    try:
        n_scored = int(paper.get("n_scored") or 0)
    except (TypeError, ValueError):
        n_scored = 0
    if n_scored:
        parts.append(f"{paper.get('right', 0)} RIGHT / {paper.get('wrong', 0)} WRONG in the digest window")
    if flips:
        parts.append(f"{len(flips)} signal flip" + ("" if len(flips) == 1 else "s"))
    try:
        n_issues = int(awareness.get("n_unhealthy") or 0)
    except (TypeError, ValueError):
        n_issues = 0
    if n_issues:
        parts.append(f"{n_issues} awareness issue" + ("" if n_issues == 1 else "s"))
    weak = [
        row
        for row in freshness
        if str(row.get("validity") or "").upper() not in {"", "OK", "CLOSED"}
    ]
    if weak and not parts:
        parts.append(f"{len(weak)} pair" + ("" if len(weak) == 1 else "s") + " not OK on freshness")
    seq = _Seq()
    items: list[dict[str, Any]] = []
    if parts:
        items.append(
            _stamp(
                cfg=cfg,
                seq=seq,
                ident=_hid("digest", raw.get("generated_at"), "|".join(parts)),
                title="Daily digest",
                detail=". ".join(parts) + ". Research snapshot — not a live edge.",
                at=at,
                source=SOURCE_DIGEST,
            )
        )
    for flip in flips:
        key = _flip_key(flip.get("pair"), flip.get("from"), flip.get("to"))
        if key and key in flip_keys:
            continue
        when = parse_ts(flip.get("when")) or at
        pair = str(flip.get("pair") or "?").upper()
        frm = str(flip.get("from") or "").upper()
        to = str(flip.get("to") or "").upper()
        title = f"{pair} flipped {frm} → {to}".strip()
        detail = str(flip.get("message") or "").strip() or title
        items.append(
            _stamp(
                cfg=cfg,
                seq=seq,
                ident=_hid("digest-flip", pair, frm, to, flip.get("when")),
                title=title,
                detail=detail,
                at=when,
                source=SOURCE_DIGEST,
            )
        )
    return items, _feed("digest", "Digest", SOURCE_DIGEST, path, present=True, count=len(items))


def _flip_key(pair: object, frm: object, to: object) -> str:
    left = str(pair or "").upper().strip()
    a = str(frm or "").upper().strip()
    b = str(to or "").upper().strip()
    if not left or not a or not b:
        return ""
    return f"{left}|{a}|{b}"


def _retrain_store(cfg: dict[str, Any]) -> Path:
    rel = str(retrain_cfg(cfg).get("store") or "data/champion")
    return resolve_under_root(rel)


def _retrain_items(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    store = _retrain_store(cfg)
    if not store.is_dir():
        return [], _feed("retrain", "Retrain", SOURCE_MODEL, store, present=False, count=0)
    seq = _Seq()
    items: list[dict[str, Any]] = []
    covered: set[str] = set()
    errors: list[str] = []
    for path in sorted(store.glob("*_history.jsonl")):
        pair = path.name[: -len("_history.jsonl")].upper()
        covered.add(pair)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        fallback = _mtime(path)
        for idx, line in enumerate(lines):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                item = _retrain_row(cfg, seq, pair, row, fallback=fallback, idx=idx)
                if item:
                    items.append(item)
    for path in sorted(store.glob("*.json")):
        name = path.name
        if name.endswith("_challenger.json"):
            pair = name[: -len("_challenger.json")].upper()
            kind = "challenger"
        else:
            pair = path.stem.upper()
            kind = "champion"
        if pair in covered:
            continue
        raw, err = _read_json(path)
        if err:
            errors.append(f"{name}: {err}")
            continue
        if not isinstance(raw, dict):
            continue
        item = _retrain_row(cfg, seq, pair, raw, fallback=_mtime(path), idx=0, kind=kind)
        if item:
            items.append(item)
            covered.add(pair)
    err = "; ".join(errors) if errors else None
    return items, _feed(
        "retrain",
        "Retrain",
        SOURCE_MODEL,
        store,
        present=True,
        count=len(items),
        error=err,
    )


def _retrain_row(
    cfg: dict[str, Any],
    seq: _Seq,
    pair: str,
    row: Mapping[str, Any],
    *,
    fallback: datetime | None,
    idx: int,
    kind: str = "history",
) -> dict[str, Any] | None:
    verdict = str(row.get("verdict") or "").strip()
    if not verdict and kind == "champion":
        verdict = str(row.get("status") or "champion")
    if not verdict:
        return None
    at = parse_ts(row.get("promoted_at") or row.get("promoted_at_display") or row.get("at")) or fallback
    if at is None:
        return None
    title = f"{pair} retrain {verdict}"
    return _stamp(
        cfg=cfg,
        seq=seq,
        ident=_hid("retrain", pair, kind, verdict, row.get("promoted_at"), idx),
        title=title,
        detail=_retrain_detail(row, verdict),
        at=at,
        source=SOURCE_MODEL,
    )


def _retrain_detail(row: Mapping[str, Any], verdict: str) -> str:
    bits = [f"verdict {verdict}"]
    if "promote" in row:
        bits.append("promoted" if row.get("promote") else "not promoted")
    deltas = row.get("deltas") if isinstance(row.get("deltas"), dict) else {}
    decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    if not deltas and isinstance(decision.get("deltas"), dict):
        deltas = decision["deltas"]
    for key, label in (("profit_factor", "PF"), ("total_return", "return"), ("max_drawdown", "max DD")):
        if key not in deltas or deltas.get(key) is None:
            continue
        try:
            bits.append(f"{label} {float(deltas[key]):+.4f}")
        except (TypeError, ValueError):
            continue
    reasons = row.get("reasons")
    if not reasons and isinstance(decision.get("reasons"), list):
        reasons = decision["reasons"]
    if isinstance(reasons, (list, tuple)):
        for reason in reasons:
            text = str(reason or "").strip()
            if text:
                bits.append(text)
                break
    if row.get("error"):
        bits.append(str(row["error"]))
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    if metrics and "PF" not in " ".join(bits):
        pf = metrics.get("profit_factor")
        ret = metrics.get("total_return")
        try:
            if pf is not None:
                bits.append(f"PF {float(pf):.4f}")
            if ret is not None:
                bits.append(f"return {float(ret):+.4f}")
        except (TypeError, ValueError):
            pass
    return " · ".join(bits)


def _gate_items(
    paths: Iterable[Path],
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seq = _Seq()
    items: list[dict[str, Any]] = []
    used: list[str] = []
    present = False
    for path in paths:
        used.append(str(path))
        bullets = _gate_bullets(path)
        if not bullets:
            continue
        present = True
        at = _mtime(path)
        if at is None:
            continue
        for idx, (title, detail) in enumerate(bullets):
            items.append(
                _stamp(
                    cfg=cfg,
                    seq=seq,
                    ident=_hid("gate", path.name, idx, title, detail),
                    title=title,
                    detail=detail,
                    at=at,
                    source=SOURCE_MODEL,
                )
            )
    label_path = Path(used[0]) if used else project_root() / "reports" / "gate_screen.md"
    return items, _feed(
        "gate_screen",
        "Edge gates",
        SOURCE_MODEL,
        label_path,
        present=present,
        count=len(items),
    )


def _gate_bullets(path: Path) -> list[tuple[str, str]]:
    """Takeaway lines already written in reports/gate_screen.md."""
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return []
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.startswith("**Takeaway"):
            start = idx + 1
            break
    if start is None:
        return []
    out: list[tuple[str, str]] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Default:"):
            body = stripped[len("Default:") :].strip()
            if body:
                out.append(("Edge gate · default", _plain(body)))
            break
        if stripped.startswith("- "):
            body = stripped[2:].strip()
            label, _rest = _split_emphasis(body)
            title = f"Edge gate · {label}" if label else "Edge gate"
            out.append((title, _plain(body)))
            continue
        if stripped.startswith("#"):
            break
    return out


def _plain(body: str) -> str:
    text = body.replace("**", "").replace("`", "")
    text = re.sub(r"\*+", "", text)
    return " ".join(text.split())


def _split_emphasis(body: str) -> tuple[str, str]:
    if body.startswith("**"):
        end = body.find("**", 2)
        if end > 2:
            label = body[2:end].strip(" :")
            rest = body[end + 2 :].strip()
            return label, rest or label
    return "", body


def _awareness_items(
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], set[str]]:
    path = persist_path(cfg)
    raw, err = _read_json(path)
    present = bool(path.exists() and path.stat().st_size > 0) if path.exists() else False
    if err or not isinstance(raw, dict):
        return [], _feed("awareness", "Awareness", SOURCE_AWARENESS, path, present=present, count=0, error=err), set()
    alerts = [row for row in (raw.get("alerts") or []) if isinstance(row, dict)]
    fallback = _mtime(path)
    seq = _Seq()
    items: list[dict[str, Any]] = []
    keys: set[str] = set()
    for alert in alerts:
        kind = str(alert.get("kind") or "").strip().lower()
        if not kind:
            continue
        at = parse_ts(alert.get("created_at")) or fallback
        if at is None:
            continue
        pair = str(alert.get("pair") or "").upper()
        frm = str(alert.get("from_value") or "")
        to = str(alert.get("to_value") or "")
        message = str(alert.get("message") or "").strip()
        title = _awareness_title(kind, pair, frm, to, message)
        if not title:
            continue
        if kind == "flip":
            key = _flip_key(pair, frm, to)
            if key:
                keys.add(key)
        detail = message or title
        tf = str(alert.get("timeframe") or "").strip()
        if tf and tf.lower() not in detail.lower():
            detail = f"{tf} · {detail}"
        items.append(
            _stamp(
                cfg=cfg,
                seq=seq,
                ident=_hid("awareness", alert.get("id") or kind, pair, frm, to, alert.get("created_at")),
                title=title,
                detail=detail,
                at=at,
                source=SOURCE_AWARENESS,
            )
        )
    return items, _feed(
        "awareness",
        "Awareness",
        SOURCE_AWARENESS,
        path,
        present=True,
        count=len(items),
    ), keys


def _awareness_title(kind: str, pair: str, frm: str, to: str, message: str) -> str:
    who = pair or "Desk"
    if kind == "flip" and frm and to:
        return f"{who} flipped {frm} → {to}"
    if kind == "stale":
        return f"{who} data STALE"
    if kind == "missing":
        return f"{who} data MISSING"
    if kind == "event":
        return message or f"{who} event window"
    return message or f"{who} {kind}"
