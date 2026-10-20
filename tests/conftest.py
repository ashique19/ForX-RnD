"""Test isolation: never write the live paper journals from pytest."""
from __future__ import annotations

import pytest
from pathlib import Path as _Path


@pytest.fixture(autouse=True)
def _isolate_paper_journals(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("paper_journals")
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(root / "journal.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(root / "explore_journal.jsonl"))
    monkeypatch.setenv("FORX_CALLS_LOG", str(root / "calls.jsonl"))
    # Synthetic test bars are often dated after the real cutoff; holdout tests opt in.
    monkeypatch.setenv("FORX_HOLDOUT_FILE", "off")
    _isolate_live_paper_book(root, monkeypatch)
    yield


def _isolate_live_paper_book(root, monkeypatch):
    """Never let a test (incl. the Streamlit AppTest render) touch data/paper_broker.json.

    The default store (``broker.store`` missing or ``data/paper_broker.json``) is
    redirected to a tmp file, and any flush that still resolves to the live file
    raises. Tests that pass their own tmp store are unaffected.
    """
    from forex_lab import broker as _broker
    from forex_lab.paths import project_root

    live = (project_root() / "data" / "paper_broker.json").resolve()
    tmp_store = root / "paper_broker.json"
    orig_resolve = _broker.resolve_under_root

    def _resolve(rel, *a, **k):
        out = orig_resolve(rel, *a, **k)
        try:
            if out is not None and _Path(out).resolve() == live:
                return tmp_store
        except OSError:
            pass
        return out

    orig_flush = _broker.PaperBroker._flush

    def _guarded_flush(self, *a, **k):
        if _Path(self.store).resolve() == live:
            raise RuntimeError(f"test tried to write the live paper book {live}")
        return orig_flush(self, *a, **k)

    monkeypatch.setattr(_broker, "resolve_under_root", _resolve)
    monkeypatch.setattr(_broker.PaperBroker, "_flush", _guarded_flush)


# Never reach the real MT5 terminal from tests (forex_lab.data.try_mt5_refresh); tests inject a fake fetch.
import os as _os_mt5
_os_mt5.environ.setdefault("FORX_MT5_TIP", "off")
_os_mt5.environ.setdefault("FORX_MT5_EXEC", "off")
_os_mt5.environ.setdefault("FORX_EXPLORE_BARS", "off")
