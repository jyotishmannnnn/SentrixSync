"""Timeline-domain entities: ClockModel, SubframeBuckets, TimelineRef,
SyncReport, ValidationReport.

These are the *result/reference* structures referenced by a Session
(SESSION_SCHEMA.md §5–§7). This module defines the data shapes only.

Scope note (important)
----------------------
`ClockModel.to_reference()` applies the affine mapping `alpha * t + beta` to a
device-local timestamp. This is the *definition* of an affine clock model, not a
synchronization algorithm: nothing here *estimates* alpha/beta from evidence
(that is the deferred clock-estimation work). Piecewise/segmented mapping is
reserved (decision: affine default, piecewise optional) and intentionally raises
until that work is approved.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .types import (
    EvidenceTier,
    GateVerdict,
    Serializable,
    coerce_enum,
    require,
    require_key,
    require_microseconds,
    require_nonempty_str,
    require_positive,
    require_unit_interval,
)


# --------------------------------------------------------------------------- #
# ClockModel
# --------------------------------------------------------------------------- #
@dataclass
class ClockModel(Serializable):
    """A device's affine map to reference time: t_ref = alpha * t_device + beta.

    `segments` (piecewise affine) is reserved and unused in v0.3. `method` is the
    evidence tier the fit was based on. Confidence/residual summarize fit quality
    but are populated by the (deferred) estimator; here they are just carried.
    """
    device_id: str
    ref_clock_id: str
    alpha: float = 1.0
    beta_us: float = 0.0
    segments: list[dict] | None = None
    method: EvidenceTier | None = None
    fit_residual_us: float | None = None
    n_events: int | None = None
    clock_confidence: float | None = None

    def validate(self) -> None:
        require_nonempty_str(self.device_id, "clock_model.device_id")
        require_nonempty_str(self.ref_clock_id, "clock_model.ref_clock_id")
        require(isinstance(self.alpha, (int, float)) and not isinstance(self.alpha, bool)
                and self.alpha > 0, "clock_model.alpha must be a positive number")
        require(isinstance(self.beta_us, (int, float)) and not isinstance(self.beta_us, bool),
                "clock_model.beta_us must be a number (microseconds)")
        if self.method is not None:
            require(isinstance(self.method, EvidenceTier),
                    "clock_model.method must be an EvidenceTier")
        if self.fit_residual_us is not None:
            require(self.fit_residual_us >= 0, "clock_model.fit_residual_us must be >= 0")
        if self.n_events is not None:
            require(isinstance(self.n_events, int) and self.n_events >= 0,
                    "clock_model.n_events must be a non-negative integer")
        if self.clock_confidence is not None:
            require_unit_interval(self.clock_confidence, "clock_model.clock_confidence")

    def to_reference(self, t_device_us: int) -> int:
        """Definitional clock map to reference microseconds (NOT estimation).

        Affine by default; if `segments` is set (optional piecewise/segmented
        drift), the segment whose [t_lo_us, t_hi_us) contains the timestamp is
        applied (clamped to the last segment outside the covered range)."""
        if self.segments:
            for seg in self.segments:
                if seg["t_lo_us"] <= t_device_us < seg["t_hi_us"]:
                    return int(round(seg["alpha"] * t_device_us + seg["beta_us"]))
            seg = self.segments[-1]
            return int(round(seg["alpha"] * t_device_us + seg["beta_us"]))
        return int(round(self.alpha * t_device_us + self.beta_us))

    @classmethod
    def from_dict(cls, d: dict) -> "ClockModel":
        method = d.get("method")
        return cls(
            device_id=require_key(d, "device_id", "clock_model"),
            ref_clock_id=require_key(d, "ref_clock_id", "clock_model"),
            alpha=d.get("alpha", 1.0),
            beta_us=d.get("beta_us", 0.0),
            segments=d.get("segments"),
            method=coerce_enum(method, EvidenceTier, "clock_model.method") if method is not None else None,
            fit_residual_us=d.get("fit_residual_us"),
            n_events=d.get("n_events"),
            clock_confidence=d.get("clock_confidence"),
        )


# --------------------------------------------------------------------------- #
# SubframeBuckets
# --------------------------------------------------------------------------- #
@dataclass
class SubframeBuckets(Serializable):
    """Describes per-anchor-frame bucketing of a high-rate stream (e.g. tactile
    burst per image frame). The exact resampling/padding rule string is recorded
    for provenance; the rule itself is applied by the (deferred) timeline stage.
    """
    anchor_stream: str
    rule: str
    R: int | None = None

    def validate(self) -> None:
        require_nonempty_str(self.anchor_stream, "subframe_buckets.anchor_stream")
        require_nonempty_str(self.rule, "subframe_buckets.rule")
        if self.R is not None:
            require(isinstance(self.R, int) and self.R > 0,
                    "subframe_buckets.R must be a positive integer")

    @classmethod
    def from_dict(cls, d: dict) -> "SubframeBuckets":
        return cls(
            anchor_stream=require_key(d, "anchor_stream", "subframe_buckets"),
            rule=require_key(d, "rule", "subframe_buckets"),
            R=d.get("R"),
        )


# --------------------------------------------------------------------------- #
# TimelineRef
# --------------------------------------------------------------------------- #
@dataclass
class TimelineRef(Serializable):
    """Pointers to the generated reference timeline (SESSION_SCHEMA.md §5)."""
    timeline_id: str
    reference_clock_id: str
    grid_rate_hz: float
    t_start_us: int | None = None
    t_end_us: int | None = None
    n_grid: int | None = None
    manifest_uri: str | None = None
    aligned_table_uri: str | None = None
    subframe_buckets: SubframeBuckets | None = None

    def validate(self) -> None:
        require_nonempty_str(self.timeline_id, "timeline.timeline_id")
        require_nonempty_str(self.reference_clock_id, "timeline.reference_clock_id")
        require_positive(self.grid_rate_hz, "timeline.grid_rate_hz")
        if self.t_start_us is not None:
            require_microseconds(self.t_start_us, "timeline.t_start_us")
        if self.t_end_us is not None:
            require_microseconds(self.t_end_us, "timeline.t_end_us")
        if self.t_start_us is not None and self.t_end_us is not None:
            require(self.t_end_us >= self.t_start_us,
                    "timeline.t_end_us must be >= t_start_us")
        if self.n_grid is not None:
            require(isinstance(self.n_grid, int) and self.n_grid >= 0,
                    "timeline.n_grid must be a non-negative integer")
        if self.subframe_buckets is not None:
            require(isinstance(self.subframe_buckets, SubframeBuckets),
                    "timeline.subframe_buckets must be a SubframeBuckets")
            self.subframe_buckets.validate()

    @classmethod
    def from_dict(cls, d: dict) -> "TimelineRef":
        sb = d.get("subframe_buckets")
        return cls(
            timeline_id=require_key(d, "timeline_id", "timeline"),
            reference_clock_id=require_key(d, "reference_clock_id", "timeline"),
            grid_rate_hz=require_key(d, "grid_rate_hz", "timeline"),
            t_start_us=d.get("t_start_us"),
            t_end_us=d.get("t_end_us"),
            n_grid=d.get("n_grid"),
            manifest_uri=d.get("manifest_uri"),
            aligned_table_uri=d.get("aligned_table_uri"),
            subframe_buckets=SubframeBuckets.from_dict(sb) if isinstance(sb, dict) else None,
        )


# --------------------------------------------------------------------------- #
# SyncReport
# --------------------------------------------------------------------------- #
@dataclass
class SyncReport(Serializable):
    """How synchronization was achieved and how good it is (SESSION_SCHEMA.md §6).

    `sync_method` is a plain string because it may be a tier value, 'none'
    (single-device), or 'mixed' (different tiers per follower).
    """
    reference_clock_id: str
    reference_selection: str
    sync_resid_us: float
    per_device: dict[str, ClockModel] = field(default_factory=dict)
    sync_method: str | None = None
    coverage: dict[str, float] | None = None
    dropout: dict[str, float] | None = None

    def validate(self) -> None:
        require_nonempty_str(self.reference_clock_id, "sync_report.reference_clock_id")
        require_nonempty_str(self.reference_selection, "sync_report.reference_selection")
        require(isinstance(self.sync_resid_us, (int, float))
                and not isinstance(self.sync_resid_us, bool) and self.sync_resid_us >= 0,
                "sync_report.sync_resid_us must be a number >= 0")
        require(isinstance(self.per_device, dict), "sync_report.per_device must be a mapping")
        for dev_id, cm in self.per_device.items():
            require_nonempty_str(dev_id, "sync_report.per_device key")
            require(isinstance(cm, ClockModel), "sync_report.per_device values must be ClockModel")
            cm.validate()
        for label, table in (("coverage", self.coverage), ("dropout", self.dropout)):
            if table is not None:
                require(isinstance(table, dict), f"sync_report.{label} must be a mapping")
                for k, v in table.items():
                    require_unit_interval(v, f"sync_report.{label}[{k}]")

    @classmethod
    def from_dict(cls, d: dict) -> "SyncReport":
        per = d.get("per_device", {}) or {}
        require(isinstance(per, dict), "sync_report.per_device must be a mapping")
        return cls(
            reference_clock_id=require_key(d, "reference_clock_id", "sync_report"),
            reference_selection=require_key(d, "reference_selection", "sync_report"),
            sync_resid_us=require_key(d, "sync_resid_us", "sync_report"),
            per_device={str(k): ClockModel.from_dict(v) for k, v in per.items()},
            sync_method=d.get("sync_method"),
            coverage=d.get("coverage"),
            dropout=d.get("dropout"),
        )


# --------------------------------------------------------------------------- #
# ValidationReport
# --------------------------------------------------------------------------- #
@dataclass
class ValidationReport(Serializable):
    """Correctness + (synthetic-only) accuracy (SESSION_SCHEMA.md §7)."""
    gate_verdict: GateVerdict
    property_checks: dict[str, str] = field(default_factory=dict)
    roundtrip_accuracy: dict | None = None
    gate_detail: str | None = None

    _CHECK_VALUES = ("pass", "fail")

    def validate(self) -> None:
        require(isinstance(self.gate_verdict, GateVerdict),
                "validation_report.gate_verdict must be a GateVerdict")
        require(isinstance(self.property_checks, dict),
                "validation_report.property_checks must be a mapping")
        for name, result in self.property_checks.items():
            require_nonempty_str(name, "validation_report.property_checks key")
            require(result in self._CHECK_VALUES,
                    f"property_check {name!r} must be 'pass' or 'fail' (got {result!r})")
        if self.roundtrip_accuracy is not None:
            require(isinstance(self.roundtrip_accuracy, dict),
                    "validation_report.roundtrip_accuracy must be a mapping")

    @classmethod
    def from_dict(cls, d: dict) -> "ValidationReport":
        return cls(
            gate_verdict=coerce_enum(require_key(d, "gate_verdict", "validation_report"),
                                     GateVerdict, "validation_report.gate_verdict"),
            property_checks=dict(d.get("property_checks", {})),
            roundtrip_accuracy=d.get("roundtrip_accuracy"),
            gate_detail=d.get("gate_detail"),
        )
