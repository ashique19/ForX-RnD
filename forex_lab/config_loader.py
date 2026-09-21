"""YAML config loading."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forex_lab.paths import project_root


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else project_root() / "config" / "default.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {cfg_path}")
    return data


def pair_to_ticker(pair: str, cfg: dict[str, Any]) -> str:
    key = pair.upper().replace("/", "").replace("=", "")
    if key.endswith("X") and len(key) == 7:
        key = key[:-1]
    pairs = cfg.get("pairs") or {}
    if key in pairs:
        return str(pairs[key])
    if len(key) == 6 and key.isalpha():
        return f"{key}=X"
    raise KeyError(f"Unknown pair '{pair}'. Known: {list(pairs)}")


def pip_size_for_pair(pair: str, cfg: dict[str, Any]) -> float:
    p = pair.upper().replace("/", "")
    if "JPY" in p:
        return 0.01
    return float(cfg.get("pip_size", 0.0001))
