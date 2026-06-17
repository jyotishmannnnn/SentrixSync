"""SyncEvent — the cross-device synchronization fiducial (CONTRACT.md §7).

A SyncEvent records the same physical or logical event as observed (in each
device's own clock) by two or more devices. It is the currency consumed by the
clock estimator — but no estimation happens here; this module defines the entity
only.

Detectors that *produce* SyncEvents are modality-specific edge plugins and live
outside `core`; the core only ever sees the resulting events, never payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .types import (
    EvidenceTier,
    Serializable,
    coerce_enum,
    require,
    require_key,
    require_microseconds,
    require_nonempty_str,
    require_unit_interval,
)


@dataclass
class SyncEvent(Serializable):
    """One fiducial observed across devices.

    `observations` maps device_id -> the event's timestamp in that device's own
    clock (integer microseconds). At least two observations are required for the
    event to be *usable* for cross-device fitting (see `is_usable`).
    """
    event_id: str
    tier: EvidenceTier
    observations: dict[str, int] = field(default_factory=dict)
    detector: str | None = None
    quality: float | None = None
    kind: str | None = None
    meta: dict | None = None

    def validate(self) -> None:
        require_nonempty_str(self.event_id, "sync_event.event_id")
        require(isinstance(self.tier, EvidenceTier), "sync_event.tier must be an EvidenceTier")
        require(isinstance(self.observations, dict) and len(self.observations) >= 1,
                "sync_event.observations must record at least one device observation")
        for dev_id, t in self.observations.items():
            require_nonempty_str(dev_id, "sync_event.observations key (device_id)")
            require_microseconds(t, f"sync_event.observations[{dev_id}]")
        if self.quality is not None:
            require_unit_interval(self.quality, "sync_event.quality")
        if self.meta is not None:
            require(isinstance(self.meta, dict), "sync_event.meta must be a mapping")

    def is_usable(self) -> bool:
        """True iff the event was observed by >= 2 devices (CONTRACT.md §7)."""
        return len(self.observations) >= 2

    def device_ids(self) -> set[str]:
        return set(self.observations.keys())

    @classmethod
    def from_dict(cls, d: dict) -> "SyncEvent":
        obs_raw = require_key(d, "observations", "sync_event")
        require(isinstance(obs_raw, dict), "sync_event.observations must be a mapping")
        return cls(
            event_id=require_key(d, "event_id", "sync_event"),
            tier=coerce_enum(require_key(d, "tier", "sync_event"), EvidenceTier, "sync_event.tier"),
            observations={str(k): v for k, v in obs_raw.items()},
            detector=d.get("detector"),
            quality=d.get("quality"),
            kind=d.get("kind"),
            meta=d.get("meta"),
        )
