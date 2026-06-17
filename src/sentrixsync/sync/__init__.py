"""Synchronization layer: timeline build, as-of join, confidence, metrics, engine."""
from __future__ import annotations

from .confidence import ConfidenceComponents, build_confidence
from .engine import SyncResult, synchronize
from .graph import Edge, ReconcileDiagnostics, build_edges, reconcile
from .join import (
    StreamAlignment,
    SubframeBucketing,
    asof_join,
    compute_R,
    subframe_buckets,
)
from .timeline import BuiltTimeline, CorrectedStream, TimelineBuilder

__all__ = [
    "synchronize", "SyncResult",
    "reconcile", "build_edges", "Edge", "ReconcileDiagnostics",
    "TimelineBuilder", "BuiltTimeline", "CorrectedStream",
    "asof_join", "StreamAlignment", "subframe_buckets", "SubframeBucketing", "compute_R",
    "build_confidence", "ConfidenceComponents",
]
