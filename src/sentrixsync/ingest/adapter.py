"""DeviceAdapter — the pull-based ingestion interface (CONTRACT.md).

A DeviceAdapter exposes one device (one clock domain) to SentrixSync. The
interface is deliberately pull-based, synchronous, and deterministic: no
streaming infrastructure, networking, or async transports. This is what makes it
equally usable for an in-memory simulator and a real device that buffers
per-stream samples.

Lifecycle:  open() -> read()/read_batch() per stream -> close()
The adapter may also be used as a context manager.

An adapter MUST NOT pre-correct timestamps to reference time and MUST NOT
fabricate regular sampling — it passes through the device's real, device-local
microsecond timestamps (CONTRACT.md §6).
"""
from __future__ import annotations

import abc

from ..core.device import DeviceDescriptor, Sample
from ..core.types import require
from .batch import SampleBatch


class AdapterError(RuntimeError):
    """Raised on illegal adapter use (e.g. read before open, unknown stream)."""


class DeviceAdapter(abc.ABC):
    """Abstract pull-based device adapter."""

    # ---- required surface ---- #
    @abc.abstractmethod
    def descriptor(self) -> DeviceDescriptor:
        """Return the (validated) DeviceDescriptor for this device."""

    @abc.abstractmethod
    def open(self) -> None:
        """Acquire resources / reset read cursors. Idempotent-safe per impl."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release resources. Safe to call after open() only."""

    @abc.abstractmethod
    def read(self, stream_id: str) -> Sample | None:
        """Return the next Sample for `stream_id`, or None when exhausted."""

    # ---- provided convenience ---- #
    def read_batch(self, stream_id: str, max_samples: int | None = None) -> SampleBatch:
        """Drain (up to `max_samples`) remaining samples of a stream into a
        SampleBatch. Default implementation loops `read()`; concrete adapters may
        override for a columnar fast path."""
        samples: list[Sample] = []
        while max_samples is None or len(samples) < max_samples:
            s = self.read(stream_id)
            if s is None:
                break
            samples.append(s)
        return SampleBatch.from_samples(samples, stream_id=stream_id)

    def stream_ids(self) -> list[str]:
        return self.descriptor().stream_ids()

    def stream_ref(self, stream_id: str) -> str | None:
        """Optional URI base addressing this stream's raw samples (for the
        Session's `stream_refs`). Default: None (in-memory / not addressable)."""
        return None

    def ground_truth(self) -> dict | None:
        """Optional segregated ground-truth clock model for this device, for
        validation only (synthetic adapters). Default: None.

        Shape (when present): {'alpha': float, 'beta_us': float[, 'note': str]}.
        """
        return None

    # ---- context manager ---- #
    def __enter__(self) -> "DeviceAdapter":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class _CursorAdapterBase(DeviceAdapter):
    """Helper base that tracks open-state and a per-stream read cursor.

    Concrete adapters supply per-stream sample lists via `_samples_for` and a
    descriptor via `descriptor`. This keeps `read()`/cursor bookkeeping in one
    place; it implements no synchronization logic.
    """

    def __init__(self) -> None:
        self._opened = False
        self._cursors: dict[str, int] = {}

    def _require_open(self) -> None:
        require(self._opened, "adapter must be open() before reading")

    def _require_known_stream(self, stream_id: str) -> None:
        if stream_id not in set(self.stream_ids()):
            raise AdapterError(f"unknown stream {stream_id!r} for device "
                               f"{self.descriptor().device_id!r}")

    @abc.abstractmethod
    def _samples_for(self, stream_id: str) -> list[Sample]:
        """Return the full ordered sample list for a stream (called on open)."""

    def open(self) -> None:
        self._cursors = {sid: 0 for sid in self.stream_ids()}
        self._opened = True

    def close(self) -> None:
        self._opened = False
        self._cursors = {}

    def read(self, stream_id: str) -> Sample | None:
        self._require_open()
        self._require_known_stream(stream_id)
        samples = self._samples_for(stream_id)
        i = self._cursors[stream_id]
        if i >= len(samples):
            return None
        self._cursors[stream_id] = i + 1
        return samples[i]
