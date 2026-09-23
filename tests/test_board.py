"""Watch-board row building (research UI; no broker calls)."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from forex_lab.config_loader import load_config
from forex_lab.data import generate_synthetic_ohlcv, try_yfinance_refresh
from forex_lab.ui.board import (
    BOARD_TABLE_COLS,
    NEED_FETCH_TRAIN,
    PAPER_STALE_CAPTION,
    attach_next_event,
    board_table,
    build_board_row,
    build_board_rows,
    conf_label,
    paper_actions_label,
    paper_submit_allowed,
    paper_submit_block_reason,
    paper_submit_risk_defaults,
    research_risk,
    research_target,
    row_from_signal,
    spark_ascii,
    sparkline_closes,
)
from forex_lab.ui.quote import quote_from_ohlcv
from forex_lab.ui.watchlist import WatchItem, Watchlist


def _tmp_cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "pairs": {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X"},
        "interval": "1h",
        "period": "1mo",
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "entry_timing": "next_open",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
        "model": {"type": "xgboost"},
        "paths": {
            "data_dir": str(tmp_path / "data"),
            "models_dir": str(tmp_path / "models"),
            "signals_dir": str(tmp_path / "signals"),
            "reports_dir": str(tmp_path / "reports"),
        },
    }
    cfg.update(extra)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "models").mkdir(exist_ok=True)
    return cfg


def test_research_target_triple_barrier_uses_last_close_proxy():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    cfg = load_config()
    compact, note = research_target(df, cfg, "BUY")
    assert compact.startswith("TP ")
    assert "SL " in compact
    assert "proxy" in note.lower()
    assert "next_open" in note or "next open" in note.lower()
    hold, _ = research_target(df, cfg, "HOLD")
    assert hold.startswith("upper ")


def test_research_target_other_scheme_is_na():
    df = generate_synthetic_ohlcv(bars=40, seed=1)
    compact, note = research_target(df, {"label_scheme": "forward_return", "horizon": 4}, "BUY")
    assert compact.startswith("n/a")
    assert "forward_return" in compact
    assert "horizon=4" in compact
    assert "barrier" in note.lower()


def test_row_from_signal_fills_table_fields():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    last = {
        "datetime": "2024-01-02 15:00:00",
        "signal": "SELL",
        "raw_signal": "SELL",
        "confidence": 0.51,
        "dir_edge": 0.22,
        "p_buy": 0.20,
        "p_sell": 0.42,
        "p_hold": 0.38,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, load_config())
    assert row.buy_sell == "SELL"
    assert row.timeframe == "1h"
    assert row.status == "ready"
    assert "TP " in row.target
    assert "conf=0.5100" in row.signal_details
    assert "xgboost" in row.signal_details
    table = board_table([row])
    assert list(table.columns) == BOARD_TABLE_COLS
    assert table.iloc[0]["Signal"] == "SELL"
    assert table.iloc[0]["TF"] == "1h"
    assert table.iloc[0]["Conf"] == "51%"
    assert table.iloc[0]["Data●"] in {"OK", "CLOSED", "STALE", "MISSING", "ERROR"}
    assert table.iloc[0]["Last/mid"] not in {"", "n/a"}
    assert table.iloc[0]["Spread"].endswith("p")
    assert table.iloc[0]["Session"] in {
        "ASIA",
        "LONDON",
        "NY",
        "LONDON+NY",
        "ASIA+LONDON",
        "ASIA+NY",
        "ASIA+LONDON+NY",
        "CLOSED",
        "OFF",
    }
    assert table.iloc[0]["Next event"] == "—"
    assert table.iloc[0]["Spark"]
    assert table.iloc[0]["Actions"] == "BUY/SELL"
    assert row.quote is not None and row.quote.available
    assert row.quote.bid is None and row.quote.ask is None
    assert row.quote.mid == row.quote.last
    assert "yfinance" in row.quote.note.lower()
    assert "not broker" in row.quote.note.lower()
    assert row.session is not None
    assert row.risk is not None and row.risk.available
    assert row.risk.sl is not None and row.risk.tp is not None
    assert row.risk.rr == 1.0
    assert "next-open" in row.risk.entry_ref
    assert row.sparkline
    assert len(row.sparkline) <= 48


def test_research_risk_hold_and_stale_are_na():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    cfg = {
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "entry_timing": "next_open",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 1.0},
        "spread_pips": 1.0,
    }
    hold = research_risk(df, cfg, "HOLD", validity="OK")
    assert hold.available is False
    assert "HOLD" in hold.reason
    stale = research_risk(df, cfg, "BUY", validity="STALE")
    assert stale.available is False
    assert "stale" in stale.reason.lower()
    buy = research_risk(df, cfg, "BUY", validity="OK")
    assert buy.available
    assert buy.rr == 2.0
    assert buy.tp > buy.entry > buy.sl
    sell = research_risk(df, cfg, "SELL", validity="OK")
    assert sell.tp < sell.entry < sell.sl


def test_sparkline_from_cache_empty_when_no_bars():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    pts = sparkline_closes(df, n=24)
    assert 2 <= len(pts) <= 24
    assert sparkline_closes(None) == []
    assert sparkline_closes(pd.DataFrame()) == []


def test_quote_last_mid_ish_and_config_spread_not_bid_ask():
    df = generate_synthetic_ohlcv(bars=40, seed=3)
    cfg = {"spread_pips": 1.5, "pip_size": 0.0001, "board": {"quote": {"bar_range_proxy": True}}}
    q = quote_from_ohlcv(df, "EURUSD", cfg)
    assert q.available
    assert q.last == float(df["Close"].iloc[-1])
    assert q.mid == q.last
    assert q.bid is None and q.ask is None
    assert q.kind == "last/mid-ish"
    assert q.spread_pips == 1.5
    assert q.spread_price == pytest.approx(1.5 * 0.0001)
    assert q.range_pips is not None and q.range_pips > 0
    assert "not a bid/ask" in q.range_note.lower()
    assert "not broker bid/ask" in q.note.lower()


def test_quote_jpy_digits_and_pip_and_optional_mid_from_bid_ask():
    df = generate_synthetic_ohlcv(pair="USDJPY", bars=30, seed=2)
    q = quote_from_ohlcv(df, "USDJPY", {"spread_pips": 1.0})
    assert q.digits == 3
    assert q.pip_size == 0.01
    assert q.spread_price == pytest.approx(0.01)
    labeled = df.copy()
    last = float(labeled["Close"].iloc[-1])
    labeled["Bid"] = last - 0.01
    labeled["Ask"] = last + 0.01
    mid = quote_from_ohlcv(labeled, "USDJPY", {"spread_pips": 1.0})
    assert mid.kind == "mid"
    assert mid.bid is not None and mid.ask is not None
    assert mid.mid == pytest.approx((mid.bid + mid.ask) / 2.0)
    empty = quote_from_ohlcv(None, "EURUSD", {"spread_pips": 2.0})
    assert empty.available is False
    assert empty.spread_pips == 2.0
    assert empty.as_table_last() == "n/a"


def test_missing_cache_row_has_empty_sparkline_and_na_risk(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    row = build_board_row("GBPUSD", cfg, refresh_data=False)
    assert row.buy_sell == "—"
    assert row.target == "n/a"
    assert NEED_FETCH_TRAIN in row.signal_details
    assert row.status == "need_fetch"
    assert row.sparkline == []
    assert row.risk is not None and row.risk.available is False
    assert "no usable data" in (row.risk.reason or "").lower() or "missing" in (row.sparkline_note or "").lower()


def test_refresh_skips_yfinance_when_model_missing(tmp_path, monkeypatch):
    called = []

    def _boom(*_a, **_k):
        called.append(True)
        return None, "should not run"

    monkeypatch.setattr("forex_lab.ui.board.try_yfinance_refresh", _boom)
    cfg = _tmp_cfg(tmp_path)
    generate_synthetic_ohlcv(pair="GBPUSD", bars=120, seed=1).to_csv(
        tmp_path / "data" / "GBPUSD_1h.csv"
    )
    row = build_board_row("GBPUSD", cfg, refresh_data=True, regenerate=True)
    assert called == []
    assert row.status == "need_train"
    assert NEED_FETCH_TRAIN in row.signal_details


def test_incremental_refresh_merges_and_does_not_write_synthetic(tmp_path, monkeypatch):
    cfg = _tmp_cfg(tmp_path)
    csv = tmp_path / "data" / "EURUSD_1h.csv"
    old = generate_synthetic_ohlcv(pair="EURUSD", bars=120, seed=1)
    old.to_csv(csv)
    before = csv.read_text(encoding="utf-8")

    fake = types.ModuleType("yfinance")

    def _download(*_a, **_k):
        raise RuntimeError("429 Too Many Requests")

    fake.download = _download
    monkeypatch.setitem(sys.modules, "yfinance", fake)

    df, reason = try_yfinance_refresh("EURUSD", cfg, interval="1h", incremental=True)
    assert df is None
    assert "rate limited" in reason.lower() or "429" in reason
    assert csv.read_text(encoding="utf-8") == before


def test_yfinance_refresh_does_not_write_synthetic(tmp_path, monkeypatch):
    cfg = _tmp_cfg(tmp_path)
    csv = tmp_path / "data" / "EURUSD_1h.csv"
    original = "Datetime,Open,High,Low,Close,Volume\n2024-01-01,1.1,1.2,1.0,1.15,1\n"
    csv.write_text(original, encoding="utf-8")

    fake = types.ModuleType("yfinance")

    def _download(*_a, **_k):
        raise RuntimeError("network down")

    fake.download = _download
    monkeypatch.setitem(sys.modules, "yfinance", fake)

    df, reason = try_yfinance_refresh("EURUSD", cfg, interval="1h")
    assert df is None
    assert "failed" in reason.lower() or "yfinance" in reason.lower()
    assert csv.read_text(encoding="utf-8") == original
    assert "synthetic" not in csv.read_text(encoding="utf-8").lower()


def test_board_row_eurusd_uses_existing_signals_csv():
    row = build_board_row("EURUSD", refresh_data=False, regenerate=False)
    assert row.pair == "EURUSD"
    assert row.timeframe == "1h"
    assert row.validity in {"OK", "CLOSED", "STALE", "MISSING", "ERROR"}
    if row.validity in {"OK", "CLOSED"}:
        assert row.status in {"ready", "stale"}
        assert row.buy_sell in {"BUY", "SELL", "HOLD"}
        assert "conf=" in row.signal_details
        assert row.model
        assert row.rationale
        assert row.explain_method
    elif row.validity == "STALE":
        assert row.buy_sell == "—"
        assert "stale" in row.signal_details.lower()
    assert row.last_bar_at
    assert row.target != ""
    assert row.quote is not None
    assert row.session is not None
    if row.quote.available:
        assert "last/mid-ish" in row.quote.kind
        assert row.quote.bid is None
    if row.last_bar_at and row.last_bar_at != "n/a":
        assert "Asia/Dhaka" in row.last_bar_at


def test_board_row_session_follows_clock_not_last_bar():
    from datetime import datetime

    overlap = build_board_row(
        "EURUSD",
        refresh_data=False,
        regenerate=False,
        now=datetime(2026, 9, 21, 14, 30, 0),
    )
    assert overlap.session is not None
    assert overlap.session.badge() == "LONDON+NY"
    weekend = build_board_row(
        "EURUSD",
        refresh_data=False,
        regenerate=False,
        now=datetime(2026, 9, 19, 12, 0, 0),
    )
    assert weekend.session is not None
    assert weekend.session.badge() == "CLOSED"

    from zoneinfo import ZoneInfo

    dhaka = ZoneInfo("Asia/Dhaka")
    # Same instant as 11:00 UTC London, shown as 17:00 Dhaka on the desk.
    london_from_dhaka = build_board_row(
        "EURUSD",
        refresh_data=False,
        regenerate=False,
        now=datetime(2026, 9, 21, 17, 0, tzinfo=dhaka),
    )
    assert london_from_dhaka.session is not None
    assert london_from_dhaka.session.badge() == "LONDON"
    assert london_from_dhaka.session.hour_utc == 11.0


def test_build_board_rows_mixed_status(tmp_path):
    cfg = _tmp_cfg(tmp_path)
    wl = Watchlist(pairs=[WatchItem("GBPUSD"), WatchItem("AUDUSD")])
    rows = build_board_rows(wl, cfg, refresh_data=False)
    assert len(rows) == 2
    assert all(r.status == "need_fetch" for r in rows)
    table = board_table(rows)
    assert list(table["Pair"]) == ["GBPUSD", "AUDUSD"]
    assert list(table.columns) == BOARD_TABLE_COLS
    assert "Last/mid" in table.columns and "Spread" in table.columns and "Session" in table.columns
    assert "Next event" in table.columns and "Spark" in table.columns
    assert "disabled" in table.iloc[0]["Actions"]


def test_paper_submit_gate_blocks_stale_missing_error():
    assert paper_submit_allowed("OK") is True
    assert paper_submit_allowed("CLOSED") is True
    assert paper_submit_allowed("STALE") is False
    assert paper_submit_allowed("MISSING") is False
    assert paper_submit_allowed("ERROR") is False
    assert "STALE" in paper_submit_block_reason("STALE")
    assert "MISSING" in paper_submit_block_reason("MISSING")
    assert "refresh" in paper_submit_block_reason("STALE").lower()
    assert "disabled" in PAPER_STALE_CAPTION.lower()
    assert "STALE" in PAPER_STALE_CAPTION


def test_spark_ascii_conf_label_and_actions():
    assert spark_ascii([]) == "—"
    assert spark_ascii([1.0]) == "—"
    up = spark_ascii([1, 2, 3, 4, 5], n=5)
    assert len(up) == 5
    assert up[0] < up[-1]
    flat = spark_ascii([1.0, 1.0, 1.0], n=3)
    assert flat == "▁▁▁"
    assert conf_label(0.51) == "51%"
    assert conf_label(None) == "—"
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    last = {
        "datetime": "2024-01-02 15:00:00",
        "signal": "BUY",
        "confidence": 0.4,
        "p_buy": 0.4,
        "p_sell": 0.3,
        "p_hold": 0.3,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, load_config())
    assert paper_actions_label(row) == "BUY/SELL"
    row.validity = "STALE"
    assert paper_actions_label(row).startswith("disabled")
    assert paper_actions_label(row, has_open=True) == "CLOSE"


def test_paper_submit_risk_defaults_use_atr_box_unless_stale():
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    cfg = {
        "label_scheme": "triple_barrier",
        "horizon": 8,
        "entry_timing": "next_open",
        "atr_period": 14,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0},
        "spread_pips": 1.0,
    }
    sl, tp = paper_submit_risk_defaults(df, cfg, "BUY", "OK")
    assert sl is not None and tp is not None
    assert tp > sl
    sl_s, tp_s = paper_submit_risk_defaults(df, cfg, "BUY", "STALE")
    assert sl_s is None and tp_s is None
    sl_m, tp_m = paper_submit_risk_defaults(df, cfg, "SELL", "MISSING")
    assert sl_m is None and tp_m is None
    hold_sl, hold_tp = paper_submit_risk_defaults(df, cfg, "HOLD", "OK")
    assert hold_sl is None and hold_tp is None


def test_attach_next_event_warns_inside_pre_event_window():
    from datetime import datetime, timezone

    from forex_lab.calendar import parse_events

    events = parse_events(
        [
            {
                "title": "Non-Farm Employment Change",
                "country": "USD",
                "date": "2026-09-21T12:30:00-04:00",
                "impact": "High",
            }
        ]
    )
    df = generate_synthetic_ohlcv(bars=80, seed=3)
    last = {
        "datetime": "2026-09-21 15:00:00",
        "signal": "HOLD",
        "confidence": 0.4,
        "p_buy": 0.34,
        "p_sell": 0.33,
        "p_hold": 0.33,
        "model": "xgboost",
        "close": float(df["Close"].iloc[-1]),
    }
    row = row_from_signal("EURUSD", "1h", last, df, load_config())
    now = datetime(2026, 9, 21, 16, 0, tzinfo=timezone.utc)
    attach_next_event(row, events, now=now, cfg={"advice": {"before_minutes": 60}})
    assert row.next_event_warn is True
    assert row.next_event.startswith("⚠")
    assert "NFP" in row.next_event
    assert "USD" in row.next_event
    table = board_table([row], events=events, now=now, cfg={"advice": {"before_minutes": 60}})
    assert table.iloc[0]["Next event"].startswith("⚠")
    later = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    attach_next_event(row, events, now=later, cfg={"advice": {"before_minutes": 60}})
    assert row.next_event_warn is False
    assert "NFP" in row.next_event
    assert not row.next_event.startswith("⚠")


def test_inactive_row_does_not_generate_signals(monkeypatch):
    frame = generate_synthetic_ohlcv(pair="GBPUSD", bars=80, seed=1)
    called: list[str] = []

    def _status(*_a, **_k):
        return {"model_exists": True, "data_exists": True, "n_bars": len(frame)}

    def _gen(*_a, **_k):
        called.append("generate")
        raise AssertionError("inactive pair must not run the model")

    monkeypatch.setattr("forex_lab.ui.board.artifact_status", _status)
    monkeypatch.setattr("forex_lab.ui.board.load_cached_ohlcv", lambda *_a, **_k: frame)
    monkeypatch.setattr("forex_lab.ui.board._latest_csv_row", lambda *_a, **_k: None)
    monkeypatch.setattr("forex_lab.ui.board.generate_signals", _gen)
    monkeypatch.setattr("forex_lab.ui.board.attach_mtf", lambda row, *_a, **_k: row)
    row = build_board_row("GBPUSD", allow_generate=False, regenerate=False)
    assert called == []
    assert row.buy_sell == "—"
    assert row.status != "need_train"
    assert "not the active pair" in row.signal_details
    assert "model was not run" in row.signal_details
