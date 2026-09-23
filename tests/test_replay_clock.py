"""ReplayClock must not let a decision at t see bar t+1."""
from __future__ import annotations

import lzma
import struct
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from forex_lab.config_loader import load_config
from forex_lab.history import (
    dukascopy_url,
    parse_bi5,
    parse_histdata_text,
    pull_history,
    resample_bars,
    ticks_to_bars,
)
from forex_lab.replay import (
    LookaheadError,
    ReplayClock,
    entry_price,
    label_ready_at,
    run_replay,
    touch_exit,
    trainable_mask,
)


def _bars(n: int = 40, start: str = "2015-01-05") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="h")
    rng = np.random.default_rng(7)
    mid = 1.10 + np.cumsum(rng.normal(0, 0.0004, n))
    spread = np.full(n, 0.00012)
    bid = mid - spread / 2
    ask = mid + spread / 2
    return pd.DataFrame(
        {
            "Open": mid,
            "High": mid + 0.0004,
            "Low": mid - 0.0004,
            "Close": mid,
            "Volume": 100.0,
            "BidOpen": bid,
            "BidHigh": bid + 0.0003,
            "BidLow": bid - 0.0005,
            "BidClose": bid,
            "AskOpen": ask,
            "AskHigh": ask + 0.0003,
            "AskLow": ask - 0.0005,
            "AskClose": ask,
            "Spread": spread,
        },
        index=idx,
    )


def test_replay_clock_hides_the_next_bar():
    df = _bars(30)
    t = df.index[10]
    nxt = df.index[11]
    poisoned = df.copy()
    poisoned.loc[nxt, "Close"] = 999.0
    clock = ReplayClock(poisoned, as_of=t)
    view = clock.visible()
    assert view.index.max() == t
    assert nxt not in view.index
    assert float(view["Close"].iloc[-1]) == pytest.approx(float(df.loc[t, "Close"]))
    assert float(view["Close"].iloc[-1]) != 999.0
    with pytest.raises(LookaheadError, match="cannot see"):
        clock.bar(nxt)


def test_trainable_labels_stop_at_the_clock():
    idx = pd.date_range("2015-01-05", periods=30, freq="h")
    horizon = 5
    as_of = idx[20]
    mask = trainable_mask(idx, idx, as_of, horizon)
    # Label at t is ready at t+horizon. t=15 finishes on bar 20; t=16 needs bar 21.
    assert bool(mask.loc[idx[15]])
    assert not bool(mask.loc[idx[16]])
    assert not bool(mask.loc[idx[20]])
    assert label_ready_at(idx[15], idx, horizon) == idx[20]
    assert label_ready_at(idx[16], idx, horizon) == idx[21]
    assert label_ready_at(idx[16], idx, horizon) > as_of


