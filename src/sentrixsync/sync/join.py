"""As-of join engine and sub-frame bucketing.

Both operate purely on timestamps (payloads stay by-reference). The join maps a
reference-time grid onto each stream's corrected reference-time samples, with a
validity mask and an interpolation-confidence that decays with the gap to the
nearest real sample — never fabricating values across a gap beyond tolerance.

Sub-frame bucketing implements the approved rule (SUBFRAME_BUCKETING.md):
fixed-R, repeat-pad, per-frame valid-count m_k, explicit validity, R via ceil().
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.types import Kernel, require, require_positive

_I64_MAX = np.iinfo(np.int64).max


@dataclass
class StreamAlignment:
    """Per-grid-point join result for one stream.

    `source_index` is the latest-at sample index (-1 if none within tolerance).
    For CONTINUOUS streams, `next_index`/`weight` describe the linear-interp
    bracket (weight in [0,1] from source toward next). `valid` is False at gaps.
    """
    stream_id: str
    kernel: Kernel
    source_index: np.ndarray   # int64, -1 = none
    next_index: np.ndarray     # int64, -1 = none (CONTINUOUS only)
    weight: np.ndarray         # float, interpolation weight (CONTINUOUS only)
    valid: np.ndarray          # bool
    interp_confidence: np.ndarray  # float in [0,1]

    def coverage(self) -> float:
        return float(self.valid.mean()) if self.valid.size else 0.0


def asof_join(stream_id: str, grid_us: np.ndarray, sample_t_ref_us: np.ndarray,
              kernel: Kernel, tolerance_us: int) -> StreamAlignment:
    require_positive(tolerance_us, "tolerance_us")
    grid = np.asarray(grid_us, dtype=np.int64)
    s = np.sort(np.asarray(sample_t_ref_us, dtype=np.int64))
    n = grid.size
    src = np.full(n, -1, dtype=np.int64)
    nxt = np.full(n, -1, dtype=np.int64)
    weight = np.zeros(n, dtype=float)
    valid = np.zeros(n, dtype=bool)
    conf = np.zeros(n, dtype=float)

    if s.size == 0:
        return StreamAlignment(stream_id, kernel, src, nxt, weight, valid, conf)

    pos = np.searchsorted(s, grid, side="right") - 1     # last index with s <= grid
    has_prev = pos >= 0
    has_next = (pos + 1) < s.size
    prev_clip = np.clip(pos, 0, s.size - 1)
    next_clip = np.clip(pos + 1, 0, s.size - 1)
    gap_prev = np.where(has_prev, grid - s[prev_clip], _I64_MAX)
    gap_next = np.where(has_next, s[next_clip] - grid, _I64_MAX)

    if kernel is Kernel.HOLD:
        gap = gap_prev
        src = np.where(has_prev, prev_clip, -1)
        valid = has_prev & (gap_prev <= tolerance_us)
    else:  # CONTINUOUS — latest-at base index plus interpolation bracket
        src = np.where(has_prev, prev_clip, -1)
        nxt = np.where(has_next, next_clip, -1)
        both = has_prev & has_next
        span = np.where(both, (s[next_clip] - s[prev_clip]).astype(float), 1.0)
        span = np.where(span == 0.0, 1.0, span)
        weight = np.clip(np.where(both, (grid - s[prev_clip]).astype(float) / span, 0.0),
                         0.0, 1.0)
        gap = np.minimum(gap_prev, gap_next)
        valid = (has_prev | has_next) & (gap <= tolerance_us)

    safe_gap = np.where(gap == _I64_MAX, tolerance_us, gap).astype(float)
    conf = np.clip(1.0 - safe_gap / float(tolerance_us), 0.0, 1.0)
    conf = np.where(valid, conf, 0.0)
    return StreamAlignment(stream_id, kernel, src.astype(np.int64), nxt.astype(np.int64),
                           weight, valid, conf)


@dataclass
class SubframeBucketing:
    """Fixed-R bucketing of a high-rate stream onto anchor-frame intervals.

    `index[k]` holds R high-rate sample indices for frame k (repeat-padded after
    the m_k real samples); `m_k[k]` is the real count; `valid[k]` is False when a
    frame contains no high-rate samples (a gap — never repeat-padded from another
    frame).
    """
    R: int
    index: np.ndarray     # (n_frames, R) int64
    m_k: np.ndarray       # (n_frames,) int
    valid: np.ndarray     # (n_frames,) bool


def compute_R(grid_rate_hz: float, anchor_fps: float) -> int:
    """R = ceil(grid_rate / anchor_fps) so no high-rate sample is discarded."""
    require_positive(grid_rate_hz, "grid_rate_hz")
    require_positive(anchor_fps, "anchor_fps")
    return int(math.ceil(grid_rate_hz / anchor_fps))


def subframe_buckets(anchor_times_us: np.ndarray, highrate_times_us: np.ndarray,
                     R: int) -> SubframeBucketing:
    """Bucket high-rate samples into the intervals between consecutive anchor
    frames. n_frames = len(anchor)-1 (the final open-ended anchor is excluded —
    documented edge behavior). Frames with > R samples keep the first R and flag
    the overflow via m_k (capped at R)."""
    require(R >= 1, "R must be >= 1")
    a = np.asarray(anchor_times_us, dtype=np.int64)
    h = np.sort(np.asarray(highrate_times_us, dtype=np.int64))
    require(a.ndim == 1 and a.size >= 2, "need >= 2 anchor frame times")
    n_frames = a.size - 1
    index = np.zeros((n_frames, R), dtype=np.int64)
    m_k = np.zeros(n_frames, dtype=int)
    valid = np.zeros(n_frames, dtype=bool)

    lo = np.searchsorted(h, a[:-1], side="left")
    hi = np.searchsorted(h, a[1:], side="left")     # [a_k, a_{k+1})
    for k in range(n_frames):
        members = np.arange(lo[k], hi[k], dtype=np.int64)
        m = members.size
        m_k[k] = min(m, R)
        if m == 0:
            valid[k] = False                         # gap: not repeat-padded
            continue
        valid[k] = True
        take = members[:R]
        index[k, :take.size] = take
        if take.size < R:                            # repeat-pad with last real sample
            index[k, take.size:] = take[-1]
    return SubframeBucketing(R=R, index=index, m_k=m_k, valid=valid)
