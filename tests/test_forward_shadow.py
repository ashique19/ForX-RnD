"""Forward shadow tracker: idempotency, catch-up, no lookahead, no backfill (seeded random-walk fixture, test-only)."""
import numpy as np
import pandas as pd
import pytest

from forex_lab import forward_shadow as fs

SWAPS = {p: {"swap_long": -1.0, "swap_short": 0.5} for p in fs.PAIRS}


def _daily(n=200, seed=0, end="2026-10-20"):
    closes = pd.bdate_range(end=end, periods=n) + pd.Timedelta(hours=21)
    out = {}
    for i, p in enumerate(fs.PAIRS):
        rng = np.random.default_rng(seed + i)
        base = 150.0 if p.endswith("JPY") else 1.1
        c = base * np.exp(np.cumsum(rng.normal(0, 0.006, n)))
        o = np.r_[c[0], c[:-1]]
        df = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.002, "Low": np.minimum(o, c) * 0.998, "Close": c,
                           "open_time": closes - pd.Timedelta(hours=24)}, index=closes)
        df.loc[df.index.dayofweek == 0, "open_time"] = df.index[df.index.dayofweek == 0] - pd.Timedelta(hours=71)
        out[p] = df
    return out


def _rates():
    idx = pd.date_range("2025-01-01", "2027-01-01", freq="D")
    return pd.DataFrame({"USD": 4.0, "EUR": 2.0, "JPY": 0.5, "GBP": 4.5, "AUD": 3.6, "NZD": 3.0, "CAD": 2.5, "CHF": 0.0}, index=idx)


def _run(journal, daily, now):
    rows = fs.step(journal, daily, _rates(), SWAPS, "test", now)
    keys = {r["key"] for r in journal}
    return journal + [r for r in rows if r["key"] not in keys], rows


def _strip(rows):
    return [{k: v for k, v in r.items() if k not in ("run_utc", "schema")} for r in rows]


def _upto(daily, t):
    return {p: d[d.index <= t] for p, d in daily.items()}


def test_init_records_state_only_and_rerun_is_noop():
    full = _daily(); t_init = pd.Timestamp("2026-09-01 23:00")
    j, rows = _run([], _upto(full, t_init), t_init.tz_localize("UTC"))
    assert [r["event"] for r in rows] == ["init"] and set(rows[0]["day0_mom120_targets"]) == set(fs.PAIRS)
    j2, rows2 = _run(j, _upto(full, t_init), t_init.tz_localize("UTC"))
    assert rows2 == [] and j2 == j  # nothing new before the first forward close


def test_no_backfill_before_init_and_fills_at_next_open():
    full = _daily(); t_init = pd.Timestamp("2026-09-01 23:00")
    j, _ = _run([], _upto(full, t_init), t_init.tz_localize("UTC"))
    j, _ = _run(j, full, pd.Timestamp("2026-10-21", tz="UTC"))
    for r in j:
        if r["event"] in ("decision", "mark", "day"):
            assert fs._ts(r["bar_close_utc"]) > t_init
    for r in [r for r in j if r["event"] in ("fill_entry", "fill_exit")]:
        d = full[r["pair"]]
        px = r["entry"] if r["event"] == "fill_entry" else r["exit"]
        hit = d[d["open_time"] == pd.Timestamp(r["fill_time_utc"]).tz_localize(None)]
        assert len(hit) == 1 and hit["Open"].iloc[0] == px  # a real bar Open, never invented
        dec = next(x for x in j if x["key"] == r["decision_key"])
        assert hit.index[0] > fs._ts(dec["bar_close_utc"])  # filled at the bar after the decision
    assert any(r["event"] == "fill_entry" for r in j)


def test_catch_up_equals_daily_runs_and_is_idempotent():
    full = _daily(); t_init = pd.Timestamp("2026-09-01 23:00")
    j0, _ = _run([], _upto(full, t_init), t_init.tz_localize("UTC"))
    # A: one run after a long outage
    jA, _ = _run(j0, full, pd.Timestamp("2026-10-21", tz="UTC"))
    # B: a run every day
    jB = j0
    for t in full["EURUSD"].index[full["EURUSD"].index > t_init]:
        jB, _ = _run(jB, _upto(full, t + pd.Timedelta(hours=2)), (t + pd.Timedelta(hours=2)).tz_localize("UTC"))
    assert sorted(_strip(jA), key=lambda r: r["key"]) == sorted(_strip(jB), key=lambda r: r["key"])
    jC, rowsC = _run(jA, full, pd.Timestamp("2026-10-21", tz="UTC"))
    assert rowsC == [] and len(jC) == len(jA)
    assert len({r["key"] for r in jA}) == len(jA)


def test_decisions_do_not_use_future_bars():
    full = _daily(); t_init = pd.Timestamp("2026-09-01 23:00")
    j0, _ = _run([], _upto(full, t_init), t_init.tz_localize("UTC"))
    j, _ = _run(j0, full, pd.Timestamp("2026-10-21", tz="UTC"))
    for r in [r for r in j if r["event"] == "decision" and r["rule"] == "MOM120"]:
        D = pd.Timestamp(r["bar_close_utc"]).tz_localize(None)
        assert fs.mom_target(full[r["pair"]][full[r["pair"]].index <= D], D) == r["target"]


def test_waits_for_missing_pair_then_proceeds():
    full = _daily(); t_init = pd.Timestamp("2026-10-01 23:00")
    j0, _ = _run([], _upto(full, t_init), t_init.tz_localize("UTC"))
    last = full["EURUSD"].index[-1]
    part = {p: (d[d.index < last] if p == "GBPJPY" else d) for p, d in full.items()}
    j1, _ = _run(j0, part, pd.Timestamp("2026-10-21", tz="UTC"))
    days = [r["bar_close_utc"] for r in j1 if r["event"] == "day" and r["rule"] == "MOM120"]
    assert fs._iso(last) not in days  # waits (pair missing, < 3 days)
    j2, _ = _run(j1, full, pd.Timestamp("2026-10-21", tz="UTC"))
    assert fs._iso(last) in [r["bar_close_utc"] for r in j2 if r["event"] == "day" and r["rule"] == "MOM120"]


def test_carry_decides_only_on_fridays_and_top3():
    full = _daily(); t_init = pd.Timestamp("2026-09-01 23:00")
    j0, _ = _run([], _upto(full, t_init), t_init.tz_localize("UTC"))
    j, _ = _run(j0, full, pd.Timestamp("2026-10-21", tz="UTC"))
    decs = [r for r in j if r["event"] == "decision" and r["rule"] == "CARRY3"]
    assert decs and all(pd.Timestamp(r["bar_close_utc"]).dayofweek == 4 for r in decs)
    sel = fs.carry_selection(_rates(), pd.Timestamp("2026-10-16 21:00"))
    assert sum(1 for v in sel.values() if v) == 3


def test_swap_increment_wednesday_triple():
    inc, n, nights = fs.swap_increment("EURUSD", 1, "2026-10-19 21:00", "2026-10-26 21:00", SWAPS)
    assert n == 5 and nights == 7 and inc == pytest.approx(7 * -1.0 / 100 / 360)


def test_append_dedups(tmp_path, monkeypatch):
    p = tmp_path / "j.jsonl"
    assert fs.append([{"key": "a", "event": "x"}], p) == 1
    assert fs.append([{"key": "a", "event": "x"}, {"key": "b", "event": "y"}], p) == 1
    assert [r["key"] for r in fs.load(p)] == ["a", "b"]
