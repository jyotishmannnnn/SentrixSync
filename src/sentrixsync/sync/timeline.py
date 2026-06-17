"""Timeline builder — constructs the reference-time master grid and aligns each
stream onto it via the as-of join.

The grid rate is configurable (default convention: highest joined native rate);
it is never anchored to an assumed video frame rate. The reference clock is the
designated-anchor device's clock (its corrected samples are identity).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..core.types import Kernel, require, require_positive
from .join import StreamAlignment, asof_join


@dataclass
class CorrectedStream:
    """A stream's samples already mapped to reference microseconds, plus kernel."""
    stream_id: str
    t_ref_us: np.ndarray
    kernel: Kernel


@dataclass
class BuiltTimeline:
    reference_clock_id: str
    grid_us: np.ndarray
    per_stream: dict[str, StreamAlignment]

    @property
    def n_grid(self) -> int:
        return int(self.grid_us.shape[0])

    @property
    def t_start_us(self) -> int:
        return int(self.grid_us[0]) if self.n_grid else 0

    @property
    def t_end_us(self) -> int:
        return int(self.grid_us[-1]) if self.n_grid else 0


class TimelineBuilder:
    def __init__(self, grid_rate_hz: float, rejection_tolerance_us: int):
        require_positive(grid_rate_hz, "grid_rate_hz")
        require_positive(rejection_tolerance_us, "rejection_tolerance_us")
        self.grid_rate_hz = float(grid_rate_hz)
        self.rejection_tolerance_us = int(rejection_tolerance_us)

    def build(self, reference_clock_id: str,
              corrected_streams: list[CorrectedStream]) -> BuiltTimeline:
        require(len(corrected_streams) >= 1, "timeline needs at least one stream")
        spans = [(cs.t_ref_us.min(), cs.t_ref_us.max())
                 for cs in corrected_streams if cs.t_ref_us.size > 0]
        require(len(spans) >= 1, "timeline needs at least one stream with samples")
        t_start = int(min(lo for lo, _ in spans))
        t_end = int(max(hi for _, hi in spans))

        step_us = 1e6 / self.grid_rate_hz
        n = int(np.floor((t_end - t_start) / step_us)) + 1
        grid = t_start + np.round(np.arange(n) * step_us).astype(np.int64)

        per_stream: dict[str, StreamAlignment] = {}
        for cs in corrected_streams:
            per_stream[cs.stream_id] = asof_join(
                cs.stream_id, grid, cs.t_ref_us, cs.kernel, self.rejection_tolerance_us)
        return BuiltTimeline(reference_clock_id=reference_clock_id, grid_us=grid,
                             per_stream=per_stream)
