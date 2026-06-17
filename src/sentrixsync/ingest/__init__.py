"""SentrixSync ingestion layer.

Pull-based device adapters, the columnar SampleBatch container, the SentrixSim
adapter, and the session ingestion pipeline. No synchronization algorithms.
"""
from __future__ import annotations

from .adapter import AdapterError, DeviceAdapter
from .batch import SampleBatch
from .pipeline import IngestionResult, ingest_session, select_reference
from .sentrixsim_adapter import SentrixSimAdapter

__all__ = [
    "DeviceAdapter",
    "AdapterError",
    "SampleBatch",
    "SentrixSimAdapter",
    "IngestionResult",
    "ingest_session",
    "select_reference",
]
