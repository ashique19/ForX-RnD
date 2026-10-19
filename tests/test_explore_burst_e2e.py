"""End-to-end explore burst main() with a mocked desk API and mocked MT5, plus a name-shadowing guard."""
import ast
import importlib.util
import json
import pathlib
from datetime import datetime

import pytest

from forex_lab import explore_book as eb
from forex_lab import mt5_feed
from test_explore_fill_booking import FillMT5

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_burst_{name}", SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _board_row(pair="EURUSD", bar="2026-10-19 15:00:00 Asia/Dhaka"):
    return {"pair": pair, "signal": "HOLD", "raw_signal": "BUY", "confidence": 0.55, "gate_reason": "conf=0.55 < min 0.60",
            "validity": "OK", "status": "ready", "muted": False, "last": 1.10006, "stop": 1.0980, "target": 1.1040,
            "horizon_bars": 8, "last_bar_dhaka": bar, "last_signal_dhaka": bar}


@pytest.fixture()
def burst(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "explore.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    monkeypatch.delenv("FORX_MT5_EXEC", raising=False)
    mod = _load("explore_paper_burst")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(mod, "_now_dhaka", lambda: datetime(2026, 10, 19, 15, 5, tzinfo=mod.DHAKA))  # Monday
    board = {"rows": [_board_row()]}
    monkeypatch.setattr(mod, "http_json", lambda base, m, p: (200, {"ok": True}) if p == "/health" else (200, board))
    fake = FillMT5()
    monkeypatch.setattr(mt5_feed, "connect", lambda require_demo=True: {"is_demo": True})
    monkeypatch.setattr(mt5_feed, "_mt5", lambda: fake)
    monkeypatch.setattr(mt5_feed, "resolve_symbols", lambda pairs=None: {p: p + ".sml" for p in (pairs or [])})
    return mod, fake, board


def test_burst_main_opens_and_mirrors_in_one_pass(burst, capsys):
    mod, fake, _ = burst
    assert mod.main([]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["ok"] is True and not res.get("step_errors")
    ev = eb.load()
    opens = [e for e in ev if e["event"] == "open"]
    mopens = [e for e in ev if e["event"] == "mt5_open" and e.get("ok")]
    assert len(opens) == 1 and [m["position_id"] for m in mopens] == [opens[0]["position_id"]]
    assert len(fake.positions) == 1 and res["status"]["open"] == 1


def test_burst_post_pass_failure_still_mirrors(burst, capsys, monkeypatch):
    mod, fake, _ = burst

    def boom():
        raise TypeError("'int' object is not callable")

    monkeypatch.setattr(mod, "book_status", boom)
    assert mod.main([]) == 4
    res = json.loads(capsys.readouterr().out)
    assert res["step_errors"][0]["step"] == "status"
    assert [e for e in eb.load() if e["event"] == "mt5_open" and e.get("ok")]  # no orphan


def test_burst_halt_file_stops_everything(burst, capsys):
    mod, fake, _ = burst
    mod.HALT.write_text("", encoding="utf-8")
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out)["halted"] is True and eb.load() == [] and fake.sent == []


def _shadowed(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imported |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            imported.add(node.name)
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        args = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id in imported:
                bad.append(f"{path.name}:{n.lineno} {fn.name}() assigns {n.id}")
        bad += [f"{path.name}:{fn.lineno} {fn.name}() arg {a}" for a in args & imported]
    return bad


@pytest.mark.parametrize("name", ["explore_paper_burst", "explore_sum_r", "explore_broker_reconcile"])
def test_no_shadowing_of_module_level_names(name):
    assert _shadowed(SCRIPTS / f"{name}.py") == []


def test_shadow_checker_catches_the_927a856_bug(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("from m import status\n\ndef main():\n    status, h = 1, 2\n    return status()\n", encoding="utf-8")
    assert _shadowed(p)


def test_excluded_no_demo_fill_is_not_open():
    ev = [{"event": "open", "position_id": "a", "pair": "EURUSD", "side": "BUY"},
          {"event": "open", "position_id": "b", "pair": "EURUSD", "side": "BUY"},
          {"event": "annotation", "annotation_type": "excluded_no_demo_fill", "ref_position_id": "a",
           "exclude_from_explore": True}]
    assert list(eb.open_positions(ev)) == ["b"] and eb.sum_r(ev)["closed_counted"] == 0
