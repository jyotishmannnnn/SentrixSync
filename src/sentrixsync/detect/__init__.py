"""Detection layer: SyncEvent detector plugins + cross-device matcher.

Importing this package registers the built-in detectors.
"""
from __future__ import annotations

from .detector import (
    Detection,
    SyncEventDetector,
    find_impulse_peaks,
    get_detector,
    register_detector,
    registered_detectors,
)
from .corrupt import corrupt_detections
from .detectors import TactileTapDetector, VisualFlashDetector  # noqa: F401 (registers)
from .matcher import associate_detections, match_detections

__all__ = [
    "Detection", "SyncEventDetector", "find_impulse_peaks",
    "register_detector", "get_detector", "registered_detectors",
    "TactileTapDetector", "VisualFlashDetector",
    "match_detections", "associate_detections", "corrupt_detections",
]
