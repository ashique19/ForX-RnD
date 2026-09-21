"""Project path helpers."""
from __future__ import annotations

from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_under_root(rel: str | Path) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    return project_root() / p
