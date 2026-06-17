"""Tests for the detector plugin framework and matcher."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.core.events import SyncEvent
from sentrixsync.core.types import EvidenceTier, ValidationError
from sentrixsync.detect import (
    find_impulse_peaks,
    get_detector,
    match_detections,
    registered_detectors,
)


def _planted_signal(peak_times_us, end_us=2_000_000, rate_hz=2000.0):
    base = np.arange(0, end_us, 1e6 / rate_hz)
    times = np.unique(np.concatenate([base, np.asarray(peak_times_us, float)])).astype(np.int64)
    sig = np.full(times.size, 0.01)
    idx = np.searchsorted(times, np.asarray(peak_times_us, dtype=np.int64))
    sig[idx] = 5.0
    return times, sig


def test_builtin_detectors_registered():
    names = registered_detectors()
    assert "tactile_tap" in names and "visual_flash" in names


def test_unknown_detector_rejected():
    with pytest.raises(ValidationError, match="unknown detector"):
        get_detector("nope")


@pytest.mark.parametrize("name", ["tactile_tap", "visual_flash"])
def test_detector_recovers_planted_impulses(name):
    planted = [300_000, 900_000, 1_500_000]
    t, sig = _planted_signal(planted)
    det = get_detector(name, threshold=0.5)
    found = det.detect(t, sig).times_us
    assert list(found) == planted


def test_find_impulse_peaks_one_per_region():
    t = np.arange(0, 10, dtype=np.int64)
    sig = np.array([0, 0, 9, 8, 0, 0, 7, 6, 0, 0], dtype=float)
    peaks = find_impulse_peaks(t, sig, threshold=0.5)
    assert list(peaks) == [2, 6]


def test_matcher_builds_events_by_order():
    detections = {"glove_L": np.array([1000, 2000, 3000]),
                  "ego_cam": np.array([21000, 22000, 23000])}
    events = match_detections(detections, tier=EvidenceTier.SHARED_EVENT)
    assert len(events) == 3
    assert all(isinstance(e, SyncEvent) and e.is_usable() for e in events)
    assert events[0].observations == {"glove_L": 1000, "ego_cam": 21000}


def test_matcher_requires_equal_counts():
    with pytest.raises(ValidationError, match="unequal detection counts"):
        match_detections({"a": np.array([1, 2]), "b": np.array([1])},
                         tier=EvidenceTier.SHARED_EVENT)


def test_matcher_requires_two_devices():
    with pytest.raises(ValidationError, match=">= 2 devices"):
        match_detections({"a": np.array([1, 2])}, tier=EvidenceTier.SHARED_EVENT)
