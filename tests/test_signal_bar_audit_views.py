"""signal_bar_audit annotations (observe-only) and the explore_sum_r post-fix / closed-bar views."""
import importlib.util
import json
import pathlib

import pytest

from forex_lab import explore_book as eb
from forex_lab import paper_shadow as ps

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mod(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


APPLY = _mod("apply_signal_bar_audit")
ESR = _mod("explore_sum_r")


@pytest.fixture()
def journals(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "explore.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    monkeypatch.setattr(ps, "journal_path", lambda: tmp_path / "journal.jsonl")
    return tmp_path


def _pos(pid, ts, r, pair="EURUSD"):
    eb.append({"event": "open", "ts_utc": ts, "position_id": pid, "pair": pair, "side": "BUY", "entry_mid": 1.1,
               "sl": 1.098, "tp": 1.104})
    if r is not None:
        eb.append({"event": "close", "ts_utc": ts, "position_id": pid, "pair": pair, "side": "BUY", "pnl_r": r,
                   "pnl_price": r * 0.002, "exit_reason": "tp" if r > 0 else "sl"})


def _audit():
    e = lambda pid, v, book="explore": {"book": book, "position_id": pid, "verdict": v, "bar_status": "forming",
                                        "partial_proven": True, "as_used_replay": ["BUY", 0.5],
                                        "closed_bar": ["HOLD", 0.6], "signal_bar_utc": "2026-10-14T09:00Z"}
    return {"entries": [e("a", "would_not_fire"), e("b", "still_fires"), e("c", "unverifiable"), e("m1", "still_fires", "main"),
                        e("ghost", "still_fires")]}


def test_annotations_flags_false_idempotent_totals_unchanged(journals):
    _pos("a", "2026-10-14T10:00:00Z", 2.0); _pos("b", "2026-10-15T10:00:00Z", -1.0); _pos("c", "2026-10-16T10:00:00Z", 0.5)
    ps.append_event({"event": "open", "position_id": "m1", "fill_id": "f1", "pair": "NZDUSD", "side": "BUY",
                     "ts_utc": "2026-10-14T10:00:00Z"})
    before = eb.sum_r()
    ex = APPLY.explore_rows(_audit(), eb.load())
    mn = APPLY.main_rows(_audit(), ps.load_events())
    assert [r["ref_position_id"] for r in ex] == ["a", "b", "c"] and [r["ref_position_id"] for r in mn] == ["m1"]
    for r in ex:
        assert r["exclude_from_explore"] is False and r["exclude_from_stage2"] is False
        assert r["annotation_type"] == "signal_bar_audit" and r["audit_version"] == "partial_bar_audit_v1"
        assert "corrected_pnl_r" not in r
    assert ex[0]["closed_bar_raw"] == "HOLD" and ex[0]["as_used_conf"] == 0.5
    assert mn[0]["ref_fill_id"] == "f1" and mn[0]["exclude_from_stage2"] is False
    for r in ex:
        eb.append(r)
    for r in mn:
        ps.append_event(r)
    assert eb.sum_r() == before
    assert ps.excluded_refs(ps.load_events()) == (set(), set())
    assert APPLY.explore_rows(_audit(), eb.load()) == [] and APPLY.main_rows(_audit(), ps.load_events()) == []


def test_apply_cli_dry_run_writes_nothing(journals, tmp_path):
    _pos("a", "2026-10-14T10:00:00Z", 2.0)
    p = tmp_path / "audit.json"; p.write_text(json.dumps(_audit()), encoding="utf-8")
    n = len(eb.load())
    assert APPLY.main(["--from-json", str(p)]) == 0 and len(eb.load()) == n
    assert APPLY.main(["--from-json", str(p), "--apply"]) == 0 and len(eb.load()) == n + 1


def test_sum_r_views_default_unchanged_and_new_keys_last(journals):
    _pos("a", "2026-10-14T10:00:00Z", 2.0); _pos("b", "2026-10-15T10:00:00Z", -1.0)
    _pos("p", "2026-10-19T15:00:00Z", 0.5); _pos("q", "2026-10-19T14:00:00Z", 1.0)
    for r in APPLY.explore_rows(_audit(), eb.load()):
        eb.append(r)
    ev = eb.load()
    rep = ESR.report(ev)
    keys = list(rep)
    assert keys[-2:] == ["post_fix_only", "closed_bar_view"]
    assert keys[:-2] == list(eb.sum_r(ev)) + ["open", "raw", "pre_reconcile", "pre_fill_booking", "reconciled"]
    assert rep["sum_r"] == rep["reconciled"]["sum_r"] == pytest.approx(2.5)
    assert rep["post_fix_only"]["sum_r"] == pytest.approx(0.5) and rep["post_fix_only"]["entries"] == 1
    cb = rep["closed_bar_view"]
    assert cb["sum_r"] == pytest.approx(0.5) and cb["excluded_would_not_fire"] == 1 and cb["closed_counted"] == 3


def test_sum_r_output_first_lines_parse_like_before(journals, capsys):
    _pos("a", "2026-10-14T10:00:00Z", 2.0)
    text = json.dumps(ESR.report(eb.load()), indent=2)
    assert text.splitlines()[1].startswith('  "sum_r": ') and json.loads(text)["sum_r"] == 2.0