"""Synchronization engine — orchestrates clock-domain reconciliation end to end.

events -> per-follower clock estimation -> correction -> timeline -> as-of join
-> confidence -> metrics -> SyncReport + ValidationReport.

Inputs are plain data (descriptors, device-local sample timestamps, SyncEvents),
so the engine is producer-agnostic. It performs no detection (that is the
detector plugins' job) and no payload resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..clock.forward import ForwardClock
from ..config import GateThresholds
from ..core.device import DeviceDescriptor
from ..core.events import SyncEvent
from ..core.timeline import ClockModel, SyncReport, ValidationReport
from ..core.types import require
from . import metrics
from .confidence import ConfidenceComponents, build_confidence
from .graph import ReconcileDiagnostics, reconcile
from .timeline import BuiltTimeline, CorrectedStream, TimelineBuilder

_STREAM_KEY = "{device}::{stream}"


def _stream_key(device_id: str, stream_id: str) -> str:
    return _STREAM_KEY.format(device=device_id, stream=stream_id)


@dataclass
class SyncResult:
    reference_device_id: str
    reference_clock_id: str
    clock_models: dict[str, ClockModel]
    timeline: BuiltTimeline
    confidence: dict[str, ConfidenceComponents]
    sync_report: SyncReport
    validation_report: ValidationReport
    diagnostics: ReconcileDiagnostics
    metrics: dict = field(default_factory=dict)


def _aggregate_tier(events: list[SyncEvent]) -> str:
    tiers = {e.tier.value for e in events}
    if not tiers:
        return "none"
    return tiers.pop() if len(tiers) == 1 else "mixed"


def synchronize(
    *,
    reference_device_id: str,
    descriptors: dict[str, DeviceDescriptor],
    stream_timestamps: dict[tuple[str, str], np.ndarray],
    sync_events: list[SyncEvent],
    grid_rate_hz: float,
    rejection_tolerance_us: int,
    gates: GateThresholds | None = None,
    ground_truth: dict[str, ForwardClock] | None = None,
    expected_counts: dict[tuple[str, str], int] | None = None,
    robust_estimation: bool = False,
    ransac_threshold_us: float = 1000.0,
    confidence_decay_tau_us: float | None = None,
    min_events: int = 2,
) -> SyncResult:
    require(reference_device_id in descriptors,
            f"reference {reference_device_id!r} not among descriptors")
    gates = gates or GateThresholds()
    ref_desc = descriptors[reference_device_id]
    ref_clock_id = ref_desc.clock.clock_id

    # 1) graph-based clock-domain reconciliation (replaces the star topology).
    #    Reference is identity; every other device is reached via the most
    #    reliable spanning path, possibly transitively. Devices with no path are
    #    reported unreachable (identity, confidence 0) — graceful, not fatal.
    #    `robust_estimation` selects RANSAC edge fits (rejects mis-associated /
    #    false-positive events) over the TLS baseline.
    clock_models, diagnostics = reconcile(
        sync_events, list(descriptors), reference_device_id, ref_clock_id,
        min_events=min_events, method="ransac" if robust_estimation else "tls",
        ransac_threshold_us=ransac_threshold_us)

    # 2) correct each stream to reference time and build the timeline.
    corrected: list[CorrectedStream] = []
    key_to_device: dict[str, str] = {}
    for (dev_id, stream_id), ts in stream_timestamps.items():
        model = clock_models[dev_id]
        t = np.asarray(ts, dtype=float)
        t_ref = np.round(model.alpha * t + model.beta_us).astype(np.int64)
        kernel = next(s.kernel for s in descriptors[dev_id].streams if s.stream_id == stream_id)
        key = _stream_key(dev_id, stream_id)
        corrected.append(CorrectedStream(stream_id=key, t_ref_us=t_ref, kernel=kernel))
        key_to_device[key] = dev_id

    builder = TimelineBuilder(grid_rate_hz, rejection_tolerance_us)
    timeline = builder.build(ref_clock_id, corrected)

    # 3) confidence components per stream (clock component optionally decays with
    #    distance from the device's sync events — long-gap uncertainty modelling).
    device_event_ref = _device_event_ref_times(sync_events, clock_models)
    confidence: dict[str, ConfidenceComponents] = {}
    for key, alignment in timeline.per_stream.items():
        dev_id = key_to_device[key]
        confidence[key] = build_confidence(
            alignment, clock_confidence=clock_models[dev_id].clock_confidence or 0.0,
            grid_us=timeline.grid_us, event_ref_times_us=device_event_ref.get(dev_id),
            decay_tau_us=confidence_decay_tau_us)

    # 4) metrics — topology-agnostic reconciliation residual (no anchor-sees-all
    #    assumption): per event, how well do all observers agree in reference time.
    followers = [d for d in descriptors if d != reference_device_id]
    sync_resid_us = metrics.reconciliation_residual(
        sync_events, clock_models, reference_device_id=reference_device_id)

    coverage = {key: timeline.per_stream[key].coverage() for key in timeline.per_stream}
    coverage_min = min(coverage.values(), default=1.0)

    dropout: dict[str, float] = {}
    if expected_counts:
        for (dev_id, stream_id), exp in expected_counts.items():
            received = int(np.asarray(stream_timestamps.get((dev_id, stream_id), [])).size)
            dropout[_stream_key(dev_id, stream_id)] = (
                0.0 if exp <= 0 else max(0.0, 1.0 - received / exp))
    dropout_max = max(dropout.values(), default=0.0)

    device_sample_times = _device_sample_times(stream_timestamps)
    rt = (metrics.roundtrip_accuracy(clock_models, ground_truth, device_sample_times,
                                     reference_device_id=reference_device_id)
          if ground_truth else None)

    # 5) reports.
    verdict, detail = metrics.gate(sync_resid_us=sync_resid_us, coverage_min=coverage_min,
                                   dropout_max=dropout_max, thresholds=gates)
    sync_report = SyncReport(
        reference_clock_id=ref_clock_id, reference_selection="designated_anchor",
        sync_resid_us=float(sync_resid_us),
        per_device={d: clock_models[d] for d in followers},
        sync_method=_aggregate_tier(sync_events),
        coverage={k: float(v) for k, v in coverage.items()},
        dropout={k: float(v) for k, v in dropout.items()} or None)
    validation_report = ValidationReport(
        gate_verdict=verdict,
        property_checks=_property_checks(timeline),
        roundtrip_accuracy=rt, gate_detail=detail)

    return SyncResult(
        reference_device_id=reference_device_id, reference_clock_id=ref_clock_id,
        clock_models=clock_models, timeline=timeline, confidence=confidence,
        sync_report=sync_report, validation_report=validation_report,
        diagnostics=diagnostics,
        metrics={"sync_resid_us": float(sync_resid_us), "coverage": coverage,
                 "coverage_min": float(coverage_min), "dropout": dropout,
                 "dropout_max": float(dropout_max), "roundtrip_accuracy": rt,
                 "reachable": sorted(diagnostics.reachable),
                 "unreachable": sorted(diagnostics.unreachable),
                 "n_edges": len(diagnostics.edges),
                 "hops": diagnostics.hops})


def _device_event_ref_times(events: list[SyncEvent], models: dict[str, ClockModel]
                            ) -> dict[str, np.ndarray]:
    """Per device, the reference-time positions of the sync events it observed
    (used for confidence decay). Devices with no usable model are skipped."""
    out: dict[str, list[int]] = {}
    for e in events:
        for dev, t_local in e.observations.items():
            m = models.get(dev)
            if m is None:
                continue
            out.setdefault(dev, []).append(m.to_reference(int(t_local)))
    return {d: np.sort(np.asarray(ts, dtype=np.int64)) for d, ts in out.items()}


def _device_sample_times(stream_timestamps: dict[tuple[str, str], np.ndarray]
                         ) -> dict[str, np.ndarray]:
    by_device: dict[str, list[np.ndarray]] = {}
    for (dev_id, _stream_id), ts in stream_timestamps.items():
        by_device.setdefault(dev_id, []).append(np.asarray(ts, dtype=np.int64))
    return {d: (np.concatenate(parts) if parts else np.empty(0, dtype=np.int64))
            for d, parts in by_device.items()}


def _property_checks(timeline: BuiltTimeline) -> dict[str, str]:
    g = timeline.grid_us
    monotonic = bool(g.size <= 1 or np.all(np.diff(g) > 0))
    if g.size > 1:
        dt = np.diff(g)
        bounded = bool(dt.min() >= 1 and dt.max() <= 2 * dt.min())
    else:
        bounded = True
    # by construction the join marks invalid wherever the gap exceeds tolerance.
    no_fabricated = all(bool(np.all(a.interp_confidence[~a.valid] == 0.0))
                        for a in timeline.per_stream.values())
    return {"grid_monotonic": "pass" if monotonic else "fail",
            "bounded_step": "pass" if bounded else "fail",
            "no_fabricated_gaps": "pass" if no_fabricated else "fail"}
