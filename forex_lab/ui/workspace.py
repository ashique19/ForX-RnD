"""Named workspace presets for the Streamlit desk.

Switch watchlist pairs, lab interval/TF, realtime poll, and a min-confidence
display overlay without rewriting ``config/default.yaml`` or the paper journal.

``BrokerPort`` is unchanged. Paper fills stay in ``data/paper_broker.json``.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from forex_lab.paths import resolve_under_root
from forex_lab.ui.watchlist import (
    DEFAULT_REFRESH_SECONDS,
    WatchItem,
    Watchlist,
    WatchlistError,
    load_watchlist,
    normalize_pair,
    save_watchlist,
)

BUILTIN_REL = "config/workspaces"
USER_REL = "data/workspaces"
ACTIVE_STEM = "active"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
RESERVED_NAMES = frozenset({ACTIVE_STEM, "con", "prn", "nul"})
MIN_REFRESH = 60
MAX_REFRESH = 3600


class WorkspaceError(ValueError):
    """Invalid workspace name or payload."""


@dataclass
class WorkspacePaths:
    builtin_dir: Path
    user_dir: Path

    @property
    def active_path(self) -> Path:
        return self.user_dir / f"{ACTIVE_STEM}.yaml"


@dataclass
class Workspace:
    name: str
    label: str | None = None
    pairs: list[str] = field(default_factory=list)
    interval: str | None = None
    refresh_seconds: int | None = None
    min_confidence: float | None = None
    source: str | None = None  # builtin | user
    path: str | None = None

    def display_label(self) -> str:
        return str(self.label or self.name)


def workspace_paths(
    *,
    builtin_dir: str | Path | None = None,
    user_dir: str | Path | None = None,
) -> WorkspacePaths:
    return WorkspacePaths(
        builtin_dir=Path(builtin_dir) if builtin_dir is not None else resolve_under_root(BUILTIN_REL),
        user_dir=Path(user_dir) if user_dir is not None else resolve_under_root(USER_REL),
    )


def normalize_name(raw: str) -> str:
    s = str(raw or "").strip().lower().replace(" ", "_")
    if not NAME_RE.match(s) or s in RESERVED_NAMES:
        raise WorkspaceError(
            f"Expected a workspace slug like 'scalp' or 'swing' (not {raw!r})"
        )
    return s


def _parse_refresh(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    n = int(raw)
    return max(MIN_REFRESH, min(n, MAX_REFRESH))


def _parse_conf(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    v = float(raw)
    if v < 0.0 or v > 1.0:
        raise WorkspaceError(f"min_confidence must be 0..1, got {raw!r}")
    return v


def _parse_pairs(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise WorkspaceError("Workspace pairs must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            pair = normalize_pair(str(item.get("pair") or item.get("symbol") or ""))
        else:
            pair = normalize_pair(str(item))
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


def workspace_from_mapping(
    data: dict[str, Any] | None,
    *,
    path: Path | None = None,
    source: str | None = None,
    name: str | None = None,
) -> Workspace:
    data = data or {}
    raw_name = data.get("name") or name or (path.stem if path is not None else "")
    slug = normalize_name(str(raw_name))
    interval = data.get("interval") or data.get("timeframe") or None
    if interval is not None:
        interval = str(interval).strip() or None
    label = data.get("label")
    if label is not None:
        label = str(label).strip() or None
    return Workspace(
        name=slug,
        label=label,
        pairs=_parse_pairs(data.get("pairs")),
        interval=interval,
        refresh_seconds=_parse_refresh(data.get("refresh_seconds")),
        min_confidence=_parse_conf(data.get("min_confidence")),
        source=source,
        path=str(path) if path else None,
    )


def _read_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        raw = json.loads(text or "null")
    else:
        raw = yaml.safe_load(text)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise WorkspaceError(f"Workspace must be a mapping: {path}")
    return raw


def _candidate_files(directory: Path, name: str) -> list[Path]:
    return [directory / f"{name}{ext}" for ext in (".yaml", ".yml", ".json")]


def _existing_named(directory: Path, name: str) -> Path | None:
    for p in _candidate_files(directory, name):
        if p.is_file():
            return p
    return None


def load_workspace(
    name: str,
    *,
    paths: WorkspacePaths | None = None,
    builtin_dir: str | Path | None = None,
    user_dir: str | Path | None = None,
) -> Workspace:
    """Load a named preset. User dir shadows builtin (config/workspaces)."""
    slug = normalize_name(name)
    paths = paths or workspace_paths(builtin_dir=builtin_dir, user_dir=user_dir)
    user_file = _existing_named(paths.user_dir, slug)
    if user_file is not None:
        return workspace_from_mapping(_read_mapping(user_file), path=user_file, source="user", name=slug)
    builtin_file = _existing_named(paths.builtin_dir, slug)
    if builtin_file is not None:
        return workspace_from_mapping(
            _read_mapping(builtin_file), path=builtin_file, source="builtin", name=slug
        )
    raise WorkspaceError(f"No workspace preset named {slug!r}")


def list_workspaces(
    *,
    paths: WorkspacePaths | None = None,
    builtin_dir: str | Path | None = None,
    user_dir: str | Path | None = None,
) -> list[Workspace]:
    """Builtin first (scalp, swing, …), then extra user-only names."""
    paths = paths or workspace_paths(builtin_dir=builtin_dir, user_dir=user_dir)
    found: dict[str, Workspace] = {}

    def _scan(directory: Path, source: str) -> None:
        if not directory.is_dir():
            return
        for p in sorted(directory.iterdir()):
            if p.suffix.lower() not in {".yaml", ".yml", ".json"}:
                continue
            if p.stem.lower() == ACTIVE_STEM:
                continue
            try:
                slug = normalize_name(p.stem)
            except WorkspaceError:
                continue
            try:
                ws = workspace_from_mapping(_read_mapping(p), path=p, source=source, name=slug)
            except (WorkspaceError, WatchlistError, json.JSONDecodeError, yaml.YAMLError):
                continue
            found[slug] = ws

    _scan(paths.builtin_dir, "builtin")
    _scan(paths.user_dir, "user")
    preferred = ["scalp", "swing"]
    ordered: list[Workspace] = []
    for key in preferred:
        if key in found:
            ordered.append(found.pop(key))
    ordered.extend(found[k] for k in sorted(found))
    return ordered


def save_workspace(
    ws: Workspace,
    *,
    paths: WorkspacePaths | None = None,
    user_dir: str | Path | None = None,
) -> Path:
    """Write a user preset under data/workspaces/. Never writes the paper journal."""
    slug = normalize_name(ws.name)
    ws.name = slug
    paths = paths or workspace_paths(user_dir=user_dir)
    paths.user_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "name": slug,
        "label": ws.label or slug,
        "pairs": list(ws.pairs),
        "interval": ws.interval,
        "refresh_seconds": ws.refresh_seconds,
        "min_confidence": ws.min_confidence,
    }
    dest = paths.user_dir / f"{slug}.yaml"
    text = (
        "# Workspace preset — board view only (pairs / TF / refresh / min conf).\n"
        "# Does not rewrite config/default.yaml, the paper journal, or BrokerPort.\n"
        + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    )
    dest.write_text(text, encoding="utf-8")
    ws.path = str(dest)
    ws.source = "user"
    return dest


def save_active(
    name: str,
    *,
    min_confidence: float | None = None,
    paths: WorkspacePaths | None = None,
    user_dir: str | Path | None = None,
) -> Path:
    slug = normalize_name(name)
    paths = paths or workspace_paths(user_dir=user_dir)
    paths.user_dir.mkdir(parents=True, exist_ok=True)
    payload = {"name": slug, "min_confidence": _parse_conf(min_confidence)}
    text = (
        "# Last applied workspace (name + min-confidence overlay).\n"
        "# Not a broker store. Paper journal is data/paper_broker.json (untouched).\n"
        + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    )
    paths.active_path.write_text(text, encoding="utf-8")
    return paths.active_path


def load_active(
    *,
    paths: WorkspacePaths | None = None,
    user_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    paths = paths or workspace_paths(user_dir=user_dir)
    if not paths.active_path.is_file():
        return None
    try:
        raw = _read_mapping(paths.active_path)
    except (WorkspaceError, json.JSONDecodeError, yaml.YAMLError):
        return None
    name = raw.get("name")
    if not name:
        return None
    try:
        slug = normalize_name(str(name))
    except WorkspaceError:
        return None
    try:
        conf = _parse_conf(raw.get("min_confidence"))
    except WorkspaceError:
        conf = None
    return {"name": slug, "min_confidence": conf}


def resolve_active_workspace(
    *,
    paths: WorkspacePaths | None = None,
    builtin_dir: str | Path | None = None,
    user_dir: str | Path | None = None,
) -> Workspace | None:
    """Named preset plus any min-confidence overlay stored in active.yaml."""
    paths = paths or workspace_paths(builtin_dir=builtin_dir, user_dir=user_dir)
    active = load_active(paths=paths)
    if not active:
        return None
    try:
        ws = load_workspace(active["name"], paths=paths)
    except WorkspaceError:
        ws = Workspace(name=active["name"], source="active")
    if active.get("min_confidence") is not None:
        ws.min_confidence = active["min_confidence"]
    return ws


def capture_current(
    wl: Watchlist,
    *,
    name: str,
    cfg: dict[str, Any] | None = None,
    min_confidence: float | None = None,
    label: str | None = None,
) -> Workspace:
    slug = normalize_name(name)
    interval = wl.interval or (str(cfg.get("interval")) if cfg and cfg.get("interval") else None)
    conf = _parse_conf(min_confidence)
    if conf is None and cfg is not None:
        raw = (cfg.get("signals") or {}).get("min_confidence")
        try:
            conf = _parse_conf(raw)
        except (TypeError, ValueError, WorkspaceError):
            conf = None
    return Workspace(
        name=slug,
        label=label or slug,
        pairs=list(wl.pair_symbols()),
        interval=interval,
        refresh_seconds=_parse_refresh(wl.refresh_seconds) or DEFAULT_REFRESH_SECONDS,
        min_confidence=conf,
        source="user",
    )


def apply_workspace(
    ws: Workspace,
    *,
    watchlist_file: str | Path | None = None,
    paths: WorkspacePaths | None = None,
    cfg: dict[str, Any] | None = None,
) -> Watchlist:
    """Copy preset fields onto the watchlist + active pointer.

    Does **not** read or write the paper journal (``broker.store``) and does
    **not** rewrite ``config/default.yaml``.
    """
    slug = normalize_name(ws.name)
    ws.name = slug
    dest = Path(watchlist_file) if watchlist_file is not None else None
    if dest is not None and dest.exists():
        wl = load_watchlist(dest, cfg=cfg, create=False)
    elif dest is not None:
        wl = Watchlist(pairs=[], refresh_seconds=DEFAULT_REFRESH_SECONDS, interval=None, path=str(dest))
    else:
        wl = load_watchlist(cfg=cfg, create=True)

    items: list[WatchItem] = []
    for pair in ws.pairs:
        try:
            items.append(WatchItem(pair=normalize_pair(pair), interval=None))
        except WatchlistError:
            continue
    wl.pairs = items
    if ws.interval is not None:
        wl.interval = ws.interval
    if ws.refresh_seconds is not None:
        wl.refresh_seconds = int(ws.refresh_seconds)
    save_watchlist(wl, dest if dest is not None else (wl.path or None))
    save_active(slug, min_confidence=ws.min_confidence, paths=paths)
    return wl


def reset_workspace(
    name: str,
    *,
    watchlist_file: str | Path | None = None,
    paths: WorkspacePaths | None = None,
    builtin_dir: str | Path | None = None,
    user_dir: str | Path | None = None,
    cfg: dict[str, Any] | None = None,
) -> Watchlist:
    """Restore a builtin factory file (drop user override) and re-apply.

    Custom-only presets are re-applied from their last saved user file.
    Paper journal is not touched.
    """
    slug = normalize_name(name)
    paths = paths or workspace_paths(builtin_dir=builtin_dir, user_dir=user_dir)
    builtin_file = _existing_named(paths.builtin_dir, slug)
    user_file = _existing_named(paths.user_dir, slug)
    if builtin_file is not None and user_file is not None:
        user_file.unlink()
    ws = load_workspace(slug, paths=paths)
    return apply_workspace(ws, watchlist_file=watchlist_file, paths=paths, cfg=cfg)


def overlay_config(cfg: dict[str, Any] | None, ws: Workspace | None) -> dict[str, Any]:
    """In-memory overlay for interval / realtime / min_confidence.

    Returns a deepcopy. Never writes YAML. Leaves ``broker`` untouched so
    PaperBroker keeps the same store path.
    """
    base = dict(cfg or {})
    if ws is None:
        return base
    out = deepcopy(base)
    if ws.interval:
        out["interval"] = ws.interval
    if ws.min_confidence is not None:
        sig = dict(out.get("signals") or {})
        sig["min_confidence"] = float(ws.min_confidence)
        out["signals"] = sig
    if ws.refresh_seconds is not None:
        board = dict(out.get("board") or {})
        board["realtime_seconds"] = int(ws.refresh_seconds)
        out["board"] = board
    return out


def workspace_to_dict(ws: Workspace) -> dict[str, Any]:
    return asdict(ws)
