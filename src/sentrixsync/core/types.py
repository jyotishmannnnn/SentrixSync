"""Foundational primitives shared by all SentrixSync core entities.

Conforms to docs/CONTRACT.md and docs/SESSION_SCHEMA.md. Contains **no
synchronization algorithms** — only version constants, enums, the serialization
base, and validation helpers.

Design notes
------------
* All timestamps are integer microseconds (decision C7). `Microseconds` is an
  alias for `int` used purely for readability.
* Enums subclass `str` so serialization is the bare string value and YAML/JSON
  manifests stay human-readable.
* `Serializable` is a plain mixin (not a dataclass) so dataclass field-ordering
  rules are never disturbed by inheritance. Subclasses are `@dataclass` and get
  `to_dict()` for free; each defines its own explicit `from_dict()` so that
  missing required fields fail loudly (this is our schema-validation-on-load).
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------- #
# Version constants
# --------------------------------------------------------------------------- #
SCHEMA_VERSION = "0.3.0"        # docs/SESSION_SCHEMA.md document-structure version
CONTRACT_VERSION = "1.0.1"      # docs/CONTRACT.md ingestion-contract version
                                # 1.0.1: PATCH — payload_inline + payload-URI grammar clarified
SENTRIXSYNC_VERSION = "0.3.0"   # framework version

# Supported ingestion-contract MAJOR (semver). We accept 1.x.y and reject >=2.
SUPPORTED_CONTRACT_MAJOR = 1

# Readability alias only; all timestamps are integer microseconds (C7).
Microseconds = int


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ValidationError(ValueError):
    """Raised when an entity violates CONTRACT.md / SESSION_SCHEMA.md rules."""


# --------------------------------------------------------------------------- #
# Enums  (open vocabularies — e.g. modality, stream kind — are plain strings)
# --------------------------------------------------------------------------- #
class Kernel(str, Enum):
    """How the timeline core resamples a stream. The ONLY behavioural switch the
    core is permitted to read (CONTRACT.md §2 modality-neutrality rule)."""
    CONTINUOUS = "continuous"   # band-limited interpolation
    HOLD = "hold"               # zero-order hold / latest-at


class EvidenceTier(str, Enum):
    HARDWARE_PTP = "hardware_ptp"
    SHARED_EVENT = "shared_event"
    WALL_CLOCK = "wall_clock"


class DeviceRole(str, Enum):
    REFERENCE = "reference"
    FOLLOWER = "follower"


class Origin(str, Enum):
    SYNTHETIC = "synthetic"
    REAL = "real"
    MIXED = "mixed"


class ParamTier(str, Enum):
    """Parameter provenance, mirroring SentrixSim's registry discipline."""
    KNOWN = "KNOWN"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class GateVerdict(str, Enum):
    CERTIFIED = "certified"
    RELEASE = "release"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"


# --------------------------------------------------------------------------- #
# Version helpers
# --------------------------------------------------------------------------- #
def parse_semver(v: str) -> tuple[int, int, int]:
    """Parse 'MAJOR.MINOR.PATCH' (ignoring any pre-release/build suffix)."""
    if not isinstance(v, str) or not v:
        raise ValidationError(f"version must be a non-empty string, got {v!r}")
    core = v.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if len(parts) < 3:
        raise ValidationError(f"version {v!r} is not MAJOR.MINOR.PATCH")
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as e:
        raise ValidationError(f"version {v!r} has non-integer components") from e


def contract_version_supported(v: str) -> bool:
    """True iff the contract version's MAJOR matches what this build supports."""
    return parse_semver(v)[0] == SUPPORTED_CONTRACT_MAJOR


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def require_nonempty_str(value: Any, name: str) -> None:
    require(isinstance(value, str) and value != "", f"{name} must be a non-empty string")


def require_microseconds(value: Any, name: str, *, allow_negative: bool = False) -> None:
    # bool is a subclass of int; reject it explicitly.
    require(isinstance(value, int) and not isinstance(value, bool),
            f"{name} must be an integer number of microseconds (got {type(value).__name__})")
    if not allow_negative:
        require(value >= 0, f"{name} must be >= 0 microseconds")


def require_unit_interval(value: Any, name: str) -> None:
    require(isinstance(value, (int, float)) and not isinstance(value, bool),
            f"{name} must be a number in [0, 1]")
    require(0.0 <= float(value) <= 1.0, f"{name} must be within [0, 1] (got {value})")


def require_positive(value: Any, name: str) -> None:
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0,
            f"{name} must be a positive number (got {value})")


def require_enum(value: Any, enum_cls: type[Enum], name: str) -> None:
    require(isinstance(value, enum_cls), f"{name} must be a {enum_cls.__name__}")


def coerce_enum(value: Any, enum_cls: type[Enum], name: str) -> Enum:
    """Coerce a stored string (or enum) into `enum_cls`; raise on mismatch."""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as e:
        allowed = ", ".join(m.value for m in enum_cls)
        raise ValidationError(
            f"{name} must be one of [{allowed}] (got {value!r})") from e


def require_key(d: dict, key: str, owner: str) -> Any:
    """Fetch a required field from a manifest dict; raise if absent."""
    if not isinstance(d, dict):
        raise ValidationError(f"{owner} must be a mapping, got {type(d).__name__}")
    if key not in d:
        raise ValidationError(f"{owner} is missing required field '{key}'")
    return d[key]


# --------------------------------------------------------------------------- #
# Serialization base
# --------------------------------------------------------------------------- #
def _encode(value: Any) -> Any:
    """Recursively convert dataclass/enum/containers into JSON/YAML-safe data."""
    if isinstance(value, Serializable):
        return value.to_dict()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):  # a dataclass that isn't Serializable (defensive)
        return {f.name: _encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


class Serializable:
    """Mixin giving every core dataclass a uniform `to_dict()` + `validate()`.

    `to_dict()` drops keys whose value is None so manifests stay clean; empty
    lists/dicts are preserved (they may be semantically meaningful, e.g. an
    explicitly-empty `calibration_refs`). `from_dict()` is defined per subclass.
    """

    def to_dict(self) -> dict:
        out: dict[str, Any] = {}
        for f in fields(self):  # type: ignore[arg-type]
            val = getattr(self, f.name)
            if val is None:
                continue
            out[f.name] = _encode(val)
        return out

    def validate(self) -> None:  # pragma: no cover - overridden by subclasses
        """Raise ValidationError if the entity violates the contract/schema."""
        return None
