"""Device-domain entities: ClockDescriptor, StreamDescriptor, Sample,
DeviceDescriptor, and the session-level DeviceRegistration.

Conforms to docs/CONTRACT.md (§3 DeviceDescriptor, §4 StreamDescriptor,
§5 Sample, §6 timestamps) and docs/SESSION_SCHEMA.md (§3 device registration).

A *device is exactly one clock domain*. Nothing here reads `modality` or stream
`kind` to make a decision — those are open-vocabulary metadata only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import (
    CONTRACT_VERSION,
    EvidenceTier,
    DeviceRole,
    Kernel,
    Serializable,
    ValidationError,
    coerce_enum,
    contract_version_supported,
    require,
    require_key,
    require_microseconds,
    require_nonempty_str,
    require_positive,
    require_unit_interval,
)


# --------------------------------------------------------------------------- #
# ClockDescriptor
# --------------------------------------------------------------------------- #
@dataclass
class ClockDescriptor(Serializable):
    """Properties of one device's clock (CONTRACT.md §3)."""
    clock_id: str
    timestamp_unit: str = "microseconds"          # fixed for v0.3 (decision C7)
    resolution_us: int = 1
    nominal_epoch: str | None = None              # device_boot | unix | session_start | unspecified
    expected_offset_us: float | None = None
    expected_skew_ppm: float | None = None
    expected_drift: Any | None = None             # opaque prior; tiered elsewhere

    def validate(self) -> None:
        require_nonempty_str(self.clock_id, "clock.clock_id")
        require(self.timestamp_unit == "microseconds",
                "clock.timestamp_unit must be 'microseconds' for v0.3 (decision C7)")
        require(isinstance(self.resolution_us, int) and self.resolution_us > 0,
                "clock.resolution_us must be a positive integer")

    @classmethod
    def from_dict(cls, d: dict) -> "ClockDescriptor":
        return cls(
            clock_id=require_key(d, "clock_id", "clock"),
            timestamp_unit=d.get("timestamp_unit", "microseconds"),
            resolution_us=d.get("resolution_us", 1),
            nominal_epoch=d.get("nominal_epoch"),
            expected_offset_us=d.get("expected_offset_us"),
            expected_skew_ppm=d.get("expected_skew_ppm"),
            expected_drift=d.get("expected_drift"),
        )


# --------------------------------------------------------------------------- #
# StreamDescriptor
# --------------------------------------------------------------------------- #
@dataclass
class StreamDescriptor(Serializable):
    """One channel from a device (CONTRACT.md §4). `kind`/`payload_kind` are
    opaque to the core; only `kernel`, `nominal_rate_hz`, and `units` carry
    behavioural meaning."""
    stream_id: str
    device_id: str
    kind: str                          # open vocabulary; metadata only
    kernel: Kernel
    payload_kind: str                  # opaque token for downstream resolvers
    units: str
    nominal_rate_hz: float | None = None
    payload_shape: list[int] | None = None
    subframe_capable: bool = False
    quality_floor: float | None = None

    def validate(self) -> None:
        require_nonempty_str(self.stream_id, "stream.stream_id")
        require_nonempty_str(self.device_id, "stream.device_id")
        require_nonempty_str(self.kind, "stream.kind")
        require(isinstance(self.kernel, Kernel), "stream.kernel must be a Kernel")
        require_nonempty_str(self.payload_kind, "stream.payload_kind")
        require_nonempty_str(self.units, "stream.units")
        if self.nominal_rate_hz is not None:
            require_positive(self.nominal_rate_hz, "stream.nominal_rate_hz")
        if self.payload_shape is not None:
            require(isinstance(self.payload_shape, list)
                    and all(isinstance(x, int) and x > 0 for x in self.payload_shape),
                    "stream.payload_shape must be a list of positive integers")
        require(isinstance(self.subframe_capable, bool),
                "stream.subframe_capable must be a bool")
        if self.quality_floor is not None:
            require_unit_interval(self.quality_floor, "stream.quality_floor")

    @classmethod
    def from_dict(cls, d: dict) -> "StreamDescriptor":
        return cls(
            stream_id=require_key(d, "stream_id", "stream"),
            device_id=require_key(d, "device_id", "stream"),
            kind=require_key(d, "kind", "stream"),
            kernel=coerce_enum(require_key(d, "kernel", "stream"), Kernel, "stream.kernel"),
            payload_kind=require_key(d, "payload_kind", "stream"),
            units=require_key(d, "units", "stream"),
            nominal_rate_hz=d.get("nominal_rate_hz"),
            payload_shape=d.get("payload_shape"),
            subframe_capable=d.get("subframe_capable", False),
            quality_floor=d.get("quality_floor"),
        )