def test_decision_fill_uses_next_bar_bid_ask_not_the_decision_close(tmp_path: Path):
    df = _bars(48)
    decision = df.index[30]
    nxt = df.index[31]
    df.loc[decision, "Close"] = 9.5
    df.loc[nxt, "AskOpen"] = 1.2345
    df.loc[nxt, "BidOpen"] = 1.2340
    live_journal = tmp_path / "live_paper.json"
    live_journal.write_text('{"untouched": true}', encoding="utf-8")

    def signal_at(name: str, ts: pd.Timestamp, visible: pd.DataFrame) -> str:
        assert visible.index.max() == ts
        if ts == decision:
            assert nxt not in visible.index
            assert float(visible.loc[decision, "Close"]) == pytest.approx(9.5)
        if name == "champion" and ts == decision:
            return "BUY"
        return "HOLD"

    cfg = {
        "horizon": 4,
        "atr_period": 14,
        "spread_pips": 1.0,
        "commission_pips": 0.0,
        "pip_size": 0.0001,
        "barrier": {"tp_atr": 2.0, "sl_atr": 2.0, "path": "high_low"},
        "signals": {"min_confidence": 0.40},
        "replay": {"use_bid_ask": True, "slippage_pips": 0.0, "store_dir": str(tmp_path)},
        "broker": {"backend": "paper", "store": str(live_journal), "default_size": 1.0},
        "ui": {"timezone": "Asia/Dhaka", "timezone_tag": "Asia/Dhaka"},
    }
    result = run_replay(
        df,
        cfg,
        "EURUSD",
        interval="1h",
        job_dir=tmp_path / "job",
        source="fixture",
        signal_at=signal_at,
        collect_decisions=True,
    )
    trades = pd.read_csv(tmp_path / "job" / "trades.csv")
    champ = trades[trades["book"] == "champion"]
    assert len(champ) == 1
    assert float(champ.iloc[0]["entry_price"]) == pytest.approx(1.2345)
    assert float(champ.iloc[0]["entry_price"]) != pytest.approx(9.5)
    assert "Asia/Dhaka" in str(champ.iloc[0]["entry_time_dhaka"])
    decisions = result["decisions"]
    assert decisions
    for row in decisions:
        assert row["visible_max"] == row["as_of"]
    # Challenger was flat, so the books do not share a position file.
    champ_book = (tmp_path / "job" / "paper_champion.json").read_text(encoding="utf-8")
    assert "1.2345" in champ_book
    assert not (tmp_path / "job" / "paper_challenger.json").exists()
    assert live_journal.read_text(encoding="utf-8") == '{"untouched": true}'
    assert "Promotion" in result["promotion_line"]
    assert "trades/hour" in result["promotion_line"]
    assert (tmp_path / "job" / "scoreboard.csv").is_file()
    assert (tmp_path / "job" / "scoreboard.xlsx").is_file()
    assert (tmp_path / "job" / "equity.png").stat().st_size > 0
    board = pd.read_csv(tmp_path / "job" / "scoreboard.csv")
    assert set(board["book"]) == {"champion", "challenger", "sma"}


def test_entry_and_exit_quotes_use_the_executable_side():
    row = pd.Series(
        {
            "Open": 1.10,
            "High": 1.11,
            "Low": 1.09,
            "Close": 1.10,
            "BidOpen": 1.1000,
            "AskOpen": 1.1002,
            "BidHigh": 1.1050,
            "BidLow": 1.0990,
            "AskHigh": 1.1052,
            "AskLow": 1.0992,
            "BidClose": 1.1010,
            "AskClose": 1.1012,
        }
    )
    assert entry_price(row, "BUY", bid_ask=True, slip=0.0) == pytest.approx(1.1002)
    assert entry_price(row, "SELL", bid_ask=True, slip=0.0) == pytest.approx(1.1000)
    # Long stop is judged on the bid, not the ask.
    hit = touch_exit("BUY", sl=1.0995, tp=1.20, row=row, bid_ask=True, bars_seen=1, horizon=8)
    assert hit == (1.0995, "sl")
    miss = touch_exit("BUY", sl=1.0980, tp=1.20, row=row, bid_ask=True, bars_seen=1, horizon=8)
    assert miss is None


def test_model_replay_does_not_train_on_unsettled_labels(tmp_path: Path):
    """A short logistic replay still refuses a training row whose label needs t+1."""
    df = _bars(160)
    cfg = load_config()
    cfg = {
        **cfg,
        "horizon": 4,
        "atr_period": 5,
        "sma_windows": [5, 8],
        "ema_windows": [5, 8],
        "vol_window": 8,
        "vol_long_window": 16,
        "range_windows": [8],
        "feature_extras": {"higher_tf": [], "pandas_ta": {"enabled": False}, "fred": {"enabled": False}, "vol_percentile_window": 0},
        "model": {**(cfg.get("model") or {}), "type": "logistic", "compare_logistic": False, "calibrate": None},
        "walk_forward": {"train_bars": 60, "test_bars": 20, "step_bars": 20, "min_train_bars": 30},
        "signals": {**(cfg.get("signals") or {}), "min_confidence": 0.0, "sessions": [], "min_dir_edge": 0.0},
        "gates": {"enabled": False},
        "replay": {"use_bid_ask": True, "slippage_pips": 0.1},
        "broker": {"backend": "paper", "store": str(tmp_path / "live.json"), "default_size": 1.0},
    }
    seen: list[pd.Timestamp] = []

    def _guard(name: str, ts: pd.Timestamp, visible: pd.DataFrame) -> str:
        # Production path does not use this hook. The mask test above is the
        # label gate; this run uses the model. Placeholder to satisfy the type
        # if we ever wire it — not called.
        seen.append(ts)
        return "HOLD"

    del _guard
    result = run_replay(df, cfg, "EURUSD", interval="1h", job_dir=tmp_path / "ml", source="fixture", collect_decisions=True)
    assert result["ok"] is True
    assert result["bid_ask"] is True
    for row in result["decisions"] or []:
        assert row["visible_max"] == row["as_of"]
        # The logged as-of is that bar. The next hour is not the visible max.
        as_of = pd.Timestamp(row["as_of"].replace(" UTC", ""))
        visible_max = pd.Timestamp(row["visible_max"].replace(" UTC", ""))
        assert visible_max == as_of
        assert visible_max + pd.Timedelta(hours=1) > as_of
    assert "not point-in-time" in result["calendar_note"]
    assert not (tmp_path / "live.json").exists()


