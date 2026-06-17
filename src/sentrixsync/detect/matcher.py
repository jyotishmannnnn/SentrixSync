"""Cross-device detection matcher.

Associates per-device detections of the same physical fiducial into SyncEvents
that the synchronization core consumes. Device clocks are monotonic, so event
*order* is preserved across devices; when every device detects the same set of
well-separated events, the k-th detection corresponds across devices.

v0.3 policy: require equal detection counts across the matched devices and
associate by sorted order. Unequal counts raise — partial/missed-detection
matching (proximity/RANSAC association) is deferred.
"""
from __future__ import annotations

import numpy as np

from ..core.events import SyncEvent
from ..core.types import EvidenceTier, require, require_nonempty_str, require_positive


def match_detections(detections: dict[str, np.ndarray], *, tier: EvidenceTier,
                     id_prefix: str = "evt") -> list[SyncEvent]:
    """Build SyncEvents from `{device_id: local_detection_times_us}`.

    Requires >= 2 devices and equal detection counts; associates the k-th sorted
    detection across devices into one SyncEvent.
    """
    require(len(detections) >= 2, "matching needs detections from >= 2 devices")
    counts = {d: int(np.asarray(t).shape[0]) for d, t in detections.items()}
    unique_counts = set(counts.values())
    require(len(unique_counts) == 1,
            f"unequal detection counts across devices: {counts} "
            "(partial-detection matching is deferred)")
    k = unique_counts.pop()
    require(k >= 1, "no detections to match")

    sorted_times = {d: np.sort(np.asarray(t, dtype=np.int64)) for d, t in detections.items()}
    for d in sorted_times:
        require_nonempty_str(d, "device_id")

    events: list[SyncEvent] = []
    for i in range(k):
        observations = {d: int(sorted_times[d][i]) for d in sorted_times}
        events.append(SyncEvent(event_id=f"{id_prefix}_{i}", tier=tier,
                                observations=observations, detector="matched"))
    return events


def associate_detections(detections: dict[str, np.ndarray], *, tier: EvidenceTier,
                         association_tolerance_us: float,
                         coarse_clocks: dict[str, tuple[float, float]] | None = None,
                         id_prefix: str = "evt", min_observers: int = 2) -> list[SyncEvent]:
    """Subset-aware event association (replaces equal-count matching).

    Each device may detect a different, partially-overlapping subset of fiducials.
    Detections are pre-aligned into a coarse common frame via `coarse_clocks`
    (a per-device (alpha, beta) — e.g. wall-clock/NTP, ms-class; identity if
    absent), then greedily clustered: detections whose coarse-common times fall
    within `association_tolerance_us` and come from distinct devices form one
    fiducial. Clusters with fewer than `min_observers` devices are dropped (not
    usable for cross-device fitting). No assumption that every device sees every
    event; no modality assumptions.
    """
    require_positive(association_tolerance_us, "association_tolerance_us")
    coarse_clocks = coarse_clocks or {}

    entries: list[tuple[float, str, int]] = []      # (common_t, device, local_t)
    for dev, times in detections.items():
        require_nonempty_str(dev, "device_id")
        a, b = coarse_clocks.get(dev, (1.0, 0.0))
        for lt in np.asarray(times, dtype=np.int64):
            entries.append((a * float(lt) + b, dev, int(lt)))
    entries.sort(key=lambda e: e[0])

    # Greedy centroid clustering: an entry joins the current cluster if it is
    # within tolerance of the running centroid. At most one detection per device
    # per cluster — a duplicate/extra detection from the same device is kept only
    # if it is closer to the centroid than the one already held (robust to
    # duplicates and same-window false positives). Gross false positives that
    # survive into an edge are handled downstream by RANSAC estimation.
    obs: list[dict[str, int]] = []          # device -> chosen local_t
    cobs: list[dict[str, float]] = []       # device -> its common_t (for replace test)
    csum: list[float] = []                  # running sum of common_t
    ccnt: list[int] = []                    # running count

    for ct, dev, lt in entries:
        if obs:
            centroid = csum[-1] / ccnt[-1]
            if abs(ct - centroid) <= association_tolerance_us:
                cur, curc = obs[-1], cobs[-1]
                if dev not in cur:
                    cur[dev] = lt
                    curc[dev] = ct
                    csum[-1] += ct
                    ccnt[-1] += 1
                elif abs(ct - centroid) < abs(curc[dev] - centroid):
                    cur[dev] = lt           # replace with the nearer detection
                    curc[dev] = ct
                continue
        obs.append({dev: lt})
        cobs.append({dev: ct})
        csum.append(ct)
        ccnt.append(1)

    events: list[SyncEvent] = []
    idx = 0
    for o in obs:
        if len(o) >= min_observers:
            events.append(SyncEvent(event_id=f"{id_prefix}_{idx}", tier=tier,
                                    observations=dict(o), detector="associated"))
            idx += 1
    return events
