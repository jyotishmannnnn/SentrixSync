"""Synthetic synchronization scenarios (forward-model driven)."""
from __future__ import annotations

from .synthetic import (
    PRESETS,
    FollowerSpec,
    ScenarioDevice,
    ScenarioSpec,
    SyntheticScenario,
    build_preset,
    build_scenario,
    run_scenario,
)
from .multimodal import (
    MULTIMODAL_PRESETS,
    DeviceSpec,
    EventGroup,
    MultimodalScenario,
    MultimodalScenarioSpec,
    build_multimodal_preset,
    build_multimodal_scenario,
    detect_scenario,
    run_multimodal_scenario,
)
from .robustness import (
    CorruptionSpec,
    coarse_clock_sweep,
    compare_affine_vs_piecewise,
    make_piecewise_session,
    run_with_corruption,
)

__all__ = [
    "ScenarioSpec", "FollowerSpec", "SyntheticScenario", "ScenarioDevice",
    "build_scenario", "build_preset", "run_scenario", "PRESETS",
    "MultimodalScenarioSpec", "DeviceSpec", "EventGroup", "MultimodalScenario",
    "build_multimodal_scenario", "build_multimodal_preset", "run_multimodal_scenario",
    "detect_scenario", "MULTIMODAL_PRESETS",
    "CorruptionSpec", "run_with_corruption", "coarse_clock_sweep",
    "make_piecewise_session", "compare_affine_vs_piecewise",
]
