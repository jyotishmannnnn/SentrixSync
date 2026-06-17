"""Synchronization metrics: residual, coverage, dropout, round-trip accuracy, and
QA gating. Round-trip accuracy is available only when synthetic ground truth is
present (CONTRACT.md §9); it is the measured-accuracy signal the CTO Review asked
for, gated by docs/SYNTHETIC_ACCURACY_BUDGET.md.
"""
from __future__ import annotations

import numpy as np

from ..clock.forward import ForwardClock
from ..config import GateThresholds
from ..core.events import SyncEvent
from ..core.timeline import ClockModel
from ..core.types import GateVerdict


def event_residual_us(model: ClockModel, events: list[SyncEvent], *,
                      reference_device_id: str) -> float:
    """RMS over `events` of (model(t_local_follower) - t_ref), where t_ref is the
    reference device's local observation (reference clock == reference time)."""
    res: list[float] = []
    fid = model.device_id
    for e in events:
        if fid in e.observations and reference_device_id in e.observations:
            est = model.to_reference(int(e.observations[fid]))
            res.append(est - int(e.observations[reference_device_id]))
    if not res:
        return 0.0
    arr = np.asarray(res, dtype=float)
    return float(np.sqrt(np.mean(arr ** 2)))


def reconciliation_residual(events: list[SyncEvent], models: dict[str, ClockModel],
                            *, reference_device_id: str) -> float:
    """Topology-agnostic synchronization residual: for each event, map every
    observing device's local time to reference time and take the spread
    (max - min) across observers; return the RMS of those spreads.

    This generalizes the star-topology residual — it does not require the
    reference device to have observed the event. Devices with no usable model
    (unreachable, confidence 0, non-reference) are excluded.
    """
    spreads: list[float] = []
    for e in events:
        vals: list[float] = []
        for dev, t_local in e.observations.items():
            m = models.get(dev)
            if m is None:
                continue
            if dev == reference_device_id or (m.clock_confidence or 0.0) > 0.0:
                vals.append(float(m.to_reference(int(t_local))))
        if len(vals) >= 2:
            spreads.append(max(vals) - min(vals))
    if not spreads:
        return 0.0
    arr = np.asarray(spreads, dtype=float)
    return float(np.sqrt(np.mean(arr ** 2)))


def roundtrip_accuracy(estimated: dict[str, ClockModel],
                       ground_truth: dict[str, ForwardClock],
                       device_sample_times: dict[str, np.ndarray],
                       *, reference_device_id: str) -> dict[str, dict]:
    """Per-device recovered-vs-true error against the segregated ground truth."""
    out: dict[str, dict] = {}
    for dev, gt in ground_truth.items():
        if dev == reference_device_id:
            continue
        est = estimated.get(dev)
        if est is None:
            continue
        alpha_err = abs(est.alpha - gt.alpha)
        beta_err = abs(est.beta_us - gt.beta_us)
        ts = device_sample_times.get(dev)
        if ts is not None and np.asarray(ts).size:
            t = np.asarray(ts, dtype=float)
            est_ref = est.alpha * t + est.beta_us
            true_ref = gt.alpha * t + gt.beta_us
            rmse = float(np.sqrt(np.mean((est_ref - true_ref) ** 2)))
        else:
            rmse = 0.0
        out[dev] = {"alpha_err": alpha_err, "beta_err_us": beta_err,
                    "alignment_rmse_us": rmse}
    return out


def gate(*, sync_resid_us: float, coverage_min: float, dropout_max: float,
         thresholds: GateThresholds) -> tuple[GateVerdict, str]:
    """Map metrics onto a release/certified/needs_review/blocked verdict."""
    th = thresholds
    if sync_resid_us >= th.hardfail_resid_us:
        return GateVerdict.BLOCKED, f"sync_resid_us={sync_resid_us:.1f} >= hardfail {th.hardfail_resid_us}"
    quality_ok = coverage_min >= th.min_coverage and dropout_max <= th.max_dropout
    if sync_resid_us < th.certified_resid_us and quality_ok:
        return GateVerdict.CERTIFIED, f"sync_resid_us={sync_resid_us:.1f} < certified {th.certified_resid_us}"
    if sync_resid_us < th.release_resid_us and quality_ok:
        return GateVerdict.RELEASE, f"sync_resid_us={sync_resid_us:.1f} < release {th.release_resid_us}"
    detail = (f"sync_resid_us={sync_resid_us:.1f}, coverage_min={coverage_min:.4f}, "
              f"dropout_max={dropout_max:.4f}")
    return GateVerdict.NEEDS_REVIEW, detail
