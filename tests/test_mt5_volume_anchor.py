"""Frozen MT5 volume anchor (2026-10-20 fix for the 2026-10-13 21:00 UTC volume-scale drift)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forex_lab import data as D
from forex_lab import mt5_volume_anchor as VA

ROOT = Path(__file__).resolve().parents[1]


def _bars(start, n, vol, px=1.10):
    idx = pd.date_range(start, periods=n, freq="1h")
    c = px + np.cumsum(np.full(n, 1e-4))
    return pd.DataFrame({"Open": c, "High": c + 2e-4, "Low": c - 2e-4, "Close": c, "Volume": vol}, index=idx)


def _cfg(tmp_path):
    return {"interval": "1h", "paths": {"data_dir": str(tmp_path)}}


def _write_cache(cfg, df):
    df = df.copy(); df.index.name = "Datetime"; df.to_csv(D.data_path("EURUSD", cfg, "1h"))


def _anchor(tmp_path, factor):
    VA.write_anchors(tmp_path, {"EURUSD": {"factor": factor}})


def test_anchor_used_even_with_short_overlap(tmp_path):
    """The drift case: 47 overlap bars (< 50) used to fall back to raw tick volume (1.0)."""
    cfg = _cfg(tmp_path)
    full = _bars("2026-09-01", 400, 400.0)
    _write_cache(cfg, full.iloc[:353])
    mt5 = full.iloc[306:].copy(); mt5["Volume"] = 1000.0  # 47-bar overlap
    _anchor(tmp_path, 0.4)
    df, src = D.try_mt5_refresh("EURUSD", cfg, interval="1h", fetch=lambda p, n: mt5)
    assert src == D.SOURCE_MT5
    assert (df["Volume"].iloc[-60:] == 400.0).all()
    meta = json.loads(D.data_path("EURUSD", cfg, "1h").with_suffix(".mt5.json").read_text(encoding="utf-8"))
    assert meta["volume_scale_source"] == "anchor" and meta["volume_ratio_cache_over_mt5"] == 0.4
    assert meta["ratio_overlap_bars"] == 47


def test_short_overlap_without_anchor_refuses_and_writes_nothing(tmp_path):
    cfg = _cfg(tmp_path)
    full = _bars("2026-09-01", 400, 400.0)
    _write_cache(cfg, full.iloc[:353])
    path = D.data_path("EURUSD", cfg, "1h")
    before = path.read_bytes()
    mt5 = full.iloc[306:].copy(); mt5["Volume"] = 1000.0
    df, tag = D.try_mt5_refresh("EURUSD", cfg, interval="1h", fetch=lambda p, n: mt5)
    assert df is None and tag.startswith("mt5 guard:") and "volume scale unknown" in tag
    assert path.read_bytes() == before
    # and the live chain does not fall back to another provider after an mt5 guard refusal
    assert tag.startswith("mt5 guard:")


def test_anchor_not_rederived_from_mt5_written_cache(tmp_path):
    """A cache already at raw MT5 scale (ratio 1.0, big overlap) must not pull the factor to 1.0."""
    cfg = _cfg(tmp_path)
    full = _bars("2026-09-01", 400, 1000.0)
    _write_cache(cfg, full.iloc[:300])
    mt5 = full.iloc[100:].copy()
    _anchor(tmp_path, 0.35)
    df, _ = D.try_mt5_refresh("EURUSD", cfg, interval="1h", fetch=lambda p, n: mt5)
    assert df["Volume"].iloc[-1] == 350.0
    scale = D.resolve_mt5_volume_scale("EURUSD", cfg, full.iloc[:300], mt5)
    assert scale[0] == 0.35 and scale[2] == "anchor" and scale[3] == pytest.approx(1.0)


def test_no_anchor_long_overlap_keeps_legacy_median(tmp_path):
    cfg = _cfg(tmp_path)
    full = _bars("2026-09-01", 400, 400.0)
    mt5 = full.copy(); mt5["Volume"] = 1000.0
    assert D.resolve_mt5_volume_scale("EURUSD", cfg, full.iloc[:300], mt5)[:3] == (pytest.approx(0.4), 300, "overlap_median")


def test_invalid_anchor_ignored(tmp_path):
    (tmp_path / VA.ANCHOR_FILE).write_text(json.dumps({"pairs": {"EURUSD": {"factor": 0}}}), encoding="utf-8")
    assert VA.load_anchor("EURUSD", tmp_path) is None
    (tmp_path / VA.ANCHOR_FILE).write_text("{not json", encoding="utf-8")
    assert VA.load_anchor("EURUSD", tmp_path) is None


def test_write_anchors_refuses_overwrite_without_force(tmp_path):
    VA.write_anchors(tmp_path, {"EURUSD": {"factor": 0.3}})
    with pytest.raises(ValueError):
        VA.write_anchors(tmp_path, {"EURUSD": {"factor": 0.9}})
    assert VA.load_anchor("EURUSD", tmp_path)["factor"] == 0.3
    VA.write_anchors(tmp_path, {"EURUSD": {"factor": 0.9}}, force=True)
    assert VA.load_anchor("EURUSD", tmp_path)["factor"] == 0.9


def test_compute_anchor_last_window_and_rejects_mt5_written_snapshot():
    mt5 = _bars("2026-06-01", 800, 1000.0)
    snap = mt5.copy()
    snap[["Open", "High", "Low", "Close"]] += 1e-5  # pre-MT5 source: different OHLC
    snap["Volume"] = [300.0] * 300 + [400.0] * 500
    rec = VA.compute_anchor(snap, mt5)
    assert rec["factor"] == pytest.approx(0.4) and rec["overlap_bars"] == 500
    assert rec["all_overlap_median"] == pytest.approx(0.4)
    with pytest.raises(ValueError, match="not pre-MT5"):
        VA.compute_anchor(mt5.assign(Volume=400.0), mt5)


def test_rewrite_volume_only_touches_volume_since_seam():
    mt5 = _bars("2026-10-01", 300, 1000.0)
    cache = mt5.copy(); cache["Volume"] = 350.0
    seam = mt5.index[200]
    cache.loc[cache.index >= seam, "Volume"] = 1000.0  # drifted raw tick volume
    out, rep = VA.rewrite_volume(cache, mt5, 0.35, seam)
    assert rep["bars"] == 100 and rep["changed"] == 100
    assert rep["implied_factor_before"] == 1.0 and rep["implied_factor_after"] == 0.35
    assert (out["Volume"] == 350.0).all()
    for c in ("Open", "High", "Low", "Close"):
        assert out[c].equals(cache[c])
    assert out.index.equals(cache.index)
    s = VA.seam_medians(out, seam, days=3)
    assert s["after_over_before"] == 1.0


def test_rewrite_volume_refuses_on_ohlc_mismatch_or_missing_bar():
    mt5 = _bars("2026-10-01", 300, 1000.0)
    cache = mt5.copy()
    cache.iloc[250, cache.columns.get_loc("Close")] += 1e-4
    with pytest.raises(ValueError, match="differ from MT5 OHLC"):
        VA.rewrite_volume(cache, mt5, 0.35, mt5.index[200])
    with pytest.raises(ValueError, match="missing from MT5"):
        VA.rewrite_volume(mt5.copy(), mt5.iloc[:-5], 0.35, mt5.index[200])


def _script():
    spec = importlib.util.spec_from_file_location("mva", ROOT / "scripts" / "mt5_volume_anchor.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_script_rewrite_dry_run_writes_nothing_and_apply_keeps_ohlc(tmp_path):
    mod = _script()
    mt5 = _bars("2026-10-01", 300, 1000.0)
    cache = mt5.copy(); cache["Volume"] = 350.0
    cache.loc[cache.index >= mt5.index[200], "Volume"] = 1000.0
    cache.index.name = "Datetime"; cache.to_csv(tmp_path / "EURUSD_1h.csv")
    cache = mod._read(tmp_path / "EURUSD_1h.csv")
    VA.write_anchors(tmp_path, {"EURUSD": {"factor": 0.35}})
    before = (tmp_path / "EURUSD_1h.csv").read_bytes()
    res = mod.rewrite(["EURUSD", "GBPUSD"], tmp_path, str(mt5.index[200]), apply=False, fetch=lambda p, n: mt5)
    assert (tmp_path / "EURUSD_1h.csv").read_bytes() == before
    assert res["GBPUSD"] == {"skipped": "cache csv missing"} and res["EURUSD"]["changed"] == 100
    res = mod.rewrite(["EURUSD"], tmp_path, str(mt5.index[200]), apply=True, fetch=lambda p, n: mt5)
    assert res["EURUSD"]["ohlc_matches_mt5_after_write"] is True and res["EURUSD"]["ohlc_text_unchanged"] is True
    after = mod._read(tmp_path / "EURUSD_1h.csv")
    assert (after["Volume"] == 350.0).all()
    assert np.allclose(after[["Open", "High", "Low", "Close"]].values, cache[["Open", "High", "Low", "Close"]].values, rtol=0, atol=0)