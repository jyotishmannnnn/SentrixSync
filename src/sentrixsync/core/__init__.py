"""SentrixSync core entities — contracts & types only (no algorithms).

Re-exports the canonical entities so callers can `from sentrixsync.core import
Session, DeviceDescriptor, ...` without knowing the module layout.
"""
from __future__ import annotations

from .device import (
    ClockDescriptor,
    DeviceDescriptor,
    DeviceRegistration,
    Sample,
    StreamDescriptor,
    validate_stream_monotonic,
)
from .events import SyncEvent
from .session import (
    CalibrationRef,
    ExportRecord,
    GroundTruthBlock,
    Session,
    SessionMetadata,
)
from .timeline import (
    ClockModel,
    SubframeBuckets,
    SyncReport,
    TimelineRef,
    ValidationReport,
)
from .types import (
    CONTRACT_VERSION,
    SCHEMA_VERSION,
    SENTRIXSYNC_VERSION,
    DeviceRole,
    EvidenceTier,
    GateVerdict,
    Kernel,
    Microseconds,
    Origin,
    ParamTier,
    Serializable,
    ValidationError,
    contract_version_supported,
    parse_semver,
)
from .uri import (
    PayloadURI,
    allowed_schemes,
    build_payload_uri,
    is_payload_uri,
    parse_payload_uri,
    register_scheme,
    validate_payload_uri,
)

__all__ = [
    # types / enums
    "Kernel", "EvidenceTier", "DeviceRole", "Origin", "ParamTier", "GateVerdict",
    "Microseconds", "Serializable", "ValidationError",
    "CONTRACT_VERSION", "SCHEMA_VERSION", "SENTRIXSYNC_VERSION",
    "contract_version_supported", "parse_semver",
    # payload-URI grammar
    "PayloadURI", "allowed_schemes", "build_payload_uri", "is_payload_uri",
    "parse_payload_uri", "register_scheme", "validate_payload_uri",
    # device domain
    "ClockDescriptor", "StreamDescriptor", "Sample", "DeviceDescriptor",
    "DeviceRegistration", "validate_stream_monotonic",
    # events
    "SyncEvent",
    # timeline domain
    "ClockModel", "SubframeBuckets", "TimelineRef", "SyncReport", "ValidationReport",
    # session domain
    "SessionMetadata", "CalibrationRef", "ExportRecord", "GroundTruthBlock", "Session",
]
