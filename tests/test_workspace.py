"""Workspace preset load/save — board view only; paper journal stays put."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from forex_lab.config_loader import load_config
from forex_lab.ui.watchlist import WatchItem, Watchlist, load_watchlist, save_watchlist
from forex_lab.ui.workspace import (
    Workspace,
    WorkspaceError,
    apply_workspace,
    capture_current,
    list_workspaces,
    load_active,
    load_workspace,
    normalize_name,
    overlay_config,
    reset_workspace,
    resolve_active_workspace,
    save_active,
    save_workspace,
    workspace_from_mapping,
    workspace_paths,
)


def _dirs(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    return workspace_paths(builtin_dir=builtin, user_dir=user)


def _write_preset(directory: Path, name: str, **fields) -> Path:
    payload = {
        "name": name,
        "label": fields.get("label", name.title()),
        "pairs": fields.get("pairs", ["EURUSD"]),
        "interval": fields.get("interval", "15m"),
        "refresh_seconds": fields.get("refresh_seconds", 60),
        "min_confidence": fields.get("min_confidence", 0.45),
    }
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_normalize_name_accepts_slugs():
    assert normalize_name("Scalp") == "scalp"
    assert normalize_name("my-desk") == "my-desk"
    assert normalize_name(" swing ") == "swing"


def test_normalize_name_rejects_junk():
    with pytest.raises(WorkspaceError):
        normalize_name("../etc")
    with pytest.raises(WorkspaceError):
        normalize_name("active")
    with pytest.raises(WorkspaceError):
        normalize_name("")
    with pytest.raises(WorkspaceError):
        normalize_name("has space and / slash")


def test_workspace_roundtrip_save_load(tmp_path):
    paths = _dirs(tmp_path)
    ws = Workspace(
        name="custom",
        label="Custom desk",
        pairs=["EURUSD", "GBPUSD"],
        interval="15m",
        refresh_seconds=90,
        min_confidence=0.50,
    )
    saved = save_workspace(ws, paths=paths)
    assert saved.exists()
    text = saved.read_text(encoding="utf-8")
    assert "EURUSD" in text
    assert "paper" in text.lower() or "BrokerPort" in text or "board view" in text.lower()
    loaded = load_workspace("custom", paths=paths)
    assert loaded.pairs == ["EURUSD", "GBPUSD"]
    assert loaded.interval == "15m"
    assert loaded.refresh_seconds == 90
    assert loaded.min_confidence == pytest.approx(0.50)
    assert loaded.source == "user"
    assert loaded.name == "custom"


def test_user_preset_shadows_builtin(tmp_path):
    paths = _dirs(tmp_path)
    _write_preset(paths.builtin_dir, "scalp", pairs=["EURUSD"], interval="15m")
    _write_preset(paths.user_dir, "scalp", pairs=["GBPUSD"], interval="1h", min_confidence=0.55)
    loaded = load_workspace("scalp", paths=paths)
    assert loaded.pairs == ["GBPUSD"]
    assert loaded.interval == "1h"
    assert loaded.source == "user"


def test_json_preset_loads(tmp_path):
    paths = _dirs(tmp_path)
    dest = paths.user_dir / "night.json"
    dest.write_text(
        '{"name":"night","pairs":["USDJPY"],"interval":"1h","refresh_seconds":180,'
        '"min_confidence":0.35}',
        encoding="utf-8",
    )
    loaded = load_workspace("night", paths=paths)
    assert loaded.pairs == ["USDJPY"]
    assert loaded.refresh_seconds == 180
    assert loaded.min_confidence == pytest.approx(0.35)


def test_committed_scalp_and_swing_exist():
    names = {w.name: w for w in list_workspaces()}
    assert "scalp" in names
    assert "swing" in names
    scalp = load_workspace("scalp")
    swing = load_workspace("swing")
    assert scalp.source == "builtin"
    assert swing.source == "builtin"
    assert scalp.interval == "15m"
    assert swing.interval == "1h"
    assert "EURUSD" in scalp.pairs
    assert "EURUSD" in swing.pairs
    assert scalp.refresh_seconds == 60
    assert swing.refresh_seconds == 120
    assert scalp.min_confidence == pytest.approx(0.45)
    assert swing.min_confidence == pytest.approx(0.40)


def test_list_workspaces_orders_scalp_swing_then_custom(tmp_path):
    paths = _dirs(tmp_path)
    _write_preset(paths.builtin_dir, "swing", pairs=["EURUSD"], interval="1h")
    _write_preset(paths.builtin_dir, "scalp", pairs=["EURUSD"], interval="15m")
    _write_preset(paths.user_dir, "zebra", pairs=["AUDUSD"], interval="1h")
    listed = list_workspaces(paths=paths)
    assert [w.name for w in listed] == ["scalp", "swing", "zebra"]


def test_apply_and_reset_watchlist(tmp_path):
    paths = _dirs(tmp_path)
    _write_preset(
        paths.builtin_dir,
        "scalp",
        pairs=["EURUSD", "GBPUSD", "USDJPY"],
        interval="15m",
        refresh_seconds=60,
        min_confidence=0.45,
    )
    watch = tmp_path / "watchlist.yaml"
    save_watchlist(
        Watchlist(pairs=[WatchItem("EURUSD")], refresh_seconds=60, interval=None),
        watch,
    )
    ws = load_workspace("scalp", paths=paths)
    applied = apply_workspace(ws, watchlist_file=watch, paths=paths)
    assert applied.pair_symbols() == ["EURUSD", "GBPUSD", "USDJPY"]
    assert applied.interval == "15m"
    assert applied.refresh_seconds == 60
    again = load_watchlist(watch)
    assert again.pair_symbols() == ["EURUSD", "GBPUSD", "USDJPY"]
    active = load_active(paths=paths)
    assert active is not None
    assert active["name"] == "scalp"
    assert active["min_confidence"] == pytest.approx(0.45)

    # User override, then reset restores factory pairs.
    _write_preset(
        paths.user_dir,
        "scalp",
        pairs=["AUDUSD"],
        interval="4h",
        refresh_seconds=120,
        min_confidence=0.60,
    )
    dirty = load_workspace("scalp", paths=paths)
    apply_workspace(dirty, watchlist_file=watch, paths=paths)
    assert load_watchlist(watch).pair_symbols() == ["AUDUSD"]
    reset_workspace("scalp", watchlist_file=watch, paths=paths)
    restored = load_watchlist(watch)
    assert restored.pair_symbols() == ["EURUSD", "GBPUSD", "USDJPY"]
    assert restored.interval == "15m"
    assert not (paths.user_dir / "scalp.yaml").exists()


def test_capture_current_then_save(tmp_path):
    paths = _dirs(tmp_path)
    wl = Watchlist(
        pairs=[WatchItem("EURUSD"), WatchItem("USDJPY")],
        refresh_seconds=75,
        interval="4h",
    )
    cfg = {"interval": "1h", "signals": {"min_confidence": 0.40}}
    ws = capture_current(wl, name="custom", cfg=cfg, min_confidence=0.55)
    assert ws.pairs == ["EURUSD", "USDJPY"]
    assert ws.interval == "4h"
    assert ws.refresh_seconds == 75
    assert ws.min_confidence == pytest.approx(0.55)
    save_workspace(ws, paths=paths)
    loaded = load_workspace("custom", paths=paths)
    assert loaded.pairs == ["EURUSD", "USDJPY"]


def test_overlay_skips_none_fields():
    cfg = {
        "interval": "1h",
        "signals": {"min_confidence": 0.40},
        "broker": {"backend": "paper", "store": "data/paper_broker.json"},
    }
    over = overlay_config(cfg, Workspace(name="session", min_confidence=0.51))
    assert over["interval"] == "1h"
    assert over["signals"]["min_confidence"] == pytest.approx(0.51)
    assert over.get("board") is None or "realtime_seconds" not in (over.get("board") or {})
    assert over["broker"]["store"] == "data/paper_broker.json"


def test_overlay_config_does_not_write_default_or_broker(tmp_path):
    default = tmp_path / "default.yaml"
    default.write_text(
        "interval: 1h\n"
        "signals:\n  min_confidence: 0.40\n"
        "board:\n  realtime_seconds: 60\n"
        "broker:\n  backend: paper\n  store: data/paper_broker.json\n",
        encoding="utf-8",
    )
    original = default.read_text(encoding="utf-8")
    cfg = yaml.safe_load(original)
    ws = Workspace(
        name="scalp",
        pairs=["EURUSD"],
        interval="15m",
        refresh_seconds=90,
        min_confidence=0.45,
    )
    over = overlay_config(cfg, ws)
    assert over["interval"] == "15m"
    assert over["signals"]["min_confidence"] == pytest.approx(0.45)
    assert over["board"]["realtime_seconds"] == 90
    assert over["broker"]["backend"] == "paper"
    assert over["broker"]["store"] == "data/paper_broker.json"
    assert default.read_text(encoding="utf-8") == original
    # original cfg mapping is not mutated
    assert cfg["interval"] == "1h"
    assert cfg["signals"]["min_confidence"] == pytest.approx(0.40)


def test_apply_preset_does_not_wipe_paper_journal(tmp_path):
    paths = _dirs(tmp_path)
    _write_preset(
        paths.builtin_dir,
        "swing",
        pairs=["EURUSD", "AUDUSD"],
        interval="1h",
        refresh_seconds=120,
        min_confidence=0.40,
    )
    paper = tmp_path / "paper_broker.json"
    journal = (
        '{"positions":[{"id":"p1","pair":"EURUSD","side":"BUY","size":1.0}],'
        '"fills":[{"id":"f1","kind":"open","pair":"EURUSD"}],'
        '"closed":[]}\n'
    )
    paper.write_text(journal, encoding="utf-8")
    mtime = paper.stat().st_mtime
    watch = tmp_path / "watchlist.yaml"
    save_watchlist(Watchlist(pairs=[WatchItem("GBPUSD")], refresh_seconds=60), watch)
    cfg = {
        "interval": "1h",
        "signals": {"min_confidence": 0.40},
        "broker": {"backend": "paper", "store": str(paper)},
        "pairs": {"EURUSD": "EURUSD=X"},
    }
    ws = load_workspace("swing", paths=paths)
    apply_workspace(ws, watchlist_file=watch, paths=paths, cfg=cfg)
    assert paper.read_text(encoding="utf-8") == journal
    assert paper.stat().st_mtime == mtime
    assert load_watchlist(watch).pair_symbols() == ["EURUSD", "AUDUSD"]
    # Switching again still leaves the journal alone.
    apply_workspace(ws, watchlist_file=watch, paths=paths, cfg=cfg)
    assert paper.read_text(encoding="utf-8") == journal


def test_resolve_active_applies_min_confidence_override(tmp_path):
    paths = _dirs(tmp_path)
    _write_preset(paths.builtin_dir, "scalp", pairs=["EURUSD"], min_confidence=0.45)
    save_active("scalp", min_confidence=0.62, paths=paths)
    resolved = resolve_active_workspace(paths=paths)
    assert resolved is not None
    assert resolved.name == "scalp"
    assert resolved.min_confidence == pytest.approx(0.62)
    assert resolved.pairs == ["EURUSD"]


def test_workspace_from_mapping_dedupes_pairs():
    ws = workspace_from_mapping(
        {"name": "scalp", "pairs": ["eur/usd", "EURUSD", {"pair": "GBPUSD"}]}
    )
    assert ws.pairs == ["EURUSD", "GBPUSD"]


def test_streamlit_workspace_controls_render_without_touching_paper(monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    from forex_lab.calendar import CalendarBundle
    from forex_lab.news import NewsBundle
    from forex_lab.paths import project_root

    def _stub_news(pair, cfg=None, **_kwargs):
        return NewsBundle(pair=str(pair).upper(), bias="unclear", headlines=[], fetched_at="2026-09-21 00:00 UTC")

    def _stub_cal(cfg=None, **_kwargs):
        return CalendarBundle(events=[], source="fixture", fetched_at="2026-09-21 00:00 UTC")

    monkeypatch.setattr("forex_lab.news.fetch_pair_news", _stub_news)
    monkeypatch.setattr("forex_lab.calendar.fetch_calendar", _stub_cal)

    paper = project_root() / "data" / "paper_broker.json"
    before = paper.read_text(encoding="utf-8") if paper.exists() else None
    watch = project_root() / "config" / "watchlist.yaml"
    watch_before = watch.read_text(encoding="utf-8")
    default_yaml = project_root() / "config" / "default.yaml"
    default_before = default_yaml.read_text(encoding="utf-8")

    at = AppTest.from_file(str(project_root() / "streamlit_app.py"), default_timeout=60)
    try:
        at.run()
        assert not at.exception, f"Streamlit render failed: {at.exception}"
        labels = [str(getattr(b, "label", "")) for b in at.button]
        assert "Apply" in labels
        assert "Reset" in labels
        assert "Save current" in labels
        selects = [str(getattr(s, "label", "")) for s in at.selectbox]
        assert any("Workspace" in s for s in selects)

        apply_btn = next(b for b in at.button if str(getattr(b, "label", "")) == "Apply")
        apply_btn.click()
        at.run()
        assert not at.exception, f"Apply failed: {at.exception}"

        # Applying a preset writes the watchlist (pairs/TF) but must not touch paper or default.yaml.
        assert default_yaml.read_text(encoding="utf-8") == default_before
        if before is None:
            assert not paper.exists()
        else:
            assert paper.read_text(encoding="utf-8") == before
        watch_after = watch.read_text(encoding="utf-8")
        assert "GBPUSD" in watch_after
        assert "15m" in watch_after
    finally:
        watch.write_text(watch_before, encoding="utf-8")
        active = project_root() / "data" / "workspaces" / "active.yaml"
        if active.exists():
            active.unlink()


def test_committed_default_config_broker_untouched():
    cfg = load_config()
    assert (cfg.get("broker") or {}).get("backend") == "paper"
    over = overlay_config(cfg, load_workspace("scalp"))
    assert over["broker"]["backend"] == "paper"
    assert over["broker"]["store"] == cfg["broker"]["store"]
    assert over["ui"]["timezone"] == "Asia/Dhaka"
    assert cfg["interval"] == "1h"
    assert cfg["signals"]["min_confidence"] == pytest.approx(0.40)
