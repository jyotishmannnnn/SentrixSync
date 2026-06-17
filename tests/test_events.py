"""Tests for SyncEvent."""
from __future__ import annotations

import pytest

from sentrixsync.core import EvidenceTier, SyncEvent
from sentrixsync.core.types import ValidationError


def test_sync_event_roundtrip():
    e = SyncEvent(event_id="tap_0", tier=EvidenceTier.SHARED_EVENT,
                  observations={"glove_L": 1000, "ego_cam": 21000},
                  detector="tap_impulse", quality=0.95, kind="impulse")
    e.validate()
    assert SyncEvent.from_dict(e.to_dict()) == e


def test_usable_requires_two_observations():
    one = SyncEvent(event_id="e", tier=EvidenceTier.SHARED_EVENT, observations={"glove_L": 5})
    one.validate()                 # valid entity...
    assert not one.is_usable()     # ...but not usable for cross-device fitting
    two = SyncEvent(event_id="e", tier=EvidenceTier.SHARED_EVENT,
                    observations={"glove_L": 5, "ego_cam": 9})
    assert two.is_usable()
    assert two.device_ids() == {"glove_L", "ego_cam"}


def test_observation_timestamps_must_be_microseconds():
    with pytest.raises(ValidationError):
        SyncEvent(event_id="e", tier=EvidenceTier.SHARED_EVENT,
                  observations={"glove_L": 1.5}).validate()


def test_requires_at_least_one_observation():
    with pytest.raises(ValidationError, match="at least one"):
        SyncEvent(event_id="e", tier=EvidenceTier.WALL_CLOCK, observations={}).validate()


def test_tier_coercion_from_string():
    e = SyncEvent.from_dict({"event_id": "e", "tier": "hardware_ptp",
                             "observations": {"a": 1, "b": 2}})
    assert e.tier is EvidenceTier.HARDWARE_PTP


def test_bad_quality_rejected():
    with pytest.raises(ValidationError):
        SyncEvent(event_id="e", tier=EvidenceTier.SHARED_EVENT,
                  observations={"a": 1, "b": 2}, quality=1.5).validate()
