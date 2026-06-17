"""Forward (generative) clock-corruption model — synthetic devices only.

Given an ideal schedule in reference time, this produces a device's *as-received*
local-clock timestamps by applying offset, skew (drift), jitter, quantization,
packet delay, and loss (Bernoulli or Gilbert-burst). It is the generator half of
the forward/inverse duality: the estimator must recover what this injected.

This module performs NO estimation. It is used by synthetic scenarios and stress
tests; real devices supply measured timestamps instead.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.types import require


@dataclass(frozen=True)
class ForwardClock:
    """A device's TRUE clock relation to reference time: t_ref = alpha*t_local + beta.

    `alpha` is the skew (rate ratio); `beta_us` the offset. Drift over a session
    is modelled as a non-unit `alpha` (affine — piecewise is deferred).
    """
    alpha: float = 1.0
    beta_us: float = 0.0

    @classmethod
    def from_offset_skew(cls, offset_us: float = 0.0, skew_ppm: float = 0.0) -> "ForwardClock":
        return cls(alpha=1.0 + skew_ppm * 1e-6, beta_us=float(offset_us))

    def ref_from_local(self, t_local_us: np.ndarray | float) -> np.ndarray | float:
        return self.alpha * np.asarray(t_local_us, dtype=float) + self.beta_us

    def local_from_ref(self, t_ref_us: np.ndarray | float) -> np.ndarray | float:
        return (np.asarray(t_ref_us, dtype=float) - self.beta_us) / self.alpha


def quantize_us(t_us: np.ndarray, resolution_us: int) -> np.ndarray:
    require(resolution_us >= 1, "resolution_us must be >= 1")
    return np.round(np.asarray(t_us, dtype=float) / resolution_us) * resolution_us


def add_jitter(t_us: np.ndarray, sigma_us: float, rng: np.random.Generator) -> np.ndarray:
    if sigma_us <= 0:
        return np.asarray(t_us, dtype=float)
    return np.asarray(t_us, dtype=float) + rng.normal(0.0, sigma_us, size=np.shape(t_us))


def enforce_monotonic_int_us(t_us: np.ndarray) -> np.ndarray:
    """Round to int64 µs and force strictly-increasing (min 1 µs gap) so the
    result satisfies the per-stream monotonicity contract even after jitter."""
    t = np.round(np.asarray(t_us, dtype=float)).astype(np.int64)
    if t.size == 0:
        return t
    out = np.maximum.accumulate(t)
    # break exact ties by nudging forward by index where needed
    fix = out + np.arange(t.size, dtype=np.int64)
    # only apply the +arange where ties/decreases occurred, keep values close
    dup = np.zeros(t.size, dtype=bool)
    dup[1:] = out[1:] <= out[:-1]
    if dup.any():
        out = np.maximum.accumulate(np.where(dup, out + np.arange(t.size), out))
    if out[0] < 0:
        out = out - out[0]
    return out.astype(np.int64)


def bernoulli_keep_mask(n: int, p_loss: float, rng: np.random.Generator) -> np.ndarray:
    require(0.0 <= p_loss <= 1.0, "p_loss must be in [0, 1]")
    if p_loss == 0.0:
        return np.ones(n, dtype=bool)
    return rng.random(n) >= p_loss


def gilbert_keep_mask(n: int, p_good_to_bad: float, p_bad_to_good: float,
                      rng: np.random.Generator, *, loss_in_bad: float = 1.0) -> np.ndarray:
    """Two-state Gilbert burst-loss model. In the GOOD state samples are kept; in
    the BAD state they are dropped with probability `loss_in_bad`. Transitions are
    Markovian, producing bursty loss rather than independent loss."""
    require(0.0 <= p_good_to_bad <= 1.0 and 0.0 <= p_bad_to_good <= 1.0,
            "Gilbert transition probabilities must be in [0, 1]")
    keep = np.ones(n, dtype=bool)
    bad = False
    for i in range(n):
        if bad:
            if rng.random() < loss_in_bad:
                keep[i] = False
            if rng.random() < p_bad_to_good:
                bad = False
        else:
            if rng.random() < p_good_to_bad:
                bad = True
                if rng.random() < loss_in_bad:
                    keep[i] = False
    return keep
