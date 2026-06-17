"""Tests for the TimelineBuilder and confidence framework."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.core.types import Kernel, ValidationError
from sentrixsync.sync import TimelineBuilder, build_confidence
from sentrixsync.sync.timeline import CorrectedStream


def test_timeline_grid_is_regular_and_monotonic():
    b = TimelineBuilder(grid_rate_hz=1000, rejection_tolerance_us=2000)
    streams = [CorrectedStream("a", np.array([0, 1000, 2000], np.int64), Kernel.CONTINUOUS)]
    tl = b.build("ref_clk", streams)
    assert tl.reference_clock_id == "ref_clk"
    assert tl.t_start_us == 0 and tl.t_end_us == 2000
    assert np.all(np.diff(tl.grid_us) == 1000)         # 1000 Hz -> 1000 us steps
    assert tl.n_grid == 3


def test_timeline_spans_union_of_streams():
    b = TimelineBuilder(grid_rate_hz=1000, rejection_tolerance_us=5000)
    streams = [CorrectedStream("a", np.array([0, 1000], np.int64), Kernel.HOLD),
               CorrectedStream("b", np.array([3000, 4000], np.int64), Kernel.HOLD)]
    tl = b.build("ref", streams)
    assert tl.t_start_us == 0 and tl.t_end_us == 4000
    assert set(tl.per_stream) == {"a", "b"}


def test_timeline_requires_a_stream_with_samples():
    b = TimelineBuilder(grid_rate_hz=1000, rejection_tolerance_us=1000)
    with pytest.raises(ValidationError, match="at least one stream with samples"):
        b.build("ref", [CorrectedStream("a", np.empty(0, np.int64), Kernel.HOLD)])


def test_confidence_components_stored_separately():
    b = TimelineBuilder(grid_rate_hz=1000, rejection_tolerance_us=2000)
    streams = [CorrectedStream("a", np.array([0, 1000, 2000], np.int64), Kernel.CONTINUOUS)]
    tl = b.build("ref", streams)
    a = tl.per_stream["a"]
    comp = build_confidence(a, clock_confidence=0.8)
    assert comp.source.shape == comp.clock.shape == comp.interpolation.shape
    # clock component carries the scalar where valid; components remain distinct
    assert np.all(comp.clock[a.valid] == 0.8)
    scalar = comp.derived_scalar()
    assert scalar.shape == comp.source.shape
    assert np.all((scalar >= 0.0) & (scalar <= 1.0))


def test_confidence_zero_at_gaps():
    b = TimelineBuilder(grid_rate_hz=1000, rejection_tolerance_us=500)
    # sample only near start; later grid points are gaps
    streams = [CorrectedStream("a", np.array([0], np.int64), Kernel.HOLD)]
    tl = b.build("ref", streams)
    # add a far stream to extend the grid
    streams2 = [CorrectedStream("a", np.array([0], np.int64), Kernel.HOLD),
                CorrectedStream("b", np.array([10000], np.int64), Kernel.HOLD)]
    tl = b.build("ref", streams2)
    a = tl.per_stream["a"]
    comp = build_confidence(a, clock_confidence=1.0)
    assert np.all(comp.source[~a.valid] == 0.0)
    assert np.all(comp.clock[~a.valid] == 0.0)
    assert np.all(comp.interpolation[~a.valid] == 0.0)
