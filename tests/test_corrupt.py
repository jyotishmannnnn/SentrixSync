"""Tests for the detection corruption model."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.core.types import ValidationError
from sentrixsync.detect import corrupt_detections


def _det(n=1000, step=1000):
    return {"A": np.arange(0, n * step, step, dtype=np.int64)}


def test_false_negatives_drop_fraction():
    out = corrupt_detections(_det(), duration_us=1_000_000, fn_rate=0.2, seed=0)
    assert 750 < out["A"].size < 850          # ~20% dropped


def test_false_positives_add_detections():
    base = _det().copy()
    out = corrupt_detections(_det(), duration_us=1_000_000, fp_rate=0.5, seed=0)
    assert out["A"].size > base["A"].size


def test_duplicates_add_near_copies():
    out = corrupt_detections(_det(), duration_us=1_000_000, dup_rate=0.5, seed=0)
    assert out["A"].size > 1000


def test_perturbation_moves_timestamps():
    out = corrupt_detections(_det(), duration_us=1_000_000, perturb_us=500, seed=0)
    base = _det()["A"]
    assert out["A"].size == base.size
    assert not np.array_equal(out["A"], base)


def test_output_sorted_and_deterministic():
    a = corrupt_detections(_det(), duration_us=1_000_000, fn_rate=0.1, dup_rate=0.1,
                           fp_rate=0.1, perturb_us=100, seed=7)
    b = corrupt_detections(_det(), duration_us=1_000_000, fn_rate=0.1, dup_rate=0.1,
                           fp_rate=0.1, perturb_us=100, seed=7)
    assert np.array_equal(a["A"], b["A"])
    assert np.all(np.diff(a["A"]) >= 0)


def test_rejects_bad_rates():
    with pytest.raises(ValidationError):
        corrupt_detections(_det(), duration_us=1_000_000, fn_rate=1.5)
