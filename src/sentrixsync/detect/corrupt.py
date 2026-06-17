"""Detection corruption model — stress-tests the pipeline under realistic
detector failures. Operates on per-device detection time arrays only (opaque
microsecond timestamps); no modality assumptions.

Injects, deterministically per `seed`:
  * false negatives (missed detections) — drop each true detection w.p. fn_rate
  * duplicates                          — near-copy each detection w.p. dup_rate
  * false positives                     — add spurious detections (Poisson)
  * timestamp perturbations             — add Gaussian noise to detection times
"""
from __future__ import annotations

import numpy as np

from ..core.types import require


def corrupt_detections(detections: dict[str, np.ndarray], *, duration_us: int,
                       fn_rate: float = 0.0, dup_rate: float = 0.0,
                       fp_rate: float = 0.0, perturb_us: float = 0.0,
                       dup_jitter_us: int = 200, seed: int = 0
                       ) -> dict[str, np.ndarray]:
    """Return corrupted per-device detections.

    `fp_rate` is the expected number of false positives **per true detection**
    (Poisson-distributed, spread uniformly across [0, duration_us)). Other rates
    are per-detection probabilities. Output remains sorted int64.
    """
    for name, v in (("fn_rate", fn_rate), ("dup_rate", dup_rate)):
        require(0.0 <= v <= 1.0, f"{name} must be in [0, 1]")
    require(fp_rate >= 0.0, "fp_rate must be >= 0")
    require(duration_us > 0, "duration_us must be positive")
    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}
    for dev, times in detections.items():
        t = np.asarray(times, dtype=np.int64).copy()
        # false negatives
        if fn_rate > 0.0 and t.size:
            t = t[rng.random(t.size) >= fn_rate]
        # duplicates (near-copies)
        if dup_rate > 0.0 and t.size:
            dmask = rng.random(t.size) < dup_rate
            if dmask.any():
                offs = rng.integers(-dup_jitter_us, dup_jitter_us + 1, int(dmask.sum()))
                t = np.concatenate([t, t[dmask] + offs])
        # false positives (Poisson count relative to detections)
        if fp_rate > 0.0:
            lam = fp_rate * max(1, np.asarray(times).size)
            n_fp = int(rng.poisson(lam))
            if n_fp:
                t = np.concatenate([t, rng.integers(0, duration_us, n_fp)])
        # timestamp perturbation
        if perturb_us > 0.0 and t.size:
            t = t + np.round(rng.normal(0.0, perturb_us, t.size)).astype(np.int64)
        out[dev] = np.sort(t.astype(np.int64))
    return out
