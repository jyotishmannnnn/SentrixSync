"""SYNC-1 — on-disk persistence of a SyncResult (+ optional Session).

Removes the in-memory-only Sync->DataEngine handoff. A SyncResult is written as a
self-contained directory:

    <dir>/
      sync_result.json   scalars, reports, diagnostics, clock models, metrics,
                         and per-stream array references
      arrays.npz         all numpy arrays (grid + per-stream join indices +
                         confidence components), dtype- and shape-exact
      session.json       (optional) the Session manifest, when supplied

`save_sync_result` / `load_sync_result` round-trip every field of `SyncResult`
faithfully, so a loaded result drives SentrixDataEngine identically to the
in-memory one. This module is persistence only: it defines NO synchronization
behaviour and changes NO contract. NPZ is a numpy container, not a new wire
format. numpy + json + stdlib only (no new dependency).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .core.timeline import ClockModel, SyncReport, ValidationReport
from .core.types import Kernel, SENTRIXSYNC_VERSION, coerce_enum, require
from .sync.confidence import ConfidenceComponents
from .sync.engine import SyncResult
from .sync.graph import Edge, ReconcileDiagnostics
from .sync.join import StreamAlignment
from .sync.timeline import BuiltTimeline

FORMAT = "sentrixsync.sync_result"
FORMAT_VERSION = "1"
_MANIFEST = "sync_result.json"
_ARRAYS = "arrays.npz"
_SESSION = "session.json"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _jsonable(o: Any) -> Any:
    """Coerce numpy scalars/arrays + sets into JSON-native types (lossless for
    the scalar metrics/diagnostics carried here)."""
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_jsonable(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return o


class _ArrayStore:
    """Collects arrays under stable npz keys (a0, a1, ...) and records the key
    in the manifest so load can resolve them back."""

    def __init__(self) -> None:
        self.arrays: dict[str, np.ndarray] = {}
        self._n = 0

    def put(self, arr: np.ndarray) -> str:
        key = f"a{self._n}"
        self._n += 1
        self.arrays[key] = np.asarray(arr)
        return key


def _kernel_value(k: Any) -> str:
    return getattr(k, "value", str(k))


# --------------------------------------------------------------------------- #
# save
# --------------------------------------------------------------------------- #
def save_sync_result(sync_result: SyncResult, dest: str | Path, *,
                     session: Any | None = None) -> Path:
    """Persist `sync_result` (and optionally `session`) to a directory `dest`.
    Returns the directory path. Overwrites an existing bundle in place."""
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    store = _ArrayStore()

    tl = sync_result.timeline
    per_stream = {}
    for key, al in tl.per_stream.items():
        per_stream[key] = {
            "stream_id": al.stream_id,
            "kernel": _kernel_value(al.kernel),
            "source_index": store.put(al.source_index),
            "next_index": store.put(al.next_index),
            "weight": store.put(al.weight),
            "valid": store.put(al.valid),
            "interp_confidence": store.put(al.interp_confidence),
        }

    confidence = {}
    for key, cc in sync_result.confidence.items():
        confidence[key] = {
            "stream_id": cc.stream_id,
            "source": store.put(cc.source),
            "clock": store.put(cc.clock),
            "interpolation": store.put(cc.interpolation),
        }

    diag = sync_result.diagnostics
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "sentrixsync_version": SENTRIXSYNC_VERSION,
        "reference_device_id": sync_result.reference_device_id,
        "reference_clock_id": sync_result.reference_clock_id,
        "clock_models": {d: cm.to_dict() for d, cm in sync_result.clock_models.items()},
        "sync_report": sync_result.sync_report.to_dict(),
        "validation_report": sync_result.validation_report.to_dict(),
        "diagnostics": {
            "edges": [_edge_to_dict(e) for e in diag.edges],
            "reachable": sorted(diag.reachable),
            "unreachable": sorted(diag.unreachable),
            "hops": {str(k): int(v) for k, v in diag.hops.items()},
            "paths": {str(k): list(v) for k, v in diag.paths.items()},
        },
        "metrics": _jsonable(sync_result.metrics),
        "timeline": {
            "reference_clock_id": tl.reference_clock_id,
            "grid_us": store.put(tl.grid_us),
            "per_stream": per_stream,
        },
        "confidence": confidence,
        "arrays_file": _ARRAYS,
    }

    (out / _MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    # allow_pickle stays False on load; only plain numeric/bool arrays are stored.
    np.savez(out / _ARRAYS, **store.arrays)

    if session is not None:
        (out / _SESSION).write_text(
            json.dumps(session.to_dict(), indent=2, default=str), encoding="utf-8")
    return out


def _edge_to_dict(e: Edge) -> dict:
    return {"a": e.a, "b": e.b, "alpha": float(e.alpha), "beta_us": float(e.beta_us),
            "residual_us": float(e.residual_us), "n_events": int(e.n_events),
            "confidence": float(e.confidence)}


# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #
def load_sync_result(src: str | Path) -> SyncResult:
    """Reconstruct a SyncResult from a bundle directory (or its manifest path).
    The returned object is field-equivalent to the original."""
    base, manifest = _resolve(src)
    npz_path = base / manifest.get("arrays_file", _ARRAYS)
    require(npz_path.exists(), f"arrays file not found: {npz_path}")
    with np.load(npz_path, allow_pickle=False) as npz:
        arrays = {k: npz[k] for k in npz.files}

    def arr(key: str) -> np.ndarray:
        return arrays[key]

    tlm = manifest["timeline"]
    per_stream: dict[str, StreamAlignment] = {}
    for key, sm in tlm["per_stream"].items():
        per_stream[key] = StreamAlignment(
            stream_id=sm["stream_id"],
            kernel=coerce_enum(sm["kernel"], Kernel, "alignment.kernel"),
            source_index=arr(sm["source_index"]).astype(np.int64),
            next_index=arr(sm["next_index"]).astype(np.int64),
            weight=arr(sm["weight"]).astype(float),
            valid=arr(sm["valid"]).astype(bool),
            interp_confidence=arr(sm["interp_confidence"]).astype(float))

    timeline = BuiltTimeline(
        reference_clock_id=tlm["reference_clock_id"],
        grid_us=arr(tlm["grid_us"]).astype(np.int64),
        per_stream=per_stream)

    confidence: dict[str, ConfidenceComponents] = {}
    for key, cm in manifest["confidence"].items():
        confidence[key] = ConfidenceComponents(
            stream_id=cm["stream_id"],
            source=arr(cm["source"]).astype(float),
            clock=arr(cm["clock"]).astype(float),
            interpolation=arr(cm["interpolation"]).astype(float))

    d = manifest["diagnostics"]
    diagnostics = ReconcileDiagnostics(
        edges=[Edge(**e) for e in d.get("edges", [])],
        reachable=set(d.get("reachable", [])),
        unreachable=set(d.get("unreachable", [])),
        hops={k: int(v) for k, v in d.get("hops", {}).items()},
        paths={k: list(v) for k, v in d.get("paths", {}).items()})

    return SyncResult(
        reference_device_id=manifest["reference_device_id"],
        reference_clock_id=manifest["reference_clock_id"],
        clock_models={dev: ClockModel.from_dict(cm)
                      for dev, cm in manifest["clock_models"].items()},
        timeline=timeline,
        confidence=confidence,
        sync_report=SyncReport.from_dict(manifest["sync_report"]),
        validation_report=ValidationReport.from_dict(manifest["validation_report"]),
        diagnostics=diagnostics,
        metrics=manifest.get("metrics", {}) or {})


def load_session(src: str | Path):
    """Load the bundled Session (if `session.json` was saved), else None."""
    base, _ = _resolve(src)
    sp = base / _SESSION
    if not sp.exists():
        return None
    from .core.session import Session
    return Session.from_dict(json.loads(sp.read_text(encoding="utf-8")))


def _resolve(src: str | Path) -> tuple[Path, dict]:
    p = Path(src)
    if p.is_dir():
        mpath = p / _MANIFEST
    elif p.suffix == ".json":
        mpath = p
    else:
        mpath = p / _MANIFEST
    require(mpath.exists(), f"sync_result manifest not found: {mpath}")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    require(manifest.get("format") == FORMAT,
            f"not a {FORMAT} bundle: {mpath}")
    return mpath.parent, manifest
