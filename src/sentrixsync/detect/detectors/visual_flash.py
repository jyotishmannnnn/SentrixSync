"""Visual flash detector — finds luminance spikes in a (scalar) brightness signal.

A second example modality plugin, demonstrating that adding a modality is adding
a detector (and an adapter), never a core change. Operates on a 1-D luminance
series; it does not assume any particular image format and the core never sees it.
"""
from __future__ import annotations

import numpy as np

from ..detector import Detection, SyncEventDetector, find_impulse_peaks, register_detector
from ...core.types import EvidenceTier


@register_detector
class VisualFlashDetector(SyncEventDetector):
    name = "visual_flash"
    tier = EvidenceTier.SHARED_EVENT

    def __init__(self, threshold: float = 0.5):
        self.threshold = float(threshold)

    def detect(self, t_us: np.ndarray, signal: np.ndarray) -> Detection:
        times = find_impulse_peaks(t_us, signal, self.threshold)
        return Detection(times_us=times)
