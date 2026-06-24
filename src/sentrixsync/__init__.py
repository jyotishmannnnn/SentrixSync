"""SentrixSync — multi-device synchronization & timeline framework.

This package currently implements only the foundation layers:
  * `core`     — canonical entities (contracts & types), per docs/CONTRACT.md
                 and docs/SESSION_SCHEMA.md.
  * `config`   — configuration loading.
  * `manifest` — session manifest (de)serialization.
  * `lifecycle`— session lifecycle management (create -> register -> finalize).

No synchronization algorithms (clock estimation, timeline generation, metrics)
are implemented yet — those are gated behind the Phase 3 review.
"""
from __future__ import annotations

from .core.types import (
    CONTRACT_VERSION,
    SCHEMA_VERSION,
    SENTRIXSYNC_VERSION,
    ValidationError,
)
from .persist import load_session, load_sync_result, save_sync_result

__version__ = SENTRIXSYNC_VERSION

__all__ = [
    "__version__",
    "CONTRACT_VERSION",
    "SCHEMA_VERSION",
    "SENTRIXSYNC_VERSION",
    "ValidationError",
    "save_sync_result",
    "load_sync_result",
    "load_session",
]
