"""SentrixSimAdapter — connects SentrixSim output to SentrixSync.

This is the ONLY connection point between the two systems. It depends on
SentrixSim's *output artifacts* (timestamps in a Parquet episode, or arrays
passed in memory) — it does not import SentrixSim, and no SentrixSync code flows
back into SentrixSim. Repository separation is preserved: SentrixSim is a
producer; this adapter reads what it produced.

Payloads are referenced, never loaded: each emitted Sample carries a
`parquet://...#stream=<id>&row=<i>` (or `memory://...`) payload_ref. The adapter
performs no synchronization.

Simplification (documented): every declared stream is emitted at the episode's
master-grid timestamps. Finer per-stream sampling/validity is a later concern;
it does not affect the ingestion plumbing this phase delivers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np

from ..core.device import DeviceDescriptor, Sample
from ..core.types import require, require_nonempty_str
from ..core.uri import build_payload_uri, parse_payload_uri
from .adapter import _CursorAdapterBase


class SentrixSimAdapter(_CursorAdapterBase):
    """Adapter over a SentrixSim episode (timestamps + payload references)."""

    def __init__(
        self,
        descriptor: DeviceDescriptor,
        timestamps: Mapping[str, np.ndarray],
        payload_base_uri: str,
        *,
        ground_truth: dict | None = None,
    ):
        super().__init__()
        descriptor.validate()
        self._descriptor = descriptor
        known = set(descriptor.stream_ids())
        for sid in timestamps:
            require(sid in known,
                    f"timestamps reference unknown stream {sid!r} for "
                    f"device {descriptor.device_id!r}")
        require("#" not in payload_base_uri,
                "payload_base_uri must not contain a fragment ('#'); "
                "per-row fragments are appended by the adapter")
        parse_payload_uri(payload_base_uri)  # validate scheme/location grammar
        self._payload_base_uri = payload_base_uri
        self._timestamps = {sid: np.asarray(ts, dtype=np.int64)
                            for sid, ts in timestamps.items()}
        self._gt = ground_truth
        self._cache: dict[str, list[Sample]] = {}

    # ---- DeviceAdapter surface ---- #
    def descriptor(self) -> DeviceDescriptor:
        return self._descriptor

    def stream_ref(self, stream_id: str) -> str | None:
        if stream_id not in set(self.stream_ids()):
            return None
        return f"{self._payload_base_uri}#stream={stream_id}"

    def ground_truth(self) -> dict | None:
        return self._gt

    def _payload_ref(self, stream_id: str, row: int) -> str:
        scheme, rest = self._payload_base_uri.split("://", 1)
        return build_payload_uri(scheme, rest, fragment=f"stream={stream_id}&row={row}")

    def _samples_for(self, stream_id: str) -> list[Sample]:
        if stream_id in self._cache:
            return self._cache[stream_id]
        ts = self._timestamps.get(stream_id)
        if ts is None:
            # A declared stream with no provided timestamps yields no samples.
            self._cache[stream_id] = []
            return self._cache[stream_id]
        samples = [
            Sample(stream_id=stream_id, t_device_us=int(t), seq=i,
                   payload_ref=self._payload_ref(stream_id, i))
            for i, t in enumerate(ts)
        ]
        self._cache[stream_id] = samples
        return samples

    # ---- construction from a SentrixSim Parquet episode ---- #
    @classmethod
    def from_parquet(
        cls,
        parquet_path: str | Path,
        descriptor: DeviceDescriptor,
        *,
        ts_column: str = "t_master_us",
        ground_truth: dict | None = None,
    ) -> "SentrixSimAdapter":
        """Build an adapter from a SentrixSim episode Parquet file.

        Reads only the timestamp column (`t_master_us`); payloads stay in the
        file and are addressed by `parquet://<abs-path>#stream=<id>&row=<i>`.
        Requires the optional `pyarrow` dependency.
        """
        p = Path(parquet_path).resolve()
        require(p.exists(), f"parquet episode not found: {p}")
        try:
            import pyarrow.parquet as pq
        except ImportError as e:  # pragma: no cover - environment-dependent
            raise ImportError(
                "SentrixSimAdapter.from_parquet requires the optional 'pyarrow' "
                "dependency (install sentrixsync[parquet])") from e
        require_nonempty_str(ts_column, "ts_column")
        table = pq.read_table(p, columns=[ts_column])
        ts = np.asarray(table.column(ts_column).to_numpy(), dtype=np.int64)
        timestamps = {sid: ts for sid in descriptor.stream_ids()}
        base = build_payload_uri("parquet", str(p).replace("\\", "/"))
        return cls(descriptor, timestamps, base, ground_truth=ground_truth)
