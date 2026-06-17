"""Tests for synchronization metrics and gating."""
from __future__ import annotations

import numpy as np

from sentrixsync.clock.forward import ForwardClock
from sentrixsync.config import GateThresholds
from sentrixsync.core.events import SyncEvent
from sentrixsync.core.timeline import ClockModel
from sentrixsync.core.types import EvidenceTier, GateVerdict
from sentrixsync.sync import metrics


def test_event_residual_zero_for_perfect_model():
    model = ClockModel(device_id="cam", ref_clock_id="ref", alpha=1.0, beta_us=1000.0)
    events = [SyncEvent("e0", EvidenceTier.SHARED_EVENT, {"cam": 0, "ref": 1000}),
              SyncEvent("e1", EvidenceTier.SHARED_EVENT, {"cam": 5000, "ref": 6000})]
    assert metrics.event_residual_us(model, events, reference_device_id="ref") == 0.0


def test_roundtrip_accuracy_matches_truth():
    gt = {"cam": ForwardClock(alpha=1.00002, beta_us=15000.0)}
    est = {"cam": ClockModel("cam", "ref", alpha=1.00002, beta_us=15000.0)}
    times = {"cam": np.linspace(0, 8e6, 100)}
    out = metrics.roundtrip_accuracy(est, gt, times, reference_device_id="ref")
    assert out["cam"]["alpha_err"] < 1e-12
    assert out["cam"]["beta_err_us"] < 1e-9
    assert out["cam"]["alignment_rmse_us"] < 1e-6


def _th():
    return GateThresholds(release_resid_us=2000, certified_resid_us=500,
                          hardfail_resid_us=5000, min_coverage=0.99, max_dropout=0.03)


def test_gate_verdicts():
    th = _th()
    assert metrics.gate(sync_resid_us=200, coverage_min=1.0, dropout_max=0.0,
                        thresholds=th)[0] is GateVerdict.CERTIFIED
    assert metrics.gate(sync_resid_us=1200, coverage_min=1.0, dropout_max=0.0,
                        thresholds=th)[0] is GateVerdict.RELEASE
    assert metrics.gate(sync_resid_us=6000, coverage_min=1.0, dropout_max=0.0,
                        thresholds=th)[0] is GateVerdict.BLOCKED
    # good residual but poor coverage -> needs review (not certified/release)
    assert metrics.gate(sync_resid_us=200, coverage_min=0.5, dropout_max=0.0,
                        thresholds=th)[0] is GateVerdict.NEEDS_REVIEW
    # good residual but excessive dropout -> needs review
    assert metrics.gate(sync_resid_us=200, coverage_min=1.0, dropout_max=0.2,
                        thresholds=th)[0] is GateVerdict.NEEDS_REVIEW
