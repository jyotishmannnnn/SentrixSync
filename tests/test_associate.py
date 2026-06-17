"""Tests for subset-aware event association."""
from __future__ import annotations

import numpy as np

from sentrixsync.core.types import EvidenceTier
from sentrixsync.detect import associate_detections


def test_subset_association_partial_overlap():
    # Three devices, roughly aligned (coarse identity). Fiducials at 0,100k,200k us.
    # A sees {f0,f1}; B sees {f0,f1,f2}; C sees {f1,f2}. Small per-device skew in obs.
    detections = {
        "A": np.array([1000, 101000]),
        "B": np.array([1200, 101200, 201200]),
        "C": np.array([101100, 201100]),
    }
    events = associate_detections(detections, tier=EvidenceTier.SHARED_EVENT,
                                  association_tolerance_us=5000)
    # f0 -> {A,B}; f1 -> {A,B,C}; f2 -> {B,C}
    subsets = sorted(sorted(e.observations.keys()) for e in events)
    assert subsets == [["A", "B"], ["A", "B", "C"], ["B", "C"]]


def test_coarse_clocks_pre_align_large_offsets():
    # B is offset +50ms; without coarse alignment its detections wouldn't cluster
    # with A's. Coarse clocks bring them into a common frame.
    detections = {"A": np.array([1000, 101000]),
                  "B": np.array([51000, 151000])}     # +50ms offset
    coarse = {"A": (1.0, 0.0), "B": (1.0, -50000.0)}
    events = associate_detections(detections, tier=EvidenceTier.SHARED_EVENT,
                                  association_tolerance_us=5000, coarse_clocks=coarse)
    assert len(events) == 2
    for e in events:
        assert set(e.observations) == {"A", "B"}


def test_min_observers_drops_singletons():
    detections = {"A": np.array([1000]), "B": np.array([900000])}  # far apart -> no overlap
    events = associate_detections(detections, tier=EvidenceTier.SHARED_EVENT,
                                  association_tolerance_us=5000)
    assert events == []                                # each cluster has 1 observer -> dropped


def test_same_device_twice_in_window_starts_new_cluster():
    detections = {"A": np.array([1000, 1100]), "B": np.array([1050])}
    events = associate_detections(detections, tier=EvidenceTier.SHARED_EVENT,
                                  association_tolerance_us=5000)
    # A's two detections cannot share a cluster; only one pairs with B
    assert len(events) == 1
    assert set(events[0].observations) == {"A", "B"}