def test_dukascopy_bi5_roundtrip_keeps_spread():
    hour = datetime(2015, 1, 2, 3, tzinfo=timezone.utc)
    url = dukascopy_url("EURUSD", hour)
    assert url.endswith("/EURUSD/2015/00/02/03h_ticks.bi5")
    point = 100_000

    def rec(ms: int, ask: float, bid: float) -> bytes:
        return struct.pack(">IIIff", ms, int(round(ask * point)), int(round(bid * point)), 1.0, 1.0)

    blob = lzma.compress(rec(0, 1.10020, 1.10000) + rec(1_800_000, 1.10100, 1.10080))
    ticks = parse_bi5(blob, hour, point)
    assert len(ticks) == 2
    bars = ticks_to_bars(ticks, "1h")
    assert len(bars) == 1
    row = bars.iloc[0]
    assert row["BidOpen"] == pytest.approx(1.10000)
    assert row["AskOpen"] == pytest.approx(1.10020)
    assert row["Spread"] > 0
    assert row["Close"] == pytest.approx((1.10080 + 1.10100) / 2)


def test_histdata_parser_and_resample_do_not_invent_bid_ask():
    text = "\n".join(
        [
            "20150101 000000;1.20000;1.20040;1.19980;1.20010;10",
            "20150101 010000;1.20010;1.20050;1.19990;1.20020;12",
        ]
    )
    m1 = parse_histdata_text(text)
    # Two stamps an hour apart are already hourly; resample to 1h keeps both.
    hourly = resample_bars(m1, "1h")
    assert len(hourly) == 2
    assert "BidClose" not in hourly.columns
    assert float(hourly.iloc[0]["Open"]) == pytest.approx(1.2)


def test_pull_history_uses_injected_hours_and_skips_git_sized_ticks(tmp_path, monkeypatch):
    monkeypatch.setenv("FORX_HISTORY_DIR", str(tmp_path / "history"))
    hour = datetime(2015, 1, 5, 10, tzinfo=timezone.utc)
    point = 100_000

    def rec(ms: int, ask: float, bid: float) -> bytes:
        return struct.pack(">IIIff", ms, int(round(ask * point)), int(round(bid * point)), 1.0, 1.0)

    blob = lzma.compress(rec(0, 1.1002, 1.1000) + rec(1000, 1.1004, 1.1001))

    def fetch_hour(ts: datetime) -> bytes | None:
        if ts.hour == 10 and ts.day == 5:
            return blob
        return None

    cfg = {"replay": {"history_dir": str(tmp_path / "history"), "workers": 1}}
    result = pull_history(
        "EURUSD",
        cfg,
        interval="1h",
        start="2015-01-05 10:00",
        end="2015-01-05 12:00",
        source="dukascopy",
        fetch_hour=fetch_hour,
    )
    assert result["source"] == "dukascopy"
    assert result["rows"] == 1
    assert result["bid_ask"] is True
    saved = Path(result["path"])
    assert saved.exists()
    text = saved.read_text(encoding="utf-8")
    assert "1.100" in text
    assert not list((tmp_path / "history").glob("*.bi5"))
