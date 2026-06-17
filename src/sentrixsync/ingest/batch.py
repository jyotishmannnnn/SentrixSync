"""SampleBatch — a columnar, per-stream container of Samples.

A SampleBatch holds many `Sample` records from a *single stream* in columnar
form (the timestamp column is a numpy int64 array for efficient synchronization
workloads). It preserves the canonical `Sample` contract exactly: round-tripping
`SampleBatch.from_samples(xs).to_samples() == xs`.

No modality-specific assumptions: payloads are carried by reference/inline only,
identical to `Sample`. This container performs no synchronization.
"""
from __future__ import annotations

from typing import Any, Iterator

import numpy as np

from ..core.device import Sample, validate_stream_monotonic
from ..core.types import (
    ValidationError,
    require,
    require_nonempty_str,
)


class SampleBatch:
    """Columnar view of one stream's samples.

    Required columns: `t_device_us` (int64 array), `payload_ref`/`payload_inline`
    (length-n lists, exactly one non-None per row), and `valid` (bool array).
    Optional columns (`seq`, `t_recv_us`, `confidence`, `meta`) are length-n
    lists when present, or None when no sample carried them.
    """

    __slots__ = ("stream_id", "t_device_us", "payload_ref", "payload_inline",
                 "valid", "seq", "t_recv_us", "confidence", "meta")

    def __init__(
        self,
        stream_id: str,
        t_device_us: Any,
        payload_ref: list[str | None],
        payload_inline: list[Any | None],
        valid: Any | None = None,
        seq: list[int | None] | None = None,
        t_recv_us: list[int | None] | None = None,
        confidence: list[float | None] | None = None,
        meta: list[dict | None] | None = None,
    ):
        self.stream_id = stream_id
        self.t_device_us = np.asarray(t_device_us, dtype=np.int64)
        n = int(self.t_device_us.shape[0])
        self.payload_ref = list(payload_ref)
        self.payload_inline = list(payload_inline)
        self.valid = (np.ones(n, dtype=bool) if valid is None
                      else np.asarray(valid, dtype=bool))
        self.seq = list(seq) if seq is not None else None
        self.t_recv_us = list(t_recv_us) if t_recv_us is not None else None
        self.confidence = list(confidence) if confidence is not None else None
        self.meta = list(meta) if meta is not None else None

    # ---- size ---- #
    def __len__(self) -> int:
        return int(self.t_device_us.shape[0])

    @property
    def n(self) -> int:
        return len(self)

    # ---- validation ---- #
    def validate(self) -> None:
        require_nonempty_str(self.stream_id, "batch.stream_id")
        n = len(self)
        require(self.t_device_us.ndim == 1, "batch.t_device_us must be 1-D")
        for name, col in (("payload_ref", self.payload_ref),
                          ("payload_inline", self.payload_inline)):
            require(len(col) == n, f"batch.{name} length {len(col)} != n={n}")
        require(self.valid.shape == (n,), "batch.valid length mismatch")
        for name, col in (("seq", self.seq), ("t_recv_us", self.t_recv_us),
                          ("confidence", self.confidence), ("meta", self.meta)):
            if col is not None:
                require(len(col) == n, f"batch.{name} length {len(col)} != n={n}")
        # Per-row contract: reconstruct Samples and validate (authoritative).
        samples = self.to_samples()
        for s in samples:
            s.validate()
            require(s.stream_id == self.stream_id,
                    f"batch row stream_id {s.stream_id!r} != {self.stream_id!r}")
        validate_stream_monotonic(samples)

    # ---- conversion ---- #
    def to_samples(self) -> list[Sample]:
        n = len(self)
        out: list[Sample] = []
        for i in range(n):
            out.append(Sample(
                stream_id=self.stream_id,
                t_device_us=int(self.t_device_us[i]),
                payload_ref=self.payload_ref[i],
                payload_inline=self.payload_inline[i],
                seq=None if self.seq is None else self.seq[i],
                t_recv_us=None if self.t_recv_us is None else self.t_recv_us[i],
                valid=bool(self.valid[i]),
                confidence=None if self.confidence is None else self.confidence[i],
                meta=None if self.meta is None else self.meta[i],
            ))
        return out

    def iter_samples(self) -> Iterator[Sample]:
        return iter(self.to_samples())

    @classmethod
    def from_samples(cls, samples: list[Sample], stream_id: str | None = None) -> "SampleBatch":
        if not samples:
            require(stream_id is not None,
                    "from_samples requires stream_id for an empty batch")
            return cls(stream_id, np.empty(0, dtype=np.int64), [], [])
        sid = samples[0].stream_id
        if stream_id is not None:
            require(stream_id == sid,
                    f"from_samples stream_id {stream_id!r} != samples' {sid!r}")
        for s in samples:
            require(s.stream_id == sid,
                    f"from_samples received mixed streams ({sid!r} vs {s.stream_id!r})")
        has_seq = any(s.seq is not None for s in samples)
        has_recv = any(s.t_recv_us is not None for s in samples)
        has_conf = any(s.confidence is not None for s in samples)
        has_meta = any(s.meta is not None for s in samples)
        return cls(
            stream_id=sid,
            t_device_us=np.fromiter((s.t_device_us for s in samples), dtype=np.int64,
                                    count=len(samples)),
            payload_ref=[s.payload_ref for s in samples],
            payload_inline=[s.payload_inline for s in samples],
            valid=np.fromiter((s.valid for s in samples), dtype=bool, count=len(samples)),
            seq=[s.seq for s in samples] if has_seq else None,
            t_recv_us=[s.t_recv_us for s in samples] if has_recv else None,
            confidence=[s.confidence for s in samples] if has_conf else None,
            meta=[s.meta for s in samples] if has_meta else None,
        )
