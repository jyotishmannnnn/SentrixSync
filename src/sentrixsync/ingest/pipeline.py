"""Session ingestion pipeline.

Drives a set of DeviceAdapters through open -> descriptor -> read_batch -> close,
assigns the reference *role* by the designated-anchor selection rule, registers
all devices into a finalized Session, and returns the ingested SampleBatches.

Scope boundary (Phase 4): this performs NO clock estimation, offset/drift
estimation, timeline reconstruction, confidence scoring, or synchronization
metrics. Reference *role* selection here is a registration-time policy
(REFERENCE_CLOCK_DECISION.md §2.1) — choosing which device's clock the timeline
will later be expressed in — not any clock-reconciliation math.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.device import DeviceDescriptor, DeviceRegistration
from ..core.session import GroundTruthBlock, Session, SessionMetadata
from ..core.types import DeviceRole, EvidenceTier, ValidationError, require
from ..lifecycle import SessionManager
from .adapter import DeviceAdapter
from .batch import SampleBatch

# Higher rank == better evidence tier (for deterministic reference selection).
_TIER_RANK = {EvidenceTier.HARDWARE_PTP: 3, EvidenceTier.SHARED_EVENT: 2,
              EvidenceTier.WALL_CLOCK: 1}


def _best_tier_rank(desc: DeviceDescriptor) -> int:
    return max((_TIER_RANK.get(t, 0) for t in desc.evidence_tiers), default=0)


def _max_rate(desc: DeviceDescriptor) -> float:
    return max((s.nominal_rate_hz or 0.0 for s in desc.streams), default=0.0)


def select_reference(descriptors: list[DeviceDescriptor]) -> str:
    """Designated-anchor reference-*device* selection (deterministic).

    Priority (REFERENCE_CLOCK_DECISION.md §2.1): a hardware-PTP-capable device,
    then a reference_candidate, then highest native rate, then best evidence
    tier, then a stable lexicographic tie-break. Returns the chosen device_id.
    """
    require(len(descriptors) >= 1, "select_reference requires at least one device")

    def key(d: DeviceDescriptor):
        has_ptp = EvidenceTier.HARDWARE_PTP in d.evidence_tiers
        # Sort DESC on the positive criteria; ASC lexicographic via negation last.
        return (has_ptp, d.reference_candidate, _max_rate(d), _best_tier_rank(d))

    # Choose max by key; break ties by smallest device_id for determinism.
    best = None
    for d in descriptors:
        if best is None:
            best = d
            continue
        bk, dk = key(best), key(d)
        if dk > bk or (dk == bk and d.device_id < best.device_id):
            best = d
    return best.device_id


@dataclass
class IngestionResult:
    """Output of the ingestion pipeline: a finalized Session plus the ingested
    SampleBatches keyed by (device_id, stream_id)."""
    session: Session
    batches: dict[tuple[str, str], SampleBatch] = field(default_factory=dict)

    def batch(self, device_id: str, stream_id: str) -> SampleBatch:
        try:
            return self.batches[(device_id, stream_id)]
        except KeyError:
            raise KeyError(f"no batch for ({device_id!r}, {stream_id!r})") from None

    def total_samples(self) -> int:
        return sum(len(b) for b in self.batches.values())


def ingest_session(
    metadata: SessionMetadata,
    adapters: list[DeviceAdapter],
    *,
    reference_device_id: str | None = None,
    max_samples: int | None = None,
) -> IngestionResult:
    """Ingest a session from a set of adapters into a finalized Session.

    Opens each adapter, registers its device (assigning exactly one reference
    role), reads every stream into a SampleBatch, attaches any synthetic
    ground-truth, finalizes the Session, and closes the adapters.
    """
    require(len(adapters) >= 1, "ingest_session requires at least one adapter")

    descriptors: list[DeviceDescriptor] = []
    opened: list[DeviceAdapter] = []
    try:
        for a in adapters:
            a.open()
            opened.append(a)
            descriptors.append(a.descriptor())

        device_ids = [d.device_id for d in descriptors]
        require(len(set(device_ids)) == len(device_ids),
                f"duplicate device_id across adapters: {device_ids}")

        ref_id = reference_device_id or select_reference(descriptors)
        require(ref_id in device_ids,
                f"reference_device_id {ref_id!r} is not among adapters {device_ids}")

        manager = SessionManager.start(metadata)
        batches: dict[tuple[str, str], SampleBatch] = {}
        gt_models: dict[str, dict] = {}

        for adapter, desc in zip(opened, descriptors):
            role = DeviceRole.REFERENCE if desc.device_id == ref_id else DeviceRole.FOLLOWER
            stream_refs = {}
            for sid in desc.stream_ids():
                ref = adapter.stream_ref(sid)
                if ref is not None:
                    stream_refs[sid] = ref
                batches[(desc.device_id, sid)] = adapter.read_batch(sid, max_samples=max_samples)
            manager.register_device(DeviceRegistration(
                device_id=desc.device_id, role=role, descriptor=desc,
                stream_refs=stream_refs))

            gt = adapter.ground_truth()
            if gt is not None:
                gt_models[desc.device_id] = gt

        if gt_models:
            manager.attach_ground_truth(GroundTruthBlock(clock_models=gt_models))

        manager.finalize_registration()
        return IngestionResult(session=manager.session, batches=batches)
    finally:
        for a in opened:
            a.close()
