# SentrixSync — Repository Memory

> Multi-device timeline synchronization. **Synchronization infrastructure ONLY.**
> See root `../CLAUDE.md` for ecosystem context.

## Role in the ecosystem

Middle stage: `SentrixSim → SentrixSync → SentrixDataEngine`. Reconciles N devices'
local clocks onto one reference timeline and produces an in-memory `SyncResult` plus
a `Session` manifest. It does NOT generate datasets.

- Versions: `SCHEMA_VERSION = "0.3.0"`, `CONTRACT_VERSION = "1.1.0"`,
  `SENTRIXSYNC_VERSION = "0.4.0"`. Accepts contract MAJOR 1 (rejects ≥2).
  - `1.1.0` (Migration Phase 2): `DeviceDescriptor.topology_ref` /
    `topology_hash` — opaque hardware-revision provenance, **never consumed** by
    sync (same discipline as `calibration_refs` / C6). `SentrixSimAdapter.from_parquet`
    auto-fills them from the producer's parquet KV meta (`sentrixsim_meta` or
    `sentrix_descriptor_*`); they flow verbatim into the Session manifest for
    DataEngine to trace/package. Sync still never branches on topology.

## Synchronization architecture

```
S0 ingest    DeviceAdapter → Sample(t_device_us, payload_ref, seq, meta)
S1 evidence  HARDWARE_PTP | SHARED_EVENT (detectors) | WALL_CLOCK
S2 estimate  per-device ClockModel; Dijkstra spanning tree to reference
S3 correct   t_ref = alpha*t_device + beta
S4 timeline  master grid @ grid_rate_hz → as-of join per stream → StreamAlignment
S5 metrics   sync_resid_us, coverage, dropout, roundtrip_accuracy, QA gate
S6 emit      SyncResult (in memory) + Session manifest (JSON/YAML)
```

## Key schemas

**`SyncResult`** (`sync/engine.py`, in-memory only — engine writes no files):
`reference_device_id, reference_clock_id, clock_models, timeline, confidence,
sync_report, validation_report, diagnostics, metrics`.

**`BuiltTimeline`** (`sync/timeline.py`): `reference_clock_id, grid_us,
per_stream: dict[key→StreamAlignment]`. Key format = `"{device}::{stream}"`.

**`StreamAlignment`** (`sync/join.py`): `stream_id, kernel, source_index, next_index,
weight, valid, interp_confidence`. `source_index == -1` → no sample in tolerance;
`valid == False` → gap.

**`Session`** (`core/session.py`): `metadata, devices(list[DeviceRegistration]),
calibration_refs, timeline: TimelineRef, sync_report, validation_report,
exports: list[ExportRecord], ground_truth`. `TimelineRef.aligned_table_uri` and
`TimelineRef.subframe_buckets` are intentionally LEFT UNFILLED — slots for a
downstream materializer.

**`ClockModel`** (`core/timeline.py`): affine `t_ref = alpha·t + beta_us`;
`identity_model` (reference) has `clock_confidence=1.0`. `segments` (piecewise) is
reserved and raises until approved.

## Confidence model (`sync/confidence.py`)

Three components per grid point, kept SEPARATE and authoritative:
- `source` — trust in the raw sample (producer-asserted; default 1 at valid).
- `clock` — `base · exp(-d/τ)`, decays with distance `d` to nearest sync event.
- `interpolation` — `clip(1 − gap/tolerance, 0, 1)`, 0 at gaps.
`derived_scalar() = source·clock·interp` is **EXPORT-ONLY**, never an internal truth.

## Adapter framework (`ingest/`)

`DeviceAdapter` ABC: `descriptor / open / close / read / read_batch`, optional
`stream_ref` / `ground_truth`. `SentrixSimAdapter.from_parquet(path, descriptor,
ts_column="t_master_us")` reads ONLY the timestamp column; payloads stay referenced
as `parquet:///abs#stream=<id>&row=<i>`. This adapter is the only Sim↔Sync contact
point; it reads Sim output, never imports Sim.

## Detector framework (`detect/`)

`@register_detector` registry; `SyncEventDetector` ABC: `detect(t_us, signal) →
Detection`. Detectors are the ONLY code allowed to read signal payloads, and only to
emit `SyncEvent`s. Shipped: `TactileTap`, `VisualFlash`.

## Clock estimation (`clock/`, `sync/graph.py`)

TLS (default) / RANSAC (robust) affine fits; `graph.reconcile` builds a
reliability-weighted spanning tree; unreachable devices degrade gracefully
(identity, confidence 0). Reference = `identity_model`.

## Critical Rules

1. **Payloads carried by reference.** Never embed bulk data; `Sample.payload_ref` is
   a URI/handle. Resolution is deferred (DataEngine's job).
2. **Timestamps always device-local.** Adapters MUST NOT pre-correct; the core does
   all clock correction.
3. **`SyncResult` is the contract.** Downstream consumes it read-only.
4. **The core never branches on `modality`.** Only `kernel`, `nominal_rate_hz`,
   `units`, `payload_kind` may influence behavior (modality-neutrality rule).
5. **Never fabricate gaps.** Beyond rejection tolerance, flag (`valid=False`,
   `interp_confidence=0`) — never interpolate.
6. **Confidence stays three-component** until the export-only scalar fold.
7. **Reference clock = designated anchor.** One device's clock is reference time.

## Explicit Non-Goals (what Sync must never become)

- A dataset / materialization engine (no aligned-table writing for ML).
- An export engine (no LeRobot / RLDS / HDF5 / MCAP dataset writers).
- A payload resolver (carries refs; does not open bulk values — except detectors
  reading signals to emit events).
- A viewer or a labeling/annotation system.
- A perception/autolabeling system.
- Anything that imports SentrixSim or SentrixDataEngine.

## Deferred (defined but not callable in v0.3) — owned by DataEngine

Payload resolution; sub-frame bucketing *materialization* (`sync/join.subframe_buckets`
computes indices only); materialized aligned-table export; multimodal export.
