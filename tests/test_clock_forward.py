"""Tests for the forward clock-corruption model."""
from __future__ import annotations

import numpy as np

from sentrixsync.clock import (
    ForwardClock,
    bernoulli_keep_mask,
    enforce_monotonic_int_us,
    gilbert_keep_mask,
    quantize_us,
)


def test_forward_clock_inverse_roundtrip():
    fc = ForwardClock.from_offset_skew(offset_us=20000, skew_ppm=50.0)
    t_ref = np.array([0.0, 1e6, 5e6])
    back = fc.ref_from_local(fc.local_from_ref(t_ref))
    assert np.allclose(back, t_ref, atol=1e-6)
    assert fc.alpha == 1.0 + 50e-6 and fc.beta_us == 20000


def test_quantize():
    t = np.array([0.0, 1234.0, 1999.0])
    q = quantize_us(t, 1000)
    assert np.array_equal(q, np.array([0.0, 1000.0, 2000.0]))


def test_enforce_monotonic_strictly_increasing():
    t = np.array([0.0, 100.0, 100.0, 90.0, 300.0])
    out = enforce_monotonic_int_us(t)
    assert out.dtype == np.int64
    assert np.all(np.diff(out) > 0)
    assert out[0] >= 0


def test_bernoulli_loss_rate_approximate():
    rng = np.random.default_rng(0)
    keep = bernoulli_keep_mask(20000, 0.3, rng)
    loss = 1.0 - keep.mean()
    assert abs(loss - 0.3) < 0.02
    assert keep.dtype == bool


def test_bernoulli_zero_loss_keeps_all():
    keep = bernoulli_keep_mask(100, 0.0, np.random.default_rng(0))
    assert keep.all()


def _max_run(mask: np.ndarray) -> int:
    best = run = 0
    for v in mask:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def test_gilbert_is_deterministic_and_bursty():
    a = gilbert_keep_mask(5000, 0.02, 0.3, np.random.default_rng(1))
    b = gilbert_keep_mask(5000, 0.02, 0.3, np.random.default_rng(1))
    assert np.array_equal(a, b)                      # deterministic with seed
    dropped = ~a
    assert dropped.sum() > 0
    assert _max_run(dropped) >= 2                     # bursty: consecutive drops