# --------------------------------------------------------------------------- #
# Sample
# --------------------------------------------------------------------------- #
@dataclass
class Sample(Serializable):
    """One timestamped record on a stream (CONTRACT.md §5, §6).

    Timestamps are device-local integer microseconds and MUST NOT be
    pre-corrected to reference time. Exactly one of `payload_ref` (URI/handle)
    or `payload_inline` (tiny scalar/fixed value only) must be present.
    """
    stream_id: str
    t_device_us: int
    payload_ref: str | None = None
    payload_inline: Any | None = None
    seq: int | None = None
    t_recv_us: int | None = None
    valid: bool = True
    confidence: float | None = None
    meta: dict | None = None

    def validate(self) -> None:
        require_nonempty_str(self.stream_id, "sample.stream_id")
        require_microseconds(self.t_device_us, "sample.t_device_us")
        has_ref = self.payload_ref is not None
        has_inline = self.payload_inline is not None
        require(has_ref != has_inline,
                "sample must carry exactly one of payload_ref or payload_inline")
        if has_ref:
            require_nonempty_str(self.payload_ref, "sample.payload_ref")
        if self.seq is not None:
            require(isinstance(self.seq, int) and not isinstance(self.seq, bool)
                    and self.seq >= 0, "sample.seq must be a non-negative integer")
        if self.t_recv_us is not None:
            require_microseconds(self.t_recv_us, "sample.t_recv_us")
        require(isinstance(self.valid, bool), "sample.valid must be a bool")
        if self.confidence is not None:
            require_unit_interval(self.confidence, "sample.confidence")
        if self.meta is not None:
            require(isinstance(self.meta, dict), "sample.meta must be a mapping")

    @classmethod
    def from_dict(cls, d: dict) -> "Sample":
        return cls(
            stream_id=require_key(d, "stream_id", "sample"),
            t_device_us=require_key(d, "t_device_us", "sample"),
            payload_ref=d.get("payload_ref"),
            payload_inline=d.get("payload_inline"),
            seq=d.get("seq"),
            t_recv_us=d.get("t_recv_us"),
            valid=d.get("valid", True),
            confidence=d.get("confidence"),
            meta=d.get("meta"),
        )


def validate_stream_monotonic(samples: list[Sample]) -> None:
    """CONTRACT.md §6.3: within a stream, t_device_us is non-decreasing; equal
    timestamps are allowed only when `seq` disambiguates order.

    This is a contract-conformance check on raw ingested samples — not a
    synchronization algorithm.
    """
    prev_t: int | None = None
    prev_seq: int | None = None
    for i, s in enumerate(samples):
        if prev_t is not None:
            if s.t_device_us < prev_t:
                raise ValidationError(
                    f"stream {s.stream_id!r} not monotonic at index {i}: "
                    f"{s.t_device_us} < {prev_t}")
            if s.t_device_us == prev_t:
                require(s.seq is not None and prev_seq is not None and s.seq > prev_seq,
                        f"stream {s.stream_id!r} has equal timestamps at index {i} "
                        f"without a strictly-increasing seq to disambiguate")
        prev_t, prev_seq = s.t_device_us, s.seq


