"""Test isolation: never write the live paper journals from pytest."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_paper_journals(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("paper_journals")
    monkeypatch.setenv("FORX_SHADOW_JOURNAL", str(root / "journal.jsonl"))
    monkeypatch.setenv("FORX_EXPLORE_JOURNAL", str(root / "explore_journal.jsonl"))
    monkeypatch.setenv("FORX_CALLS_LOG", str(root / "calls.jsonl"))
    # Synthetic test bars are often dated after the real cutoff; holdout tests opt in.
    monkeypatch.setenv("FORX_HOLDOUT_FILE", "off")
    yield
