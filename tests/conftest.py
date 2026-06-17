"""Shared fixtures and builders for SentrixSync tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sentrixsync.ingest import SentrixSimAdapter
from sentrixsync.core import (
    ClockDescriptor,
    DeviceDescriptor,
    DeviceRegistration,
    DeviceRole,
    EvidenceTier,
    Kernel,
    Origin,
    Session,
    SessionMetadata,
    StreamDescriptor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "configs"


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR


# --------------------------------------------------------------------------- #
# Entity builders
# --------------------------------------------------------------------------- #
def make_tactile_descriptor(device_id: str = "glove_L") -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id=device_id,
        modality="tactile",
        producer="sentrixsim",
        is_synthetic=True,
        reference_candidate=True,
        clock=ClockDescriptor(clock_id=f"{device_id}_hub", resolution_us=1,
                              nominal_epoch="session_start"),
        evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
        streams=[
            StreamDescriptor(stream_id="tactile_field", device_id=device_id,
                             kind="tactile_field", kernel=Kernel.CONTINUOUS,
                             payload_kind="bmm350_cluster_uT", units="uT",
                             nominal_rate_hz=400.0, payload_shape=[21, 3],
                             subframe_capable=True),
        ],
    )


def make_camera_descriptor(device_id: str = "ego_cam") -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id=device_id,
        modality="rgb",
        producer="synthetic_vision",
        is_synthetic=True,
        clock=ClockDescriptor(clock_id=f"{device_id}_clock", resolution_us=100,
                              nominal_epoch="device_boot", expected_offset_us=20000),
        evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
        streams=[
            StreamDescriptor(stream_id="image", device_id=device_id, kind="image",
                             kernel=Kernel.HOLD, payload_kind="rgb_frame_uri",
                             units="none", nominal_rate_hz=30.0),
        ],
        calibration_refs=["calib/ego_cam_intrinsics.json"],
    )


def make_sim_adapter(descriptor: DeviceDescriptor | None = None, n: int = 8,
                     base: str | None = None, ground_truth: dict | None = None
                     ) -> SentrixSimAdapter:
    """An in-memory SentrixSimAdapter (memory:// base) for ingestion tests —
    needs no pyarrow and no on-disk data."""
    desc = descriptor or make_tactile_descriptor()
    # Regular 1600 Hz grid (625 us steps) per declared stream.
    timestamps = {sid: np.arange(n, dtype=np.int64) * 625 for sid in desc.stream_ids()}
    return SentrixSimAdapter(desc, timestamps, base or f"memory://{desc.device_id}",
                             ground_truth=ground_truth)


@pytest.fixture
def tactile_descriptor() -> DeviceDescriptor:
    return make_tactile_descriptor()


@pytest.fixture
def camera_descriptor() -> DeviceDescriptor:
    return make_camera_descriptor()


@pytest.fixture
def single_device_session() -> Session:
    """N=1 synthetic tactile session (the Phase A target)."""
    desc = make_tactile_descriptor()
    return Session(
        metadata=SessionMetadata(session_id="01J9SYNTH0001", origin=Origin.SYNTHETIC,
                                 producers=["sentrixsim"], grid_rate_hz=1600,
                                 rejection_tolerance_us=1875),
        devices=[DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                                    descriptor=desc,
                                    stream_refs={"tactile_field": "streams/glove_L/tactile.parquet"})],
    )


@pytest.fixture
def two_device_session() -> Session:
    """Two-device synthetic visuotactile session (the Phase B target)."""
    glove = make_tactile_descriptor()
    cam = make_camera_descriptor()
    return Session(
        metadata=SessionMetadata(session_id="01J9VT00002", origin=Origin.SYNTHETIC,
                                 producers=["sentrixsim", "synthetic_vision"],
                                 grid_rate_hz=1600, rejection_tolerance_us=1875),
        devices=[
            DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                               descriptor=glove,
                               stream_refs={"tactile_field": "streams/glove_L/tactile.parquet"}),
            DeviceRegistration(device_id="ego_cam", role=DeviceRole.FOLLOWER,
                               descriptor=cam,
                               stream_refs={"image": "streams/ego_cam/frames.mcap"}),
        ],
    )
