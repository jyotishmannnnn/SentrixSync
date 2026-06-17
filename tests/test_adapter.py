"""Tests for the DeviceAdapter pull interface (exercised via SentrixSimAdapter)."""
from __future__ import annotations

import pytest

from sentrixsync.ingest import AdapterError
from conftest import make_sim_adapter


def test_descriptor_and_stream_ids():
    a = make_sim_adapter(n=5)
    assert a.descriptor().device_id == "glove_L"
    assert a.stream_ids() == ["tactile_field"]


def test_read_before_open_raises():
    a = make_sim_adapter(n=3)
    with pytest.raises(Exception):  # ValidationError via require()
        a.read("tactile_field")


def test_read_sequence_and_exhaustion():
    a = make_sim_adapter(n=3)
    a.open()
    seen = []
    while True:
        s = a.read("tactile_field")
        if s is None:
            break
        seen.append(s)
    a.close()
    assert [s.t_device_us for s in seen] == [0, 625, 1250]


def test_unknown_stream_raises():
    a = make_sim_adapter(n=2)
    a.open()
    with pytest.raises(AdapterError, match="unknown stream"):
        a.read("nope")
    a.close()


def test_read_batch_full_and_limited():
    a = make_sim_adapter(n=10)
    a.open()
    full = a.read_batch("tactile_field")
    a.close()
    assert len(full) == 10

    a.open()
    first3 = a.read_batch("tactile_field", max_samples=3)
    rest = a.read_batch("tactile_field")           # cursor advanced past first 3
    a.close()
    assert len(first3) == 3 and len(rest) == 7


def test_context_manager_opens_and_closes():
    a = make_sim_adapter(n=4)
    with a as opened:
        b = opened.read_batch("tactile_field")
    assert len(b) == 4
    # after exit, reading should fail (closed)
    with pytest.raises(Exception):
        a.read("tactile_field")


def test_read_batch_matches_read_loop():
    a = make_sim_adapter(n=6)
    a.open()
    loop = []
    while (s := a.read("tactile_field")) is not None:
        loop.append(s)
    a.close()
    a.open()
    batch = a.read_batch("tactile_field")
    a.close()
    assert batch.to_samples() == loop
