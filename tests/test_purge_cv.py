"""Purge/embargo bars on trainable_mask (LdP label-path overlap)."""
from __future__ import annotations

import pandas as pd

from forex_lab.replay import trainable_mask, label_ready_at


def test_trainable_mask_purge_drops_boundary_overlap():
    idx = pd.date_range("2015-01-05", periods=40, freq="h")
    horizon = 5
    as_of = idx[20]
    # Legacy: label at t=15 ready at bar 20 inclusive.
    legacy = trainable_mask(idx, idx, as_of, horizon, purge_bars=0)
    assert bool(legacy.loc[idx[15]])
    assert not bool(legacy.loc[idx[16]])
    # purge_bars=1: cutoff is bar 19 => t=14 ready at 19 OK; t=15 ready at 20 purged.
    purged = trainable_mask(idx, idx, as_of, horizon, purge_bars=1)
    assert bool(purged.loc[idx[14]])
    assert not bool(purged.loc[idx[15]])
    # purge_bars=horizon: last kept decision is as_of_loc - horizon - horizon.
    heavy = trainable_mask(idx, idx, as_of, horizon, purge_bars=horizon)
    assert label_ready_at(idx[10], idx, horizon) == idx[15]
    assert bool(heavy.loc[idx[10]])  # ready at 15; cutoff = idx[20-5]=idx[15]
    assert not bool(heavy.loc[idx[11]])  # ready at 16 > cutoff 15


def test_trainable_mask_purge_zero_matches_legacy():
    idx = pd.date_range("2015-01-05", periods=30, freq="h")
    as_of = idx[20]
    a = trainable_mask(idx, idx, as_of, 5)
    b = trainable_mask(idx, idx, as_of, 5, purge_bars=0)
    assert a.equals(b)
