"""Tactile tap detector — finds impulsive contact spikes in a tactile signal.

A modality-specific plugin (example). The synchronization core never sees this
signal; it consumes only the SyncEvents built from these detections.
"""
from __future__ import annotations

import numpy as np

from ..detector import Detection, SyncEventDetector, find_impulse_peaks, register_detector
from ...core.types import EvidenceTier


@register_detector
class TactileTapDetector(SyncEventDetector):
    name = "tactile_tap"
    tier = EvidenceTier.SHARED_EVENT

    def __init__(self, threshold: float = 0.5):
        self.threshold = float(threshold)

    def detect(self, t_us: np.ndarray, signal: np.ndarray) -> Detection:
        times = find_impulse_peaks(t_us, signal, self.threshold)
        return Detection(times_us=times)
