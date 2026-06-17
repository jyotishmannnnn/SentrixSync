"""Clock layer: forward corruption model + inverse estimators."""
from __future__ import annotations

from .estimate import (
    FitResult,
    clock_confidence,
    fit_affine,
    fit_affine_tls,
    fit_offset,
    fit_piecewise_affine,
    identity_model,
    ransac_affine,
    tls_affine,
)
from .forward import (
    ForwardClock,
    add_jitter,
    bernoulli_keep_mask,
    enforce_monotonic_int_us,
    gilbert_keep_mask,
    quantize_us,
)

__all__ = [
    "ForwardClock", "quantize_us", "add_jitter", "enforce_monotonic_int_us",
    "bernoulli_keep_mask", "gilbert_keep_mask",
    "FitResult", "fit_offset", "fit_affine", "fit_affine_tls", "tls_affine",
    "ransac_affine", "fit_piecewise_affine", "clock_confidence", "identity_model",
]
