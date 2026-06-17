"""Tests for long-gap (distance-from-event) confidence decay."""
from __future__ import annotations

import numpy as np

from sentrixsync.core.types import Kernel
from sentrixsync.sync import TimelineBuilder, build_confidence
from sentrixsync.sync.timeline import CorrectedStream


def _alignment_over(grid_end_us=100_000, step_us=1000):
    b = TimelineBuilder(grid_rate_hz=1000, rejection_tolerance_us=5000)
    samples = np.arange(0, grid_end_us, step_us, dtype=np.int64)
    tl = b.build("ref", [CorrectedStream("a", samples, Kernel.CONTINUOUS)])
    return tl, tl.per_stream["a"]


def test_clock_confidence_decays_with_distance_from_events():
    tl, a = _alignment_over()
    comp = build_confidence(a, clock_confidence=1.0, grid_us=tl.grid_us,
                            event_ref_times_us=np.array([0]), decay_tau_us=10000.0)
    assert comp.clock[0] > comp.clock[a.valid.size // 2] > comp.clock[-1]
    assert comp.clock[0] == 1.0                      # at the event
    assert comp.clock[-1] < 0.01                     # ~exp(-99000/10000)


def test_no_decay_when_unconfigured_is_flat():
    tl, a = _alignment_over()
    comp = build_confidence(a, clock_confidence=0.8)
    assert np.all(comp.clock[a.valid] == 0.8)        # flat where valid


def test_confidence_high_near_any_event():
    tl, a = _alignment_over()
    comp = build_confidence(a, clock_confidence=1.0, grid_us=tl.grid_us,
                            event_ref_times_us=np.array([0, 99000]), decay_tau_us=5000.0)
    # both ends near an event -> high; middle (farthest from both) -> lower
    assert comp.clock[0] > 0.9 and comp.clock[-1] > 0.9
    assert comp.clock[a.valid.size // 2] < comp.clock[0]
