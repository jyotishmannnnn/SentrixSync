"""Tests for timeline-domain entities."""
from __future__ import annotations

import pytest

from sentrixsync.core import (
    ClockModel,
    EvidenceTier,
    GateVerdict,
    SubframeBuckets,
    SyncReport,
    TimelineRef,
    ValidationReport,
)
from sentrixsync.core.types import ValidationError


# ---- ClockModel ---- #
def test_clock_model_roundtrip():
    cm = ClockModel(device_id="ego_cam", ref_clock_id="glove_L_hub", alpha=1.000018,
                    beta_us=20431.0, method=EvidenceTier.SHARED_EVENT,
                    fit_residual_us=280.0, n_events=6, clock_confidence=0.93)
    cm.validate()
    assert ClockModel.from_dict(cm.to_dict()) == cm


def test_clock_model_to_reference_is_affine():
    cm = ClockModel(device_id="d", ref_clock_id="r", alpha=2.0, beta_us=100.0)
    assert cm.to_reference(1000) == 2100


def test_clock_model_alpha_must_be_positive():
    with pytest.raises(ValidationError):
        ClockModel(device_id="d", ref_clock_id="r", alpha=0.0).validate()


def test_clock_model_piecewise_to_reference():
    cm = ClockModel(device_id="d", ref_clock_id="r", segments=[
        {"t_lo_us": -1e18, "t_hi_us": 1000.0, "alpha": 1.0, "beta_us": 0.0},
        {"t_lo_us": 1000.0, "t_hi_us": 1e18, "alpha": 2.0, "beta_us": 100.0},
    ])
    assert cm.to_reference(500) == 500        # first segment
    assert cm.to_reference(2000) == 4100      # second segment: 2*2000+100
    assert cm.to_reference(5000) == 10100     # clamps to last segment


# ---- SubframeBuckets / TimelineRef ---- #
def test_timeline_roundtrip_with_buckets():
    tl = TimelineRef(timeline_id="tl1", reference_clock_id="glove_L_hub", grid_rate_hz=1600,
                     t_start_us=0, t_end_us=1_000_000, n_grid=1600,
                     subframe_buckets=SubframeBuckets(anchor_stream="image",
                                                      rule="fixed R per frame; boundary-padded",
                                                      R=53))
    tl.validate()
    assert TimelineRef.from_dict(tl.to_dict()) == tl


def test_timeline_end_before_start_rejected():
    with pytest.raises(ValidationError, match="t_end_us"):
        TimelineRef(timeline_id="t", reference_clock_id="r", grid_rate_hz=1000,
                    t_start_us=100, t_end_us=50).validate()


def test_timeline_grid_rate_positive():
    with pytest.raises(ValidationError):
        TimelineRef(timeline_id="t", reference_clock_id="r", grid_rate_hz=0).validate()


# ---- SyncReport ---- #
def test_sync_report_roundtrip():
    sr = SyncReport(reference_clock_id="glove_L_hub", reference_selection="designated_anchor",
                    sync_resid_us=280.0, sync_method="shared_event",
                    per_device={"ego_cam": ClockModel(device_id="ego_cam",
                                                       ref_clock_id="glove_L_hub",
                                                       alpha=1.0, beta_us=20431.0)},
                    coverage={"tactile_field": 1.0, "image": 0.998},
                    dropout={"image": 0.002})
    sr.validate()
    assert SyncReport.from_dict(sr.to_dict()) == sr


def test_sync_report_coverage_must_be_unit_interval():
    with pytest.raises(ValidationError):
        SyncReport(reference_clock_id="r", reference_selection="x", sync_resid_us=0,
                   coverage={"s": 1.5}).validate()


def test_sync_report_negative_residual_rejected():
    with pytest.raises(ValidationError):
        SyncReport(reference_clock_id="r", reference_selection="x", sync_resid_us=-1).validate()


# ---- ValidationReport ---- #
def test_validation_report_roundtrip():
    vr = ValidationReport(gate_verdict=GateVerdict.RELEASE,
                          property_checks={"monotonic": "pass", "bounded_step": "pass"},
                          roundtrip_accuracy={"ego_cam": {"alignment_rmse_us": 190}},
                          gate_detail="sync_resid_us=280 -> release band")
    vr.validate()
    assert ValidationReport.from_dict(vr.to_dict()) == vr


def test_validation_report_bad_check_value():
    with pytest.raises(ValidationError, match="pass"):
        ValidationReport(gate_verdict=GateVerdict.RELEASE,
                         property_checks={"monotonic": "maybe"}).validate()
