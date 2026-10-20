"""conftest isolation: tests never resolve or write the live data/paper_broker.json."""
from __future__ import annotations

from forex_lab.broker import PaperBroker, make_broker
from forex_lab.paths import project_root

LIVE = (project_root() / "data" / "paper_broker.json").resolve()


def test_default_store_is_redirected_off_the_live_book():
    b = make_broker({})
    assert b.store.resolve() != LIVE
    b2 = make_broker({"broker": {"backend": "paper", "store": "data/paper_broker.json"}})
    assert b2.store.resolve() != LIVE


def test_live_book_flush_guard_is_installed():
    # Checked without calling _flush, so a broken guard can never write the live file.
    assert PaperBroker._flush.__name__ == "_guarded_flush"
