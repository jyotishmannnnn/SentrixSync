"""Session lifecycle management.

Tracks which stage a session is in and enforces legal transitions. The full
lifecycle (ARCHITECTURE.md §7) is declared here for clarity, but only the stages
that do **not** require synchronization algorithms are executable in this build:

    CREATED -> DEVICES_REGISTERED

The later stages (EVIDENCE_COLLECTED, CLOCKS_ESTIMATED, TIMELINE_BUILT, SCORED)
are part of the deferred synchronization work and are intentionally not callable
yet. `mark()` will refuse to advance into them.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path

from .core.session import (
    CalibrationRef,
    GroundTruthBlock,
    Session,
    SessionMetadata,
)
from .core.device import DeviceRegistration
from .core.types import ValidationError, require
from . import manifest


class SessionState(str, Enum):
    CREATED = "created"
    DEVICES_REGISTERED = "devices_registered"
    EVIDENCE_COLLECTED = "evidence_collected"     # deferred
    CLOCKS_ESTIMATED = "clocks_estimated"         # deferred
    TIMELINE_BUILT = "timeline_built"             # deferred
    SCORED = "scored"                             # deferred
    EMITTED = "emitted"


# Declared legal forward transitions for the whole lifecycle.
_ALLOWED_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.CREATED: {SessionState.DEVICES_REGISTERED},
    SessionState.DEVICES_REGISTERED: {SessionState.EVIDENCE_COLLECTED},
    SessionState.EVIDENCE_COLLECTED: {SessionState.CLOCKS_ESTIMATED},
    SessionState.CLOCKS_ESTIMATED: {SessionState.TIMELINE_BUILT},
    SessionState.TIMELINE_BUILT: {SessionState.SCORED},
    SessionState.SCORED: {SessionState.EMITTED},
    SessionState.EMITTED: set(),
}

# Stages whose machinery is deferred behind the Phase 3 review.
_DEFERRED_STATES: set[SessionState] = {
    SessionState.EVIDENCE_COLLECTED,
    SessionState.CLOCKS_ESTIMATED,
    SessionState.TIMELINE_BUILT,
    SessionState.SCORED,
}


class SessionManager:
    """Builds and tracks a Session through its lifecycle.

    Only registration-era operations are implemented in this build. Persistence
    (save/load) is available at any reached state.
    """

    def __init__(self, session: Session, state: SessionState = SessionState.CREATED):
        self.session = session
        self.state = state

    # ---- construction ---- #
    @classmethod
    def start(cls, metadata: SessionMetadata) -> "SessionManager":
        require(isinstance(metadata, SessionMetadata),
                "SessionManager.start requires a SessionMetadata")
        metadata.validate()
        return cls(Session(metadata=metadata), SessionState.CREATED)

    # ---- registration era ---- #
    def register_device(self, registration: DeviceRegistration) -> "SessionManager":
        require(self.state is SessionState.CREATED,
                f"devices can only be registered in CREATED state (now {self.state.value})")
        require(isinstance(registration, DeviceRegistration),
                "register_device requires a DeviceRegistration")
        registration.validate()
        existing = {r.device_id for r in self.session.devices}
        require(registration.device_id not in existing,
                f"device {registration.device_id!r} already registered")
        self.session.devices.append(registration)
        return self

    def attach_calibration(self, ref: CalibrationRef) -> "SessionManager":
        require(isinstance(ref, CalibrationRef), "attach_calibration requires a CalibrationRef")
        ref.validate()
        self.session.calibration_refs.append(ref)
        return self

    def attach_ground_truth(self, block: GroundTruthBlock) -> "SessionManager":
        require(isinstance(block, GroundTruthBlock),
                "attach_ground_truth requires a GroundTruthBlock")
        block.validate()
        self.session.ground_truth = block
        return self

    def finalize_registration(self) -> "SessionManager":
        """Validate the registered set and advance to DEVICES_REGISTERED.

        This runs the full Session validation (exactly-one-reference, unique
        device ids, ground-truth/origin consistency, etc.).
        """
        require(self.state is SessionState.CREATED,
                f"finalize_registration only valid from CREATED (now {self.state.value})")
        self.session.validate()
        self.state = SessionState.DEVICES_REGISTERED
        return self

    # ---- generic transition guard ---- #
    def mark(self, target: SessionState) -> "SessionManager":
        """Advance the lifecycle state, enforcing legal transitions. Refuses to
        enter stages whose synchronization machinery is deferred."""
        require(target in _ALLOWED_TRANSITIONS[self.state],
                f"illegal transition {self.state.value} -> {target.value}")
        if target in _DEFERRED_STATES:
            raise NotImplementedError(
                f"stage {target.value!r} requires synchronization algorithms, "
                "which are deferred until the Phase 3 review is approved")
        self.state = target
        return self

    # ---- persistence ---- #
    def save(self, path: str | Path, *, validate: bool = True) -> Path:
        return manifest.save_session(self.session, path, validate=validate)

    @classmethod
    def load(cls, path: str | Path, *, validate: bool = True,
             state: SessionState = SessionState.CREATED) -> "SessionManager":
        return cls(manifest.load_session(path, validate=validate), state)
