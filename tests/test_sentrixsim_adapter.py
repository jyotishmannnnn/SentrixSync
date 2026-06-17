"""Tests for SentrixSimAdapter (in-memory + optional real-Parquet integration)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sentrixsync.core import parse_payload_uri
from sentrixsync.core.types import ValidationError
from sentrixsync.ingest import SentrixSimAdapter
from conftest import REPO_ROOT, make_sim_adapter, make_tactile_descriptor


def test_payload_refs_are_conformant_uris():
    a = make_sim_adapter(n=3, base="memory://glove_L")
    a.open()
    batch = a.read_batch("tactile_field")
    a.close()
    for i, s in enumerate(batch.to_samples()):
        u = parse_payload_uri(s.payload_ref)
        assert u.scheme == "memory"
        assert u.fragment == f"stream=tactile_field&row={i}"


def test_stream_ref_and_ground_truth():
    a = make_sim_adapter(n=2, ground_truth={"alpha": 1.0, "beta_us": 0.0})
    assert a.stream_ref("tactile_field") == "memory://glove_L#stream=tactile_field"
    assert a.stream_ref("nope") is None
    assert a.ground_truth() == {"alpha": 1.0, "beta_us": 0.0}


def test_timestamps_for_unknown_stream_rejected():
    desc = make_tactile_descriptor()
    with pytest.raises(ValidationError, match="unknown stream"):
        SentrixSimAdapter(desc, {"ghost": np.arange(3, dtype=np.int64)}, "memory://glove_L")


def test_payload_base_with_fragment_rejected():
    desc = make_tactile_descriptor()
    with pytest.raises(ValidationError, match="fragment"):
        SentrixSimAdapter(desc, {"tactile_field": np.arange(3, dtype=np.int64)},
                          "memory://glove_L#already")


def test_declared_stream_without_timestamps_yields_empty():
    desc = make_tactile_descriptor()
    a = SentrixSimAdapter(desc, {}, "memory://glove_L")   # no timestamps at all
    a.open()
    b = a.read_batch("tactile_field")
    a.close()
    assert len(b) == 0


# -- Optional integration test against a real SentrixSim Parquet episode -- #
def _find_sentrixsim_parquet() -> Path | None:
    base = REPO_ROOT.parent / "SentrixSim" / "dataset_v0.1" / "parquet"
    if not base.exists():
        return None
    files = list(base.rglob("*.parquet"))
    return files[0] if files else None


def test_from_parquet_real_episode_if_available():
    pq_path = _find_sentrixsim_parquet()
    if pq_path is None:
        pytest.skip("no SentrixSim parquet episode available")
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    desc = make_tactile_descriptor()
    a = SentrixSimAdapter.from_parquet(pq_path, desc)
    a.open()
    batch = a.read_batch("tactile_field")
    a.close()

    n_rows = pq.ParquetFile(pq_path).metadata.num_rows
    assert len(batch) == n_rows
    # payload refs address the parquet file and parse correctly
    u = parse_payload_uri(batch.to_samples()[0].payload_ref)
    assert u.scheme == "parquet"
    # timestamps are non-decreasing (device-local, un-corrected)
    assert bool(np.all(np.diff(batch.t_device_us) >= 0))
