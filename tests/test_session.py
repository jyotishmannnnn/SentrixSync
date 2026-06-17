"""Tests for the Session entity and its cross-cutting validation rules."""
from __future__ import annotations

import pytest

from sentrixsync.core import (
    DeviceRole,
    GroundTruthBlock,
    Origin,
    Session,
)
from sentrixsync.core.types import ValidationError


def test_single_device_session_valid(single_device_session):
    single_device_session.validate()
    ref = single_device_session.reference_device()
    assert ref is not None and ref.device_id == "glove_L"


def test_two_device_session_valid(two_device_session):
    two_device_session.validate()


def test_session_roundtrip(two_device_session):
    restored = Session.from_dict(two_device_session.to_dict())
    restored.validate()
    assert restored.to_dict() == two_device_session.to_dict()


def test_exactly_one_reference_required(two_device_session):
    # promote the follower to a second reference -> invalid
    two_device_session.devices[1].role = DeviceRole.REFERENCE
    with pytest.raises(ValidationError, match="exactly one device"):
        two_device_session.validate()


def test_zero_reference_rejected(single_device_session):
    single_device_session.devices[0].role = DeviceRole.FOLLOWER
    with pytest.raises(ValidationError, match="exactly one device"):
        single_device_session.validate()


def test_duplicate_device_ids_rejected(two_device_session):
    two_device_session.devices[1].device_id = "glove_L"
    two_device_session.devices[1].descriptor.device_id = "glove_L"
    # fix stream device_ids so descriptor itself is valid, isolating the dup check
    for s in two_device_session.devices[1].descriptor.streams:
        s.device_id = "glove_L"
    with pytest.raises(ValidationError, match="duplicate device_id"):
        two_device_session.validate()


def test_ground_truth_forbidden_for_real_session(single_device_session):
    single_device_session.metadata.origin = Origin.REAL
    single_device_session.ground_truth = GroundTruthBlock(
        clock_models={"glove_L": {"alpha": 1.0, "beta_us": 0}})
    with pytest.raises(ValidationError, match="synthetic"):
        single_device_session.validate()


def test_ground_truth_allowed_for_synthetic(single_device_session):
    single_device_session.ground_truth = GroundTruthBlock(
        clock_models={"glove_L": {"alpha": 1.0, "beta_us": 0}})
    single_device_session.validate()


def test_ground_truth_block_requires_alpha_beta():
    with pytest.raises(ValidationError, match="alpha and beta_us"):
        GroundTruthBlock(clock_models={"d": {"alpha": 1.0}}).validate()


def test_producers_required(single_device_session):
    single_device_session.metadata.producers = []
    with pytest.raises(ValidationError, match="producers"):
        single_device_session.validate()