# --------------------------------------------------------------------------- #
# DeviceDescriptor
# --------------------------------------------------------------------------- #
@dataclass
class DeviceDescriptor(Serializable):
    """What a device is (CONTRACT.md §3). One device == one clock domain."""
    device_id: str
    modality: str                          # open vocabulary; metadata only
    producer: str
    is_synthetic: bool
    clock: ClockDescriptor
    evidence_tiers: list[EvidenceTier]
    streams: list[StreamDescriptor]
    reference_candidate: bool = False
    calibration_refs: list[str] = field(default_factory=list)   # URIs; never consumed (C6)
    # Topology provenance (Phase 2). Opaque pass-through: which hardware-revision
    # topology descriptor this device's streams were produced under. NEVER consumed
    # by synchronization logic (same discipline as calibration_refs / decision C6);
    # carried verbatim so downstream (DataEngine) can trace + package it.
    topology_ref: str | None = None        # descriptor version id, e.g. "Mark2_v1"
    topology_hash: str | None = None       # e.g. "sha256:..."
    param_tiers: dict | None = None
    notes: str | None = None
    contract_version: str = CONTRACT_VERSION

    def validate(self) -> None:
        require_nonempty_str(self.device_id, "device.device_id")
        require_nonempty_str(self.modality, "device.modality")  # NOT checked against a list
        require_nonempty_str(self.producer, "device.producer")
        require(isinstance(self.is_synthetic, bool), "device.is_synthetic must be a bool")
        require(contract_version_supported(self.contract_version),
                f"device.contract_version {self.contract_version!r} is unsupported")
        require(isinstance(self.clock, ClockDescriptor), "device.clock must be a ClockDescriptor")
        self.clock.validate()
        require(isinstance(self.evidence_tiers, list) and len(self.evidence_tiers) >= 1,
                "device.evidence_tiers must list at least one tier")
        for t in self.evidence_tiers:
            require(isinstance(t, EvidenceTier), "device.evidence_tiers entries must be EvidenceTier")
        require(isinstance(self.streams, list) and len(self.streams) >= 1,
                "device.streams must contain at least one stream")
        seen: set[str] = set()
        for s in self.streams:
            require(isinstance(s, StreamDescriptor), "device.streams entries must be StreamDescriptor")
            s.validate()
            require(s.device_id == self.device_id,
                    f"stream {s.stream_id!r} device_id {s.device_id!r} != {self.device_id!r}")
            require(s.stream_id not in seen, f"duplicate stream_id {s.stream_id!r}")
            seen.add(s.stream_id)
        require(isinstance(self.reference_candidate, bool),
                "device.reference_candidate must be a bool")
        require(isinstance(self.calibration_refs, list)
                and all(isinstance(x, str) for x in self.calibration_refs),
                "device.calibration_refs must be a list of URI strings")
        for label, v in (("topology_ref", self.topology_ref),
                         ("topology_hash", self.topology_hash)):
            if v is not None:
                require_nonempty_str(v, f"device.{label}")

    def stream_ids(self) -> list[str]:
        return [s.stream_id for s in self.streams]

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceDescriptor":
        tiers_raw = require_key(d, "evidence_tiers", "device")
        require(isinstance(tiers_raw, list), "device.evidence_tiers must be a list")
        streams_raw = require_key(d, "streams", "device")
        require(isinstance(streams_raw, list), "device.streams must be a list")
        return cls(
            device_id=require_key(d, "device_id", "device"),
            modality=require_key(d, "modality", "device"),
            producer=require_key(d, "producer", "device"),
            is_synthetic=require_key(d, "is_synthetic", "device"),
            clock=ClockDescriptor.from_dict(require_key(d, "clock", "device")),
            evidence_tiers=[coerce_enum(t, EvidenceTier, "device.evidence_tiers")
                            for t in tiers_raw],
            streams=[StreamDescriptor.from_dict(s) for s in streams_raw],
            reference_candidate=d.get("reference_candidate", False),
            calibration_refs=list(d.get("calibration_refs", [])),
            topology_ref=d.get("topology_ref"),
            topology_hash=d.get("topology_hash"),
            param_tiers=d.get("param_tiers"),
            notes=d.get("notes"),
            contract_version=d.get("contract_version", CONTRACT_VERSION),
        )


# --------------------------------------------------------------------------- #
# DeviceRegistration (session-level)
# --------------------------------------------------------------------------- #
@dataclass
class DeviceRegistration(Serializable):
    """A device's participation in a session (SESSION_SCHEMA.md §3).

    Either an inline `descriptor` or a `descriptor_ref` pointer must be present.
    `stream_refs` maps stream_id -> URI of the raw samples.
    """
    device_id: str
    role: DeviceRole
    descriptor: DeviceDescriptor | None = None
    descriptor_ref: str | None = None
    stream_refs: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        require_nonempty_str(self.device_id, "registration.device_id")
        require(isinstance(self.role, DeviceRole), "registration.role must be a DeviceRole")
        require(self.descriptor is not None or self.descriptor_ref is not None,
                "registration must carry an inline descriptor or a descriptor_ref")
        if self.descriptor is not None:
            require(isinstance(self.descriptor, DeviceDescriptor),
                    "registration.descriptor must be a DeviceDescriptor")
            self.descriptor.validate()
            require(self.descriptor.device_id == self.device_id,
                    "registration.device_id must match its descriptor")
            known = set(self.descriptor.stream_ids())
            for sid in self.stream_refs:
                require(sid in known,
                        f"stream_ref {sid!r} is not a stream of device {self.device_id!r}")
        if self.descriptor_ref is not None:
            require_nonempty_str(self.descriptor_ref, "registration.descriptor_ref")
        require(isinstance(self.stream_refs, dict)
                and all(isinstance(k, str) and isinstance(v, str)
                        for k, v in self.stream_refs.items()),
                "registration.stream_refs must map stream_id -> URI string")

    @classmethod
    def from_dict(cls, d: dict) -> "DeviceRegistration":
        desc = d.get("descriptor")
        return cls(
            device_id=require_key(d, "device_id", "registration"),
            role=coerce_enum(require_key(d, "role", "registration"), DeviceRole, "registration.role"),
            descriptor=DeviceDescriptor.from_dict(desc) if isinstance(desc, dict) else None,
            descriptor_ref=d.get("descriptor_ref"),
            stream_refs=dict(d.get("stream_refs", {})),
        )
