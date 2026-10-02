"""Read-only Stage-4 risk-envelope drift checker.

The checker compares the committed freeze snapshot with config/default.yaml.
It never rewrites YAML and is intentionally fail-soft: callers receive a
report even when a file is missing or malformed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - fail-soft for minimal environments
    yaml = None


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = REPO_ROOT / "_RISK_ENVELOPE_FREEZE_20261002.json"
DEFAULT_YAML = REPO_ROOT / "config" / "default.yaml"


def _get_path(data: Any, dotted: str) -> Any:
    value = data
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


class _Missing:
    pass


_MISSING = _Missing()


def load_snapshot(path: str | Path = DEFAULT_SNAPSHOT) -> dict[str, Any]:
    """Load the committed JSON freeze snapshot."""
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("yaml"), dict):
        raise ValueError("freeze snapshot must contain a yaml object")
    return payload


def load_live_yaml(path: str | Path = DEFAULT_YAML) -> dict[str, Any]:
    """Load current YAML without changing it."""
    if yaml is None:
        raise RuntimeError("PyYAML unavailable")
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError("live YAML root must be a mapping")
    return payload


def drift_report(
    snapshot_path: str | Path = DEFAULT_SNAPSHOT,
    yaml_path: str | Path = DEFAULT_YAML,
) -> dict[str, Any]:
    """Return a fail-soft report of frozen-key drift."""
    try:
        snapshot = load_snapshot(snapshot_path)
        live = load_live_yaml(yaml_path)
    except Exception as exc:  # fail-soft by design
        return {"ok": False, "checked": 0, "drift": [], "error": str(exc)}

    drift: list[dict[str, Any]] = []
    for key, expected in snapshot["yaml"].items():
        actual = _get_path(live, key)
        if actual is _MISSING or actual != expected:
            drift.append({"key": key, "expected": expected,
                          "actual": None if actual is _MISSING else actual})
    return {
        "ok": not drift,
        "checked": len(snapshot["yaml"]),
        "drift": drift,
        "error": None,
        "snapshot": str(snapshot_path),
        "yaml": str(yaml_path),
    }


def format_report(report: dict[str, Any]) -> str:
    """Render a short operator-facing report."""
    if report.get("error"):
        return f"WARN risk envelope check unavailable: {report['error']}"
    if report.get("ok"):
        return f"OK risk envelope: {report.get('checked', 0)} frozen YAML keys match"
    lines = [
        f"WARN risk envelope drift: {len(report.get('drift', []))} key(s) differ",
    ]
    for item in report.get("drift", []):
        lines.append(
            f"  {item['key']}: expected={item['expected']!r} actual={item['actual']!r}"
        )
    return "\n".join(lines)
