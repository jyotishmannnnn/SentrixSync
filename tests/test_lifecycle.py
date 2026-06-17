"""Tests for session lifecycle management (registration era + transition guards)."""
from __future__ import annotations

import pytest

from sentrixsync.core import (
    DeviceRegistration,
    DeviceRole,
    Origin,
    SessionMetadata,
)
from sentrixsync.core.types import ValidationError
from sentrixsync.lifecycle import SessionManager, SessionState
from conftest import make_camera_descriptor, make_tactile_descriptor


def _meta():
    return SessionMetadata(session_id="S1", origin=Origin.SYNTHETIC,
                           producers=["sentrixsim"], grid_rate_hz=1600)


def test_build_session_via_manager():
    m = SessionManager.start(_meta())
    assert m.state is SessionState.CREATED
    m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                         descriptor=make_tactile_descriptor()))
    m.finalize_registration()
    assert m.state is SessionState.DEVICES_REGISTERED
    assert m.session.reference_device().device_id == "glove_L"


def test_register_after_finalize_rejected():
    m = SessionManager.start(_meta())
    m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                         descriptor=make_tactile_descriptor()))
    m.finalize_registration()
    with pytest.raises(ValidationError, match="CREATED"):
        m.register_device(DeviceRegistration(device_id="ego_cam", role=DeviceRole.FOLLOWER,
                                             descriptor=make_camera_descriptor()))


def test_duplicate_registration_rejected():
    m = SessionManager.start(_meta())
    m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                         descriptor=make_tactile_descriptor()))
    with pytest.raises(ValidationError, match="already registered"):
        m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.FOLLOWER,
                                             descriptor=make_tactile_descriptor()))


def test_finalize_requires_a_reference():
    m = SessionManager.start(_meta())
    m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.FOLLOWER,
                                         descriptor=make_tactile_descriptor()))
    with pytest.raises(ValidationError, match="exactly one device"):
        m.finalize_registration()


def test_deferred_stage_is_blocked():
    m = SessionManager.start(_meta())
    m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                         descriptor=make_tactile_descriptor()))
    m.finalize_registration()
    with pytest.raises(NotImplementedError, match="deferred"):
        m.mark(SessionState.EVIDENCE_COLLECTED)


def test_illegal_transition_rejected():
    m = SessionManager.start(_meta())
    with pytest.raises(ValidationError, match="illegal transition"):
        m.mark(SessionState.EMITTED)


def test_manager_save_load(tmp_path):
    m = SessionManager.start(_meta())
    m.register_device(DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                         descriptor=make_tactile_descriptor()))
    m.finalize_registration()
    path = m.save(tmp_path / "s.yaml")
    loaded = SessionManager.load(path)
    assert loaded.session.metadata.session_id == "S1"
