"""Compositional confidence model.

Three confidence components are stored SEPARATELY per grid point and never
collapsed internally (per the approved decision). A derived scalar is provided
for export only.

  * source        — trust in the underlying raw sample (producer-asserted).
  * clock         — trust in the device's fitted clock model.
  * interpolation — trust in the resampled value (decays with gap; 0 at a gap).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.types import require
from .join import StreamAlignment


@dataclass
class ConfidenceComponents:
    stream_id: str
    source: np.ndarray          # per grid point, [0,1]
    clock: np.ndarray           # per grid point, [0,1]
    interpolation: np.ndarray   # per grid point, [0,1]

    def __post_init__(self) -> None:
        n = self.source.shape[0]
        require(self.clock.shape[0] == n and self.interpolation.shape[0] == n,
                "confidence components must share length")

    def derived_scalar(self) -> np.ndarray:
        """EXPORT-ONLY convenience: a single per-point scalar. Not used as an
        internal source of truth — the three components remain authoritative."""
        return np.clip(self.source * self.clock * self.interpolation, 0.0, 1.0)


def build_confidence(alignment: StreamAlignment, *, clock_confidence: float,
                     source_confidence_per_sample: np.ndarray | None = None,
                     grid_us: np.ndarray | None = None,
                     event_ref_times_us: np.ndarray | None = None,
                     decay_tau_us: float | None = None) -> ConfidenceComponents:
    """Assemble the three components for one stream on the grid.

    `source_confidence_per_sample` (length = n_samples) defaults to all-ones;
    invalid (gap) grid points get 0 in every component. `interpolation` comes
    directly from the join's gap-decay.

    Long-gap modelling: when `grid_us`, `event_ref_times_us`, and `decay_tau_us`
    are supplied, the **clock** component decays as exp(-d/tau) with d the
    distance (in reference us) from each grid point to the nearest sync event
    used to fit that device's clock — so confidence reflects extrapolation
    uncertainty far from any event. Without them the clock component is flat.
    """
    n = alignment.valid.shape[0]
    valid = alignment.valid
    src = np.zeros(n, dtype=float)
    if source_confidence_per_sample is None:
        src[valid] = 1.0
    else:
        scps = np.asarray(source_confidence_per_sample, dtype=float)
        idx = alignment.source_index
        ok = valid & (idx >= 0)
        src[ok] = scps[idx[ok]]

    base = float(clock_confidence)
    if (grid_us is not None and event_ref_times_us is not None and decay_tau_us
            and np.asarray(event_ref_times_us).size):
        g = np.asarray(grid_us, dtype=float)
        ev = np.sort(np.asarray(event_ref_times_us, dtype=float))
        pos = np.clip(np.searchsorted(ev, g), 0, ev.size - 1)
        left = np.clip(pos - 1, 0, ev.size - 1)
        dist = np.minimum(np.abs(g - ev[pos]), np.abs(g - ev[left]))
        decay = np.exp(-dist / float(decay_tau_us))
        clk = np.where(valid, base * decay, 0.0)
    else:
        clk = np.where(valid, base, 0.0)

    interp = np.where(valid, alignment.interp_confidence, 0.0)
    return ConfidenceComponents(stream_id=alignment.stream_id, source=src,
                                clock=clk, interpolation=interp)
