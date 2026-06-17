"""SyncEvent detector plugin framework.

Detectors are modality-specific edge plugins that read a single stream's signal
and emit local detection times. They are the ONLY components allowed to look at
payload content; the synchronization core consumes the resulting SyncEvents only
and never sees a signal. No detector is required by the core — a session with
hardware/PTP evidence needs none.

A detector operates on in-memory arrays `(t_us, signal)`; it performs no payload
resolution (resolvers are out of scope). How signals are obtained (synthetic
generation now, payload resolution later) is not the detector's concern.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

import numpy as np

from ..core.types import EvidenceTier, require, require_nonempty_str

# --- plugin registry --------------------------------------------------------
_REGISTRY: dict[str, type["SyncEventDetector"]] = {}


def register_detector(cls: type["SyncEventDetector"]) -> type["SyncEventDetector"]:
    """Class decorator registering a detector under its `name`."""
    require_nonempty_str(getattr(cls, "name", ""), "detector.name")
    _REGISTRY[cls.name] = cls
    return cls


def get_detector(name: str, **kwargs) -> "SyncEventDetector":
    require(name in _REGISTRY, f"unknown detector {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)


def registered_detectors() -> list[str]:
    return sorted(_REGISTRY)


@dataclass
class Detection:
    """Local-clock detection times (microseconds) for one device's signal."""
    times_us: np.ndarray
    confidences: np.ndarray | None = None

    def __post_init__(self) -> None:
        self.times_us = np.asarray(self.times_us, dtype=np.int64)
        if self.confidences is not None:
            self.confidences = np.asarray(self.confidences, dtype=float)
            require(self.confidences.shape == self.times_us.shape,
                    "Detection.confidences must match times_us length")

    def __len__(self) -> int:
        return int(self.times_us.shape[0])


class SyncEventDetector(abc.ABC):
    """Abstract modality-specific detector. Subclasses set `name` and `tier`."""
    name: str = ""
    tier: EvidenceTier = EvidenceTier.SHARED_EVENT

    @abc.abstractmethod
    def detect(self, t_us: np.ndarray, signal: np.ndarray) -> Detection:
        """Return local detection times from one stream's `(t_us, signal)`."""


def find_impulse_peaks(t_us: np.ndarray, signal: np.ndarray, threshold: float) -> np.ndarray:
    """One peak per supra-threshold region (its argmax). Shared helper for
    impulse-like detectors; deterministic and modality-agnostic in itself."""
    t_us = np.asarray(t_us, dtype=np.int64)
    signal = np.asarray(signal, dtype=float)
    require(t_us.shape == signal.shape and t_us.ndim == 1,
            "t_us/signal must be equal-length 1-D arrays")
    above = signal > threshold
    peaks: list[int] = []
    i, n = 0, signal.size
    while i < n:
        if above[i]:
            j = i
            while j < n and above[j]:
                j += 1
            peaks.append(i + int(np.argmax(signal[i:j])))
            i = j
        else:
            i += 1
    return t_us[np.asarray(peaks, dtype=int)] if peaks else np.empty(0, dtype=np.int64)
