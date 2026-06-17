"""Tests for configuration loading against the repository's own configs/."""
from __future__ import annotations

import pytest

from sentrixsync.config import (
    GateThresholds,
    ReferenceConfig,
    load_device_descriptor,
    load_reference_config,
    load_scenario,
)
from sentrixsync.core import EvidenceTier, Kernel
from sentrixsync.core.types import ValidationError


def test_load_reference_config(config_dir):
    cfg = load_reference_config(config_dir / "reference.yaml")
    assert cfg.reference_clock_policy == "designated_anchor"
    assert cfg.grid_rate_hz == 1600
    assert cfg.rejection_tolerance_us == 1875
    assert cfg.gates.release_resid_us == 2000
    assert cfg.gates.certified_resid_us == 500


def test_reference_config_rejects_non_anchor_policy():
    with pytest.raises(ValidationError, match="designated_anchor"):
        ReferenceConfig(reference_clock_policy="virtual_consensus").validate()


def test_gate_band_ordering_enforced():
    with pytest.raises(ValidationError, match="certified <= release <= hardfail"):
        GateThresholds(release_resid_us=100, certified_resid_us=200, hardfail_resid_us=300).validate()


def test_load_glove_descriptor(config_dir):
    desc = load_device_descriptor(config_dir / "devices" / "glove_L.descriptor.yaml")
    assert desc.device_id == "glove_L"
    assert desc.reference_candidate is True
    assert EvidenceTier.SHARED_EVENT in desc.evidence_tiers
    tactile = desc.streams[0]
    assert tactile.kernel is Kernel.CONTINUOUS
    assert tactile.subframe_capable is True


def test_load_camera_descriptor(config_dir):
    desc = load_device_descriptor(config_dir / "devices" / "ego_cam.descriptor.yaml")
    assert desc.modality == "rgb"
    assert desc.streams[0].kernel is Kernel.HOLD
    # calibration is referenced but the entity exposes no consumer for it (C6)
    assert desc.calibration_refs == ["calib/ego_cam_intrinsics.json"]


@pytest.mark.parametrize("name", ["clean", "dual_device_offset"])
def test_load_scenarios(config_dir, name):
    sc = load_scenario(config_dir / "scenarios" / f"{name}.yaml")
    assert sc["name"] == name
    assert "devices" in sc


def test_scenario_requires_name(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("description: missing name\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="name"):
        load_scenario(bad)
