"""Tests for the SampleBatch columnar container."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.core import Sample
from sentrixsync.core.types import ValidationError
from sentrixsync.ingest import SampleBatch


def _samples(n=4, with_optional=False):
    out = []
    for i in range(n):
        out.append(Sample(
            stream_id="s", t_device_us=i * 625, payload_ref=f"memory://d#stream=s&row={i}",
            seq=i if with_optional else None,
            t_recv_us=(i * 625 + 10) if with_optional else None,
            confidence=0.9 if with_optional else None,
            meta={"i": i} if with_optional else None,
        ))
    return out


def test_roundtrip_required_only():
    xs = _samples(with_optional=False)
    b = SampleBatch.from_samples(xs)
    b.validate()
    assert b.to_samples() == xs


def test_roundtrip_with_optional_columns():
    xs = _samples(with_optional=True)
    b = SampleBatch.from_samples(xs)
    b.validate()
    assert b.to_samples() == xs
    assert b.seq is not None and b.confidence is not None


def test_timestamp_column_is_int64_ndarray():
    b = SampleBatch.from_samples(_samples())
    assert isinstance(b.t_device_us, np.ndarray)
    assert b.t_device_us.dtype == np.int64
    assert len(b) == 4 and b.n == 4


def test_optional_columns_absent_when_unused():
    b = SampleBatch.from_samples(_samples(with_optional=False))
    assert b.seq is None and b.t_recv_us is None
    assert b.confidence is None and b.meta is None


def test_empty_batch_requires_stream_id():
    with pytest.raises(ValidationError, match="stream_id"):
        SampleBatch.from_samples([])
    b = SampleBatch.from_samples([], stream_id="s")
    b.validate()
    assert len(b) == 0


def test_mixed_streams_rejected():
    xs = [Sample(stream_id="a", t_device_us=0, payload_ref="memory://d#r=0"),
          Sample(stream_id="b", t_device_us=1, payload_ref="memory://d#r=1")]
    with pytest.raises(ValidationError, match="mixed streams"):
        SampleBatch.from_samples(xs)


def test_validate_catches_monotonic_violation():
    b = SampleBatch(stream_id="s", t_device_us=[100, 50],
                    payload_ref=["memory://d#r=0", "memory://d#r=1"],
                    payload_inline=[None, None])
    with pytest.raises(ValidationError, match="not monotonic"):
        b.validate()


def test_validate_catches_payload_xor_violation():
    b = SampleBatch(stream_id="s", t_device_us=[0],
                    payload_ref=[None], payload_inline=[None])  # neither payload
    with pytest.raises(ValidationError, match="exactly one"):
        b.validate()


def test_column_length_mismatch_rejected():
    b = SampleBatch(stream_id="s", t_device_us=[0, 1],
                    payload_ref=["memory://d#r=0"], payload_inline=[None, None])
    with pytest.raises(ValidationError, match="length"):
        b.validate()
