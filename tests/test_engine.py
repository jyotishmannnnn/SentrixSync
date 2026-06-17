"""Tests for the synchronization engine on a small hand-built case."""
from __future__ import annotations

import numpy as np

from sentrixsync.clock.forward import ForwardClock
from sentrixsync.core.events import SyncEvent
from sentrixsync.core.types import EvidenceTier
from sentrixsync.sync import synchronize
from conftest import make_camera_descriptor, make_tactile_descriptor


def _descriptors():
    return {"glove_L": make_tactile_descriptor(), "ego_cam": make_camera_descriptor()}


def test_engine_recovers_offset_and_builds_reports():
    descriptors = _descriptors()
    ref_id = "glove_L"
    beta = 20000.0
    cam_fwd = ForwardClock(alpha=1.0, beta_us=beta)

    # follower-local sample stream + events; reference is identity.
    cam_local = np.arange(0, 4_000_000, 5000, dtype=np.int64)        # 200 Hz, 4 s
    glove_local = np.arange(0, 4_000_000, 625, dtype=np.int64)       # 1600 Hz
    event_ref = np.array([500_000, 1_500_000, 2_500_000, 3_500_000], dtype=np.int64)
    events = []
    for i, tr in enumerate(event_ref):
        cam_obs = int(round(cam_fwd.local_from_ref(tr)))
        events.append(SyncEvent(f"e{i}", EvidenceTier.SHARED_EVENT,
                                {"glove_L": int(tr), "ego_cam": cam_obs}))

    result = synchronize(
        reference_device_id=ref_id, descriptors=descriptors,
        stream_timestamps={("glove_L", "tactile_field"): glove_local,
                           ("ego_cam", "image"): cam_local},
        sync_events=events, grid_rate_hz=1600, rejection_tolerance_us=6000,
        ground_truth={"glove_L": ForwardClock(), "ego_cam": cam_fwd})

    # offset recovered
    cam_model = result.clock_models["ego_cam"]
    assert abs(cam_model.beta_us - beta) < 1.0
    assert abs(cam_model.alpha - 1.0) < 1e-7
    # reference is identity
    assert result.clock_models["glove_L"].alpha == 1.0

    # reports are well-formed and validate against the core contract
    result.sync_report.validate()
    result.validation_report.validate()
    assert result.sync_report.reference_selection == "designated_anchor"
    assert "ego_cam" in result.sync_report.per_device
    assert result.validation_report.property_checks["grid_monotonic"] == "pass"
    assert result.metrics["roundtrip_accuracy"]["ego_cam"]["alpha_err"] < 1e-6


def test_engine_handles_follower_without_events():
    descriptors = _descriptors()
    glove_local = np.arange(0, 1_000_000, 625, dtype=np.int64)
    cam_local = np.arange(0, 1_000_000, 5000, dtype=np.int64)
    result = synchronize(
        reference_device_id="glove_L", descriptors=descriptors,
        stream_timestamps={("glove_L", "tactile_field"): glove_local,
                           ("ego_cam", "image"): cam_local},
        sync_events=[], grid_rate_hz=1600, rejection_tolerance_us=6000)
    # no evidence -> identity fallback with zero confidence, still valid
    cam = result.clock_models["ego_cam"]
    assert cam.alpha == 1.0 and cam.clock_confidence == 0.0
    result.sync_report.validate()
