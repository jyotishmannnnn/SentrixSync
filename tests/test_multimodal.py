"""End-to-end multimodal synchronization tests.

Demonstrates that synchronization stays accurate when NO single device observes
all events and some devices reach the reference only transitively. Per-hop
accuracy budget (multimodal section of docs/SYNTHETIC_ACCURACY_BUDGET.md).
"""
from __future__ import annotations

import pytest

from sentrixsync.core.types import Kernel
from sentrixsync.scenarios import (
    DeviceSpec,
    EventGroup,
    MultimodalScenarioSpec,
    build_multimodal_preset,
    build_multimodal_scenario,
    run_multimodal_scenario,
)

# Per-hop CI budget (recovered vs injected).
DIRECT = {"alpha_err": 8e-5, "beta_err_us": 500, "alignment_rmse_us": 500}
TRANSITIVE = {"alpha_err": 2e-4, "beta_err_us": 1500, "alignment_rmse_us": 1500}


def test_mm5_all_devices_reconciled():
    result = run_multimodal_scenario(build_multimodal_preset("mm_5device"))
    assert result.metrics["unreachable"] == []
    assert set(result.metrics["reachable"]) == {"glove", "imu", "audio", "camera", "mocap"}
    result.sync_report.validate()
    result.validation_report.validate()


def test_mm5_transitive_topology():
    result = run_multimodal_scenario(build_multimodal_preset("mm_5device"))
    hops = result.metrics["hops"]
    assert hops["glove"] == 0
    assert hops["imu"] == 1 and hops["audio"] == 1          # direct to reference
    assert hops["camera"] == 2 and hops["mocap"] == 2        # transitive via imu

    # No single device observes all events: the reference (glove, tap-only) shares
    # NO event with the camera/mocap (flash group) -> no direct edge exists.
    edge_pairs = {frozenset((e.a, e.b)) for e in result.diagnostics.edges}
    assert frozenset(("glove", "camera")) not in edge_pairs
    assert frozenset(("glove", "mocap")) not in edge_pairs


def test_mm5_accuracy_budget_per_hop():
    result = run_multimodal_scenario(build_multimodal_preset("mm_5device"))
    hops = result.metrics["hops"]
    rt = result.metrics["roundtrip_accuracy"]
    for dev, acc in rt.items():
        budget = DIRECT if hops[dev] == 1 else TRANSITIVE
        assert acc["alpha_err"] <= budget["alpha_err"], (dev, acc)
        assert acc["beta_err_us"] <= budget["beta_err_us"], (dev, acc)
        assert acc["alignment_rmse_us"] <= budget["alignment_rmse_us"], (dev, acc)


def test_mm5_timeline_spans_all_streams():
    result = run_multimodal_scenario(build_multimodal_preset("mm_5device"))
    keys = set(result.timeline.per_stream)
    assert len(keys) == 5                                    # one stream per device


def _disconnected_spec():
    return MultimodalScenarioSpec(
        name="disconnected", reference_device_id="A",
        pattern=["shared", "shared", "private"], n_events=30, duration_s=4.0,
        groups={"shared": EventGroup("shared", ("A", "B")),
                "private": EventGroup("private", ("rogue",))},
        devices=[
            DeviceSpec("A", "tactile", 1000.0, "tactile_tap", reference_candidate=True),
            DeviceSpec("B", "imu", 500.0, "tactile_tap", offset_us=8000.0, jitter_us=150.0),
            DeviceSpec("rogue", "rgb", 200.0, "visual_flash", offset_us=30000.0,
                       kernel=Kernel.HOLD),
        ],
        seed=21)


def test_disconnected_device_degrades_gracefully():
    result = run_multimodal_scenario(build_multimodal_scenario(_disconnected_spec()))
    assert "rogue" in result.metrics["unreachable"]         # no shared events -> unreachable
    assert {"A", "B"} <= set(result.metrics["reachable"])
    # rogue still appears with an identity model at confidence 0 (no crash)
    assert result.clock_models["rogue"].clock_confidence == 0.0
    result.sync_report.validate()
