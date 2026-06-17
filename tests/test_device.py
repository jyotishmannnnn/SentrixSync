"""Tests for device-domain entities and contract rules."""
from __future__ import annotations

import pytest

from sentrixsync.core import (
    ClockDescriptor,
    DeviceDescriptor,
    DeviceRegistration,
    DeviceRole,
    EvidenceTier,
    Kernel,
    Sample,
    StreamDescriptor,
    validate_stream_monotonic,
)
from sentrixsync.core.types import ValidationError


# ---- ClockDescriptor ---- #
def test_clock_descriptor_requires_microsecond_unit():
    with pytest.raises(ValidationError, match="microseconds"):
        ClockDescriptor(clock_id="c", timestamp_unit="nanoseconds").validate()


def test_clock_descriptor_resolution_positive():
    with pytest.raises(ValidationError):
        ClockDescriptor(clock_id="c", resolution_us=0).validate()


# ---- StreamDescriptor ---- #
def test_stream_roundtrip(tactile_descriptor):
    s = tactile_descriptor.streams[0]
    s.validate()
    assert StreamDescriptor.from_dict(s.to_dict()) == s


def test_stream_rejects_bad_rate():
    with pytest.raises(ValidationError):
        StreamDescriptor(stream_id="s", device_id="d", kind="k", kernel=Kernel.HOLD,
                         payload_kind="p", units="u", nominal_rate_hz=-5).validate()


def test_stream_kernel_coercion_from_string():
    d = {"stream_id": "s", "device_id": "d", "kind": "k", "kernel": "continuous",
         "payload_kind": "p", "units": "u"}
    assert StreamDescriptor.from_dict(d).kernel is Kernel.CONTINUOUS


# ---- Sample ---- #
def test_sample_requires_exactly_one_payload():
    with pytest.raises(ValidationError, match="exactly one"):
        Sample(stream_id="s", t_device_us=0).validate()                       # neither
    with pytest.raises(ValidationError, match="exactly one"):
        Sample(stream_id="s", t_device_us=0, payload_ref="u", payload_inline=1).validate()
    Sample(stream_id="s", t_device_us=0, payload_ref="uri://x").validate()    # ok
    Sample(stream_id="s", t_device_us=0, payload_inline=0.1).validate()       # ok (tiny)


def test_sample_timestamp_must_be_int_us():
    with pytest.raises(ValidationError):
        Sample(stream_id="s", t_device_us=1.5, payload_ref="u").validate()


def test_sample_roundtrip():
    s = Sample(stream_id="s", t_device_us=625, payload_ref="uri://x", seq=3,
               t_recv_us=700, confidence=0.9, meta={"k": 1})
    s.validate()
    assert Sample.from_dict(s.to_dict()) == s


# ---- monotonicity (CONTRACT §6.3) ---- #
def test_monotonic_ok_and_violation():
    ok = [Sample("s", 0, payload_ref="a"), Sample("s", 100, payload_ref="b"),
          Sample("s", 100, payload_ref="c", seq=1)]
    # equal timestamps require strictly-increasing seq; first 100 has no seq -> fails
    with pytest.raises(ValidationError, match="seq"):
        validate_stream_monotonic(ok)

    good = [Sample("s", 0, payload_ref="a", seq=0),
            Sample("s", 100, payload_ref="b", seq=1),
            Sample("s", 100, payload_ref="c", seq=2)]
    validate_stream_monotonic(good)

    backwards = [Sample("s", 100, payload_ref="a"), Sample("s", 50, payload_ref="b")]
    with pytest.raises(ValidationError, match="not monotonic"):
        validate_stream_monotonic(backwards)


# ---- DeviceDescriptor ---- #
def test_device_roundtrip(tactile_descriptor):
    tactile_descriptor.validate()
    assert DeviceDescriptor.from_dict(tactile_descriptor.to_dict()) == tactile_descriptor


def test_device_requires_at_least_one_stream(tactile_descriptor):
    tactile_descriptor.streams = []
    with pytest.raises(ValidationError, match="at least one stream"):
        tactile_descriptor.validate()


def test_device_stream_device_id_must_match(tactile_descriptor):
    tactile_descriptor.streams[0].device_id = "other"
    with pytest.raises(ValidationError, match="device_id"):
        tactile_descriptor.validate()


def test_device_rejects_unsupported_contract_version(tactile_descriptor):
    tactile_descriptor.contract_version = "2.0.0"
    with pytest.raises(ValidationError, match="contract_version"):
        tactile_descriptor.validate()


def test_modality_is_open_vocabulary():
    """Modality-neutrality: a brand-new modality must validate and round-trip."""
    d = DeviceDescriptor(
        device_id="lidar0", modality="lidar", producer="future_rig", is_synthetic=False,
        clock=ClockDescriptor(clock_id="lidar0_clk"),
        evidence_tiers=[EvidenceTier.HARDWARE_PTP],
        streams=[StreamDescriptor(stream_id="points", device_id="lidar0", kind="point_cloud",
                                  kernel=Kernel.HOLD, payload_kind="pcd_uri", units="m")],
    )
    d.validate()
    assert DeviceDescriptor.from_dict(d.to_dict()) == d


# ---- DeviceRegistration ---- #
def test_registration_stream_ref_must_be_known_stream(tactile_descriptor):
    reg = DeviceRegistration(device_id="glove_L", role=DeviceRole.REFERENCE,
                             descriptor=tactile_descriptor,
                             stream_refs={"nonexistent": "uri://x"})
    with pytest.raises(ValidationError, match="not a stream"):
        reg.validate()


def test_registration_requires_descriptor_or_ref():
    reg = DeviceRegistration(device_id="d", role=DeviceRole.FOLLOWER)
    with pytest.raises(ValidationError, match="descriptor"):
        reg.validate()
