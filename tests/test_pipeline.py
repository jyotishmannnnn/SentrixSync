"""Tests for the session ingestion pipeline and reference-role selection."""
from __future__ import annotations

import pytest

from sentrixsync.core import (
    ClockDescriptor,
    DeviceDescriptor,
    DeviceRole,
    EvidenceTier,
    Kernel,
    Origin,
    SessionMetadata,
    StreamDescriptor,
)
from sentrixsync.core.types import ValidationError
from sentrixsync.ingest import ingest_session, select_reference
from conftest import make_camera_descriptor, make_sim_adapter, make_tactile_descriptor


def _meta(origin=Origin.SYNTHETIC, producers=("sentrixsim",)):
    return SessionMetadata(session_id="ING1", origin=origin, producers=list(producers),
                           grid_rate_hz=1600)


# ---- reference selection ---- #
def test_select_reference_prefers_candidate_highest_rate():
    glove = make_tactile_descriptor()       # candidate, 400 Hz
    cam = make_camera_descriptor()          # not candidate, 30 Hz
    assert select_reference([glove, cam]) == "glove_L"
    assert select_reference([cam, glove]) == "glove_L"   # order-independent


def test_select_reference_hardware_ptp_wins():
    ptp = DeviceDescriptor(
        device_id="tracker", modality="pose", producer="rig", is_synthetic=False,
        reference_candidate=False,
        clock=ClockDescriptor(clock_id="tracker_clk"),
        evidence_tiers=[EvidenceTier.HARDWARE_PTP],
        streams=[StreamDescriptor(stream_id="pose6d", device_id="tracker", kind="pose6d",
                                  kernel=Kernel.CONTINUOUS, payload_kind="pose_uri",
                                  units="m", nominal_rate_hz=120.0)])
    glove = make_tactile_descriptor()       # candidate but only shared_event
    assert select_reference([glove, ptp]) == "tracker"


def test_select_reference_lexicographic_tiebreak():
    a = make_tactile_descriptor("aaa")
    b = make_tactile_descriptor("bbb")
    assert select_reference([b, a]) == "aaa"


# ---- ingestion ---- #
def test_ingest_two_devices_builds_valid_session():
    glove = make_sim_adapter(make_tactile_descriptor(), n=8,
                             ground_truth={"alpha": 1.0, "beta_us": 0.0})
    cam = make_sim_adapter(make_camera_descriptor(), n=4)
    result = ingest_session(_meta(producers=("sentrixsim", "synthetic_vision")),
                            [glove, cam])
    result.session.validate()
    # reference role assigned to the glove (candidate, higher rate)
    assert result.session.reference_device().device_id == "glove_L"
    roles = {r.device_id: r.role for r in result.session.devices}
    assert roles["ego_cam"] is DeviceRole.FOLLOWER
    # batches captured per (device, stream)
    assert len(result.batch("glove_L", "tactile_field")) == 8
    assert len(result.batch("ego_cam", "image")) == 4
    assert result.total_samples() == 12


def test_ingest_attaches_ground_truth_for_synthetic():
    glove = make_sim_adapter(ground_truth={"alpha": 1.0, "beta_us": 0.0})
    result = ingest_session(_meta(), [glove])
    assert result.session.ground_truth is not None
    assert result.session.ground_truth.clock_models["glove_L"]["beta_us"] == 0.0


def test_ingest_stream_refs_recorded():
    glove = make_sim_adapter()
    result = ingest_session(_meta(), [glove])
    reg = result.session.reference_device()
    assert reg.stream_refs["tactile_field"] == "memory://glove_L#stream=tactile_field"


def test_duplicate_device_ids_across_adapters_rejected():
    a = make_sim_adapter(make_tactile_descriptor("dup"))
    b = make_sim_adapter(make_tactile_descriptor("dup"))
    with pytest.raises(ValidationError, match="duplicate device_id"):
        ingest_session(_meta(), [a, b])


def test_explicit_reference_device_id_honored():
    glove = make_sim_adapter(make_tactile_descriptor(), n=4)
    cam = make_sim_adapter(make_camera_descriptor(), n=4)
    result = ingest_session(_meta(producers=("sentrixsim", "synthetic_vision")),
                            [glove, cam], reference_device_id="ego_cam")
    assert result.session.reference_device().device_id == "ego_cam"


def test_invalid_reference_device_id_rejected():
    glove = make_sim_adapter()
    with pytest.raises(ValidationError, match="not among adapters"):
        ingest_session(_meta(), [glove], reference_device_id="ghost")


def test_adapters_closed_after_ingest():
    glove = make_sim_adapter(n=4)
    ingest_session(_meta(), [glove])
    # cursor map cleared on close -> reading now fails
    with pytest.raises(Exception):
        glove.read("tactile_field")


def test_ground_truth_on_real_session_rejected():
    # A synthetic adapter exposing ground truth on a REAL-origin session must fail
    glove = make_sim_adapter(ground_truth={"alpha": 1.0, "beta_us": 0.0})
    with pytest.raises(ValidationError, match="synthetic"):
        ingest_session(_meta(origin=Origin.REAL), [glove])
