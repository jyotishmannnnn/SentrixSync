"""Tests for the as-of join engine and sub-frame bucketing."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.core.types import Kernel, ValidationError
from sentrixsync.sync import asof_join, compute_R, subframe_buckets


def test_hold_latest_at_and_gap_invalid():
    grid = np.array([0, 100, 250, 1000], dtype=np.int64)
    samples = np.array([0, 200], dtype=np.int64)
    a = asof_join("s", grid, samples, Kernel.HOLD, tolerance_us=150)
    # grid 0 -> sample0 (gap0); 100 -> sample0 (gap100<=150 valid); 250 -> sample1 (gap50 valid);
    # 1000 -> sample1 (gap800 > 150 -> invalid)
    assert list(a.source_index) == [0, 0, 1, 1]
    assert list(a.valid) == [True, True, True, False]
    assert a.interp_confidence[3] == 0.0
    assert a.interp_confidence[0] == 1.0          # zero gap -> full confidence


def test_continuous_interpolation_weight():
    grid = np.array([500], dtype=np.int64)
    samples = np.array([0, 1000], dtype=np.int64)
    a = asof_join("s", grid, samples, Kernel.CONTINUOUS, tolerance_us=2000)
    assert a.source_index[0] == 0 and a.next_index[0] == 1
    assert abs(a.weight[0] - 0.5) < 1e-9
    assert a.valid[0]


def test_empty_stream_all_invalid():
    grid = np.array([0, 100], dtype=np.int64)
    a = asof_join("s", grid, np.empty(0, np.int64), Kernel.HOLD, tolerance_us=100)
    assert not a.valid.any()
    assert a.coverage() == 0.0


def test_grid_before_first_sample_invalid_for_hold():
    grid = np.array([0, 50], dtype=np.int64)
    samples = np.array([40], dtype=np.int64)
    a = asof_join("s", grid, samples, Kernel.HOLD, tolerance_us=100)
    assert not a.valid[0]                          # nothing at-or-before t=0
    assert a.valid[1]


def test_compute_R_uses_ceil():
    assert compute_R(1600, 30) == 54               # 53.33 -> 54, nothing discarded
    assert compute_R(1000, 100) == 10


def test_subframe_repeat_pad_and_validity():
    anchor = np.array([0, 100, 200], dtype=np.int64)     # 2 frames: [0,100), [100,200)
    high = np.array([10, 20, 110], dtype=np.int64)       # frame0: {10,20}; frame1: {110}
    b = subframe_buckets(anchor, high, R=3)
    assert b.R == 3
    assert list(b.m_k) == [2, 1]
    assert list(b.valid) == [True, True]
    # frame0: indices [0,1, pad->1]; frame1: [2,2,2] (repeat-pad)
    assert list(b.index[0]) == [0, 1, 1]
    assert list(b.index[1]) == [2, 2, 2]


def test_subframe_empty_frame_is_gap():
    anchor = np.array([0, 100, 200], dtype=np.int64)
    high = np.array([10], dtype=np.int64)                # only frame0 populated
    b = subframe_buckets(anchor, high, R=2)
    assert list(b.valid) == [True, False]                # empty frame -> invalid, not padded
    assert b.m_k[1] == 0


def test_subframe_requires_two_anchors():
    with pytest.raises(ValidationError, match=">= 2 anchor"):
        subframe_buckets(np.array([0], np.int64), np.array([0], np.int64), R=2)
