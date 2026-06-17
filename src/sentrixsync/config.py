"""Configuration loading for SentrixSync.

Loads YAML configuration into typed objects:
  * `ReferenceConfig` (+ `GateThresholds`) from configs/reference.yaml — the
    reference-clock policy, grid rate, rejection tolerance, and QA gate bands.
  * `DeviceDescriptor` from a configs/devices/*.descriptor.yaml file.
  * Scenario files are forward-model inputs consumed by the (deferred)
    synchronization stages; they are loaded as lightly-validated raw dicts here.

No synchronization logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .core.device import DeviceDescriptor
from .core.types import (
    Serializable,
    require,
    require_key,
    require_microseconds,
    require_nonempty_str,
    require_positive,
    ValidationError,
)


# --------------------------------------------------------------------------- #
# Raw YAML loading
# --------------------------------------------------------------------------- #
def load_yaml(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValidationError(f"config {p} must contain a mapping at the top level")
    return data


# --------------------------------------------------------------------------- #
# Reference configuration
# --------------------------------------------------------------------------- #
@dataclass
class GateThresholds(Serializable):
    """QA gate bands. Defaults reproduce the Data Engine Phase-7 thresholds."""
    release_resid_us: float = 2000.0
    certified_resid_us: float = 500.0
    hardfail_resid_us: float = 5000.0
    min_coverage: float = 0.99
    max_dropout: float = 0.03

    def validate(self) -> None:
        require_positive(self.release_resid_us, "gates.release_resid_us")
        require_positive(self.certified_resid_us, "gates.certified_resid_us")
        require_positive(self.hardfail_resid_us, "gates.hardfail_resid_us")
        require(self.certified_resid_us <= self.release_resid_us <= self.hardfail_resid_us,
                "gates must satisfy certified <= release <= hardfail residual bands")
        require(0.0 <= self.min_coverage <= 1.0, "gates.min_coverage must be in [0, 1]")
        require(0.0 <= self.max_dropout <= 1.0, "gates.max_dropout must be in [0, 1]")

    @classmethod
    def from_dict(cls, d: dict) -> "GateThresholds":
        return cls(
            release_resid_us=d.get("release_resid_us", 2000.0),
            certified_resid_us=d.get("certified_resid_us", 500.0),
            hardfail_resid_us=d.get("hardfail_resid_us", 5000.0),
            min_coverage=d.get("min_coverage", 0.99),
            max_dropout=d.get("max_dropout", 0.03),
        )


@dataclass
class ReferenceConfig(Serializable):
    """Session-level synchronization policy (configs/reference.yaml)."""
    reference_clock_policy: str = "designated_anchor"
    grid_rate_hz: float | None = None
    rejection_tolerance_us: int | None = None
    gates: GateThresholds | None = None

    def validate(self) -> None:
        require_nonempty_str(self.reference_clock_policy, "reference.reference_clock_policy")
        # v0.3 supports designated_anchor (REFERENCE_CLOCK_DECISION.md).
        require(self.reference_clock_policy == "designated_anchor",
                "reference_clock_policy must be 'designated_anchor' in v0.3")
        if self.grid_rate_hz is not None:
            require_positive(self.grid_rate_hz, "reference.grid_rate_hz")
        if self.rejection_tolerance_us is not None:
            require_microseconds(self.rejection_tolerance_us, "reference.rejection_tolerance_us")
        if self.gates is not None:
            self.gates.validate()

    @classmethod
    def from_dict(cls, d: dict) -> "ReferenceConfig":
        gates = d.get("gates")
        return cls(
            reference_clock_policy=d.get("reference_clock_policy", "designated_anchor"),
            grid_rate_hz=d.get("grid_rate_hz"),
            rejection_tolerance_us=d.get("rejection_tolerance_us"),
            gates=GateThresholds.from_dict(gates) if isinstance(gates, dict) else None,
        )


def load_reference_config(path: str | Path, *, validate: bool = True) -> ReferenceConfig:
    cfg = ReferenceConfig.from_dict(load_yaml(path))
    if validate:
        cfg.validate()
    return cfg


# --------------------------------------------------------------------------- #
# Device descriptors
# --------------------------------------------------------------------------- #
def load_device_descriptor(path: str | Path, *, validate: bool = True) -> DeviceDescriptor:
    desc = DeviceDescriptor.from_dict(load_yaml(path))
    if validate:
        desc.validate()
    return desc


# --------------------------------------------------------------------------- #
# Scenarios (forward-model inputs; consumed by deferred sync stages)
# --------------------------------------------------------------------------- #
def load_scenario(path: str | Path) -> dict[str, Any]:
    """Load a scenario config. Lightly validated only: scenarios drive the
    forward-corruption model and synchronization stages that are not yet
    implemented, so we verify shape but do not interpret the parameters here.
    """
    data = load_yaml(path)
    require_key(data, "name", "scenario")
    require_nonempty_str(data["name"], "scenario.name")
    return data
