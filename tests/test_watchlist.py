"""Watchlist load/save for the Streamlit research board."""
from __future__ import annotations

import pytest

from forex_lab.ui.watchlist import (
    WatchItem,
    Watchlist,
    WatchlistError,
    active_pair,
    add_pair,
    default_watchlist,
    load_watchlist,
    normalize_pair,
    parse_ohlcv_filename,
    remove_pair,
    save_watchlist,
)


def test_normalize_pair_accepts_common_spellings():
    assert normalize_pair("EURUSD") == "EURUSD"
    assert normalize_pair("eur/usd") == "EURUSD"
    assert normalize_pair("EUR-USD") == "EURUSD"
    assert normalize_pair("EURUSD=X") == "EURUSD"
    assert normalize_pair(" eurusd ") == "EURUSD"


def test_normalize_pair_rejects_junk():
    with pytest.raises(WatchlistError):
        normalize_pair("EU")
    with pytest.raises(WatchlistError):
        normalize_pair("EURUSDJPY")
    with pytest.raises(WatchlistError):
        normalize_pair("")


def test_parse_ohlcv_filename():
    assert parse_ohlcv_filename("EURUSD_1h.csv") == ("EURUSD", "1h")
    assert parse_ohlcv_filename("USDJPY_15m.csv") == ("USDJPY", "15m")
    assert parse_ohlcv_filename("notes.csv") is None


def test_watchlist_roundtrip(tmp_path):
    path = tmp_path / "watchlist.yaml"
    wl = Watchlist(
        pairs=[WatchItem("EURUSD"), WatchItem("GBPUSD", interval="1d")],
        refresh_seconds=45,
        interval=None,
    )
    saved = save_watchlist(wl, path)
    assert saved.exists()
    text = saved.read_text(encoding="utf-8")
    assert "EURUSD" in text
    assert "GBPUSD" in text
    loaded = load_watchlist(path)
    assert loaded.pair_symbols() == ["EURUSD", "GBPUSD"]
    assert loaded.pairs[1].interval == "1d"
    assert loaded.refresh_seconds == 45
    assert loaded.contains("eurusd")


def test_add_and_remove_pair(tmp_path):
    path = tmp_path / "watchlist.yaml"
    wl = Watchlist(pairs=[WatchItem("EURUSD")], refresh_seconds=60)
    add_pair(wl, "gbp/usd")
    add_pair(wl, "GBPUSD")  # duplicate
    save_watchlist(wl, path)
    loaded = load_watchlist(path)
    assert loaded.pair_symbols() == ["EURUSD", "GBPUSD"]
    remove_pair(loaded, "EURUSD")
    save_watchlist(loaded, path)
    again = load_watchlist(path)
    assert again.pair_symbols() == ["GBPUSD"]
    assert not again.contains("EURUSD")


def test_missing_file_create_defaults_to_eurusd(tmp_path):
    path = tmp_path / "cfg" / "watchlist.yaml"
    wl = load_watchlist(path, create=True)
    assert path.exists()
    assert wl.pairs[0].pair == "EURUSD"
    assert wl.refresh_seconds == 60


def test_default_watchlist_starts_with_eurusd():
    wl = default_watchlist()
    assert wl.pair_symbols()[0] == "EURUSD"


def test_committed_watchlist_includes_eurusd():
    wl = load_watchlist(create=False)
    assert "EURUSD" in wl.pair_symbols()


def test_active_pair_defaults_to_first_and_persists(tmp_path):
    path = tmp_path / "watchlist.yaml"
    wl = Watchlist(pairs=[WatchItem("EURUSD"), WatchItem("GBPUSD")], refresh_seconds=60)
    assert active_pair(wl) == "EURUSD"
    save_watchlist(wl, path)
    assert "active: EURUSD" in path.read_text(encoding="utf-8")
    assert active_pair(load_watchlist(path)) == "EURUSD"
    loaded = load_watchlist(path)
    loaded.active = "GBPUSD"
    save_watchlist(loaded, path)
    assert active_pair(load_watchlist(path)) == "GBPUSD"


def test_unknown_active_falls_back_to_the_first_pair(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("active: USDJPY\npairs:\n- EURUSD\n- GBPUSD\n", encoding="utf-8")
    wl = load_watchlist(path)
    assert active_pair(wl) == "EURUSD"
    save_watchlist(wl, path)
    assert active_pair(load_watchlist(path)) == "EURUSD"


def test_adding_a_pair_does_not_steal_active_unless_the_list_was_empty():
    wl = Watchlist(pairs=[WatchItem("EURUSD")], active="EURUSD")
    add_pair(wl, "GBPUSD")
    assert active_pair(wl) == "EURUSD"
    empty = Watchlist(pairs=[])
    add_pair(empty, "USDJPY")
    assert active_pair(empty) == "USDJPY"


def test_removing_the_active_pair_promotes_the_next():
    wl = Watchlist(
        pairs=[WatchItem("EURUSD"), WatchItem("GBPUSD"), WatchItem("USDJPY")],
        active="EURUSD",
    )
    remove_pair(wl, "USDJPY")
    assert active_pair(wl) == "EURUSD"
    remove_pair(wl, "EURUSD")
    assert active_pair(wl) == "GBPUSD"
    remove_pair(wl, "GBPUSD")
    assert active_pair(wl) == ""


def test_empty_pairs_list_is_allowed(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("refresh_seconds: 90\npairs: []\n", encoding="utf-8")
    wl = load_watchlist(path)
    assert wl.pairs == []
    assert wl.refresh_seconds == 90
