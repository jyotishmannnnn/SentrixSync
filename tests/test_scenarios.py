"""End-to-end synthetic scenario tests, gated by the synthetic accuracy budget.

These exercise detector plugins -> matcher -> estimator -> timeline -> metrics,
and compare recovered vs injected clock parameters. The clean and
dual_device_offset thresholds come from docs/SYNTHETIC_ACCURACY_BUDGET.md.
"""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.core import (
    DeviceRegistration,
    DeviceRole,
    Origin,
    Session,
    SessionMetadata,
    TimelineRef,
)
from sentrixsync.core.types import GateVerdict
from sentrixsync.scenarios import PRESETS, build_preset, run_scenario

# CI gating thresholds (docs/SYNTHETIC_ACCURACY_BUDGET.md)
BUDGET = {
    "clean": {"alpha_err": 1e-6, "beta_err_us": 50, "alignment_rmse_us": 100},
    "dual_device_offset": {"alpha_err": 5e-5, "beta_err_us": 500, "alignment_rmse_us": 600},
}


def _follower_accuracy(result):
    rt = result.metrics["roundtrip_accuracy"]
    assert rt, "expected ground-truth round-trip accuracy"
    return next(iter(rt.values()))


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_scenario_runs_and_reports_validate(name):
    result = run_scenario(build_preset(name))
    result.sync_report.validate()
    result.validation_report.validate()
    assert isinstance(result.validation_report.gate_verdict, GateVerdict)
    assert result.metrics["sync_resid_us"] >= 0.0
    assert 0.0 <= result.metrics["coverage_min"] <= 1.0


@pytest.mark.parametrize("name", list(BUDGET))
def test_accuracy_budget_met(name):
    acc = _follower_accuracy(run_scenario(build_preset(name)))
    b = BUDGET[name]
    assert acc["alpha_err"] <= b["alpha_err"], acc
    assert acc["beta_err_us"] <= b["beta_err_us"], acc
    assert acc["alignment_rmse_us"] <= b["alignment_rmse_us"], acc


def test_clean_is_exact_to_floating_point():
    acc = _follower_accuracy(run_scenario(build_preset("clean")))
    assert acc["alpha_err"] == 0.0 and acc["beta_err_us"] == 0.0


def test_offset_scenario_recovers_offset():
    result = run_scenario(build_preset("offset"))
    assert abs(result.clock_models["ego_cam"].beta_us - 20000) < 500


def test_drift_scenario_recovers_skew():
    result = run_scenario(build_preset("drift"))
    assert abs(result.clock_models["ego_cam"].alpha - (1.0 + 25e-6)) < 5e-6


@pytest.mark.parametrize("name,lo,hi", [("loss", 0.02, 0.12), ("burst", 0.005, 0.20)])
def test_loss_scenarios_report_dropout(name, lo, hi):
    result = run_scenario(build_preset(name))
    assert lo <= result.metrics["dropout_max"] <= hi


def test_scenario_reports_attach_to_session():
    """Integration: the engine's reports fit a valid Session manifest."""
    scen = build_preset("dual_device_offset")
    result = run_scenario(scen)
    tl = result.timeline
    devices = []
    for dev_id, desc in scen.descriptors().items():
        role = DeviceRole.REFERENCE if dev_id == scen.reference_device_id else DeviceRole.FOLLOWER
        devices.append(DeviceRegistration(device_id=dev_id, role=role, descriptor=desc))
    session = Session(
        metadata=SessionMetadata(session_id="SCEN1", origin=Origin.SYNTHETIC,
                                 producers=["synthetic"], grid_rate_hz=scen.grid_rate_hz),
        devices=devices,
        timeline=TimelineRef(timeline_id="tl1", reference_clock_id=result.reference_clock_id,
                             grid_rate_hz=scen.grid_rate_hz, t_start_us=tl.t_start_us,
                             t_end_us=tl.t_end_us, n_grid=tl.n_grid),
        sync_report=result.sync_report, validation_report=result.validation_report)
    session.validate()
    assert session.sync_report.reference_clock_id == result.reference_clock_id
