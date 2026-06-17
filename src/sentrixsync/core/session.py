"""Session — the first-class unit of synchronized capture/generation.

Conforms to docs/SESSION_SCHEMA.md. A Session is self-describing and
reference-based: it holds metadata, device registrations, and pointers to
calibration, timeline, reports, and exports. It never embeds bulk payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .device import DeviceRegistration
from .timeline import SyncReport, TimelineRef, ValidationReport
from .types import (
    CONTRACT_VERSION,
    DeviceRole,
    Origin,
    ParamTier,
    SCHEMA_VERSION,
    SENTRIXSYNC_VERSION,
    Serializable,
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
# SessionMetadata
# --------------------------------------------------------------------------- #
@dataclass
class SessionMetadata(Serializable):
    """Identity and provenance of a session (SESSION_SCHEMA.md §2)."""
    session_id: str
    origin: Origin
    producers: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    contract_version: str = CONTRACT_VERSION
    sentrixsync_version: str = SENTRIXSYNC_VERSION
    created_at: str | None = None
    reference_clock_policy: str = "designated_anchor"
    grid_rate_hz: float | None = None
    rejection_tolerance_us: int | None = None
    notes: str | None = None

    def validate(self) -> None:
        require_nonempty_str(self.session_id, "session.session_id")
        require(isinstance(self.origin, Origin), "session.origin must be an Origin")
        require(isinstance(self.producers, list) and len(self.producers) >= 1
                and all(isinstance(p, str) and p for p in self.producers),
                "session.producers must list at least one producer string")
        require_nonempty_str(self.schema_version, "session.schema_version")
        require(contract_version_supported(self.contract_version),
                f"session.contract_version {self.contract_version!r} is unsupported")
        require_nonempty_str(self.reference_clock_policy, "session.reference_clock_policy")
        if self.grid_rate_hz is not None:
            require_positive(self.grid_rate_hz, "session.grid_rate_hz")
        if self.rejection_tolerance_us is not None:
            require_microseconds(self.rejection_tolerance_us, "session.rejection_tolerance_us")

    @classmethod
    def from_dict(cls, d: dict) -> "SessionMetadata":
        return cls(
            session_id=require_key(d, "session_id", "session"),
            origin=coerce_enum(require_key(d, "origin", "session"), Origin, "session.origin"),
            producers=list(d.get("producers", [])),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            contract_version=d.get("contract_version", CONTRACT_VERSION),
            sentrixsync_version=d.get("sentrixsync_version", SENTRIXSYNC_VERSION),
            created_at=d.get("created_at"),
            reference_clock_policy=d.get("reference_clock_policy", "designated_anchor"),
            grid_rate_hz=d.get("grid_rate_hz"),
            rejection_tolerance_us=d.get("rejection_tolerance_us"),
            notes=d.get("notes"),
        )


# --------------------------------------------------------------------------- #
# CalibrationRef
# --------------------------------------------------------------------------- #
@dataclass
class CalibrationRef(Serializable):
    """Pointer to a calibration artifact (SESSION_SCHEMA.md §4).

    Per decision C6, spatial calibration (intrinsics/extrinsics/hand_eye) may be
    *referenced* here but is NEVER consumed by synchronization logic. This entity
    deliberately exposes no method that reads the artifact.
    """
    calibration_id: str
    kind: str                      # clock_fit | intrinsics | extrinsics | hand_eye | other
    uri: str
    device_id: str | None = None
    device_ids: list[str] | None = None
    tier: ParamTier | None = None
    confidence: float | None = None

    def validate(self) -> None:
        require_nonempty_str(self.calibration_id, "calibration.calibration_id")
        require_nonempty_str(self.kind, "calibration.kind")
        require_nonempty_str(self.uri, "calibration.uri")
        if self.device_ids is not None:
            require(isinstance(self.device_ids, list)
                    and all(isinstance(x, str) and x for x in self.device_ids),
                    "calibration.device_ids must be a list of device id strings")
        if self.tier is not None:
            require(isinstance(self.tier, ParamTier), "calibration.tier must be a ParamTier")
        if self.confidence is not None:
            require_unit_interval(self.confidence, "calibration.confidence")

    @classmethod
    def from_dict(cls, d: dict) -> "CalibrationRef":
        tier = d.get("tier")
        return cls(
            calibration_id=require_key(d, "calibration_id", "calibration"),
            kind=require_key(d, "kind", "calibration"),
            uri=require_key(d, "uri", "calibration"),
            device_id=d.get("device_id"),
            device_ids=d.get("device_ids"),
            tier=coerce_enum(tier, ParamTier, "calibration.tier") if tier is not None else None,
            confidence=d.get("confidence"),
        )


# --------------------------------------------------------------------------- #
# ExportRecord
# --------------------------------------------------------------------------- #
@dataclass
class ExportRecord(Serializable):
    """A produced downstream artifact (SESSION_SCHEMA.md §8)."""
    format: str
    uri: str
    produced_at: str | None = None
    frame_count: int | None = None
    sample_count: int | None = None
    consumer_hint: str | None = None

    def validate(self) -> None:
        require_nonempty_str(self.format, "export.format")
        require_nonempty_str(self.uri, "export.uri")
        for label, v in (("frame_count", self.frame_count), ("sample_count", self.sample_count)):
            if v is not None:
                require(isinstance(v, int) and not isinstance(v, bool) and v >= 0,
                        f"export.{label} must be a non-negative integer")

    @classmethod
    def from_dict(cls, d: dict) -> "ExportRecord":
        return cls(
            format=require_key(d, "format", "export"),
            uri=require_key(d, "uri", "export"),
            produced_at=d.get("produced_at"),
            frame_count=d.get("frame_count"),
            sample_count=d.get("sample_count"),
            consumer_hint=d.get("consumer_hint"),
        )


# --------------------------------------------------------------------------- #
# GroundTruthBlock (synthetic only)
# --------------------------------------------------------------------------- #
@dataclass
class GroundTruthBlock(Serializable):
    """Segregated true clock relationships, for validation ONLY (CONTRACT.md §9).

    Must never be visible to the clock estimator. Present only for synthetic /
    mixed sessions. `clock_models` maps device_id -> {'alpha': ..., 'beta_us': ...}.
    """
    clock_models: dict[str, dict] = field(default_factory=dict)
    note: str | None = None

    def validate(self) -> None:
        require(isinstance(self.clock_models, dict),
                "ground_truth.clock_models must be a mapping")
        for dev_id, model in self.clock_models.items():
            require_nonempty_str(dev_id, "ground_truth.clock_models key")
            require(isinstance(model, dict) and "alpha" in model and "beta_us" in model,
                    f"ground_truth.clock_models[{dev_id}] must contain alpha and beta_us")

    @classmethod
    def from_dict(cls, d: dict) -> "GroundTruthBlock":
        return cls(
            clock_models={str(k): dict(v) for k, v in d.get("clock_models", {}).items()},
            note=d.get("note"),
        )


# --------------------------------------------------------------------------- #
# Session
# --------------------------------------------------------------------------- #
@dataclass
class Session(Serializable):
    """The complete session manifest (SESSION_SCHEMA.md)."""
    metadata: SessionMetadata
    devices: list[DeviceRegistration] = field(default_factory=list)
    calibration_refs: list[CalibrationRef] = field(default_factory=list)
    timeline: TimelineRef | None = None
    sync_report: SyncReport | None = None
    validation_report: ValidationReport | None = None
    exports: list[ExportRecord] = field(default_factory=list)
    ground_truth: GroundTruthBlock | None = None

    # ---- validation ---- #
    def validate(self) -> None:
        require(isinstance(self.metadata, SessionMetadata),
                "session.metadata must be a SessionMetadata")
        self.metadata.validate()

        require(isinstance(self.devices, list) and len(self.devices) >= 1,
                "session.devices must register at least one device")
        device_ids: set[str] = set()
        reference_ids: list[str] = []
        for reg in self.devices:
            require(isinstance(reg, DeviceRegistration),
                    "session.devices entries must be DeviceRegistration")
            reg.validate()
            require(reg.device_id not in device_ids,
                    f"duplicate device_id {reg.device_id!r} in session")
            device_ids.add(reg.device_id)
            if reg.role is DeviceRole.REFERENCE:
                reference_ids.append(reg.device_id)
        require(len(reference_ids) == 1,
                f"exactly one device must have role 'reference' (found {len(reference_ids)})")

        for cal in self.calibration_refs:
            require(isinstance(cal, CalibrationRef),
                    "session.calibration_refs entries must be CalibrationRef")
            cal.validate()

        if self.timeline is not None:
            self.timeline.validate()
        if self.sync_report is not None:
            self.sync_report.validate()
        if self.validation_report is not None:
            self.validation_report.validate()
        for ex in self.exports:
            require(isinstance(ex, ExportRecord), "session.exports entries must be ExportRecord")
            ex.validate()

        # Ground truth is synthetic-only and forbidden for real sessions.
        if self.ground_truth is not None:
            self.ground_truth.validate()
            require(self.metadata.origin in (Origin.SYNTHETIC, Origin.MIXED),
                    "ground_truth may only be present on synthetic/mixed sessions (CONTRACT.md §9)")

        # Cross-consistency: sync_report and timeline must agree on the reference.
        if self.sync_report is not None and self.timeline is not None:
            require(self.sync_report.reference_clock_id == self.timeline.reference_clock_id,
                    "sync_report.reference_clock_id must match timeline.reference_clock_id")

    # ---- convenience ---- #
    def reference_device(self) -> DeviceRegistration | None:
        for reg in self.devices:
            if reg.role is DeviceRole.REFERENCE:
                return reg
        return None

    # ---- (de)serialization ---- #
    def to_dict(self) -> dict:  # explicit to control nested ordering/keys
        out: dict = {"metadata": self.metadata.to_dict(),
                     "devices": [r.to_dict() for r in self.devices]}
        out["calibration_refs"] = [c.to_dict() for c in self.calibration_refs]
        if self.timeline is not None:
            out["timeline"] = self.timeline.to_dict()
        if self.sync_report is not None:
            out["sync_report"] = self.sync_report.to_dict()
        if self.validation_report is not None:
            out["validation_report"] = self.validation_report.to_dict()
        if self.exports:
            out["exports"] = [e.to_dict() for e in self.exports]
        if self.ground_truth is not None:
            out["ground_truth"] = self.ground_truth.to_dict()
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        meta = SessionMetadata.from_dict(require_key(d, "metadata", "session"))
        devices_raw = d.get("devices", [])
        require(isinstance(devices_raw, list), "session.devices must be a list")
        timeline = d.get("timeline")
        sync = d.get("sync_report")
        val = d.get("validation_report")
        gt = d.get("ground_truth")
        return cls(
            metadata=meta,
            devices=[DeviceRegistration.from_dict(r) for r in devices_raw],
            calibration_refs=[CalibrationRef.from_dict(c)
                              for c in d.get("calibration_refs", [])],
            timeline=TimelineRef.from_dict(timeline) if isinstance(timeline, dict) else None,
            sync_report=SyncReport.from_dict(sync) if isinstance(sync, dict) else None,
            validation_report=ValidationReport.from_dict(val) if isinstance(val, dict) else None,
            exports=[ExportRecord.from_dict(e) for e in d.get("exports", [])],
            ground_truth=GroundTruthBlock.from_dict(gt) if isinstance(gt, dict) else None,
        )
