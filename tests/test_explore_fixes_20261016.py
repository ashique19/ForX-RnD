from datetime import datetime, timezone
import importlib.util, pathlib, sys

from forex_lab import explore_book as eb

_spec = importlib.util.spec_from_file_location("rig", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "refresh_if_gap.py")
rig = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(rig)


def _row(bar, pair="EURUSD", raw="BUY", **kw):
    r = {"pair": pair, "signal": "HOLD", "raw_signal": raw, "confidence": 0.55, "gate_reason": "conf=0.55 < min 0.60",
         "validity": "OK", "status": "ready", "muted": False, "last": 1.1000, "stop": 1.0980, "target": 1.1020,
         "horizon_bars": 8, "last_bar_dhaka": bar, "last_signal_dhaka": bar}
    r.update(kw); return r


def test_same_signal_bar_not_reentered(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "e.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    now = datetime(2026, 10, 15, 13, 10, tzinfo=timezone.utc)
    for _ in range(3):
        eb.run_pass([_row("2026-10-15 19:00:00 Asia/Dhaka")], now=now)
    assert eb.status()["fills"] == 1
    dec = [e for e in eb.load() if e["event"] == "decision"]
    assert dec[-1]["reason"] == "signal_bar_already_entered"
    eb.run_pass([_row("2026-10-15 20:00:00 Asia/Dhaka")], now=now)  # new bar stacks
    assert eb.status()["fills"] == 2
    eb.run_pass([_row("2026-10-15 20:00:00 Asia/Dhaka", raw="SELL", stop=1.1020, target=1.0980)], now=now)
    assert eb.status()["fills"] == 3  # other direction is a different signal


def test_recomputed_barrier_rebased_on_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "e.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_BARS", "off")
    fn = lambda pair, side: {"sl": 1.0970, "tp": 1.1030, "horizon_bars": 8, "entry": 1.1000}
    eb.run_pass([_row("b1", stop=None, target=None, last=1.1010)], barrier_fn=fn)
    o = [e for e in eb.load() if e["event"] == "open"][0]
    assert abs((o["entry_mid"] - o["sl"]) - (o["tp"] - o["entry_mid"])) < 1e-9
    assert abs(o["sl"] - 1.0980) < 1e-9


def test_rebase_event_moves_paper_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(tmp_path / "e.jsonl"))
    eb.append({"event": "open", "position_id": "x", "pair": "EURUSD", "side": "BUY", "entry_mid": 1.1, "sl": 1.098, "tp": 1.102})
    eb.append({"event": "rebase", "position_id": "x", "entry_mid": 1.1001, "sl": 1.0981, "tp": 1.1021, "basis": "mt5_fill"})
    p = eb.open_positions(eb.load())["x"]
    assert p["entry_mid"] == 1.1001 and p["sl"] == 1.0981 and p["entry_basis"] == "mt5_fill"


def test_sum_r_single_definition():
    ev = [
        {"event": "close", "position_id": "a", "pnl_r": -5.0},
        {"event": "close", "position_id": "b", "pnl_r": 1.0},
        {"event": "close", "position_id": "b", "pnl_r": 1.0},  # duplicate close ignored
        {"event": "close", "position_id": "c", "pnl_r": 2.0},
        {"event": "annotation", "ref_position_id": "a", "corrected_pnl_r": -1.0},
        {"event": "annotation", "ref_position_id": "c", "exclude_from_explore": True},
    ]
    s = eb.sum_r(ev)
    assert s["sum_r"] == 0.0 and s["closed_counted"] == 2 and s["annotated"] == 1
    assert eb.status(ev)["sum_r"] == 0.0


def test_refresh_gap_rule():
    now = datetime(2026, 10, 15, 14, 5, tzinfo=timezone.utc)  # latest closed bar = 13:00Z
    rows = [{"pair": "EURUSD", "last_bar_dhaka": "2026-10-15 19:00:00 Asia/Dhaka"},  # 13:00Z ok
            {"pair": "GBPUSD", "last_bar_dhaka": "2026-10-15 18:00:00 Asia/Dhaka"},  # 12:00Z -> 1h gap
            {"pair": "USDJPY", "last_bar_dhaka": None}]
    g = rig.gap_pairs(rows, now)
    assert [x["pair"] for x in g] == ["GBPUSD", "USDJPY"] and g[0]["closed_hours_missing"] == 1
    assert not rig.fx_open(datetime(2026, 10, 17, 12, tzinfo=timezone.utc))
