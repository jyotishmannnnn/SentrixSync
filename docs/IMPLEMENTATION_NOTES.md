# SentrixSync — Implementation Notes (Phases 1–7)

**Status:** foundations + ingestion + synchronization + multimodal
generalization + robustness hardening complete. This document records what is
built, what is intentionally not built, and the small implementation decisions
made along the way.

## Built in Phase 1 — Core Contracts & Types

`src/sentrixsync/core/`:

| File | Entities |
|---|---|
| `types.py` | version constants; `Kernel`, `EvidenceTier`, `DeviceRole`, `Origin`, `ParamTier`, `GateVerdict` enums; `Serializable` base; validation helpers; semver/contract-version support. |
| `device.py` | `ClockDescriptor`, `StreamDescriptor`, `Sample`, `DeviceDescriptor`, `DeviceRegistration`, `validate_stream_monotonic`. |
| `events.py` | `SyncEvent` (+ `is_usable`, `device_ids`). |
| `timeline.py` | `ClockModel`, `SubframeBuckets`, `TimelineRef`, `SyncReport`, `ValidationReport`. |
| `session.py` | `SessionMetadata`, `CalibrationRef`, `ExportRecord`, `GroundTruthBlock`, `Session`. |

Every entity conforms to `CONTRACT.md` / `SESSION_SCHEMA.md`, has `validate()`
and `to_dict()`/`from_dict()`, and is covered by unit tests. `from_dict()` raises
on missing required fields — this is our schema-validation-on-load.

## Built in Phase 2 — Repository Foundations

| File | Responsibility |
|---|---|
| `config.py` | YAML loading; `ReferenceConfig` + `GateThresholds`; device-descriptor loading; lightly-validated scenario loading. |
| `manifest.py` | Session manifest (de)serialization (JSON/YAML); contract-version gate on load. |
| `lifecycle.py` | `SessionState` + `SessionManager`: create → register → finalize, save/load, transition guard. |
| `configs/` | `reference.yaml`, two device descriptors, two scenario files. |
| `tests/` | 82 tests across 8 files. |

## Built in Phase 4 — Ingestion Layer

`src/sentrixsync/core/`:

| File | Adds |
|---|---|
| `uri.py` | Payload-reference URI grammar (`file`/`mcap`/`parquet`/`memory`, extensible); parse/validate/build only — no resolvers. |

`src/sentrixsync/ingest/`:

| File | Responsibility |
|---|---|
| `batch.py` | `SampleBatch` — columnar (numpy int64 timestamps), per-stream; round-trips the canonical `Sample` contract. |
| `adapter.py` | `DeviceAdapter` ABC (pull-based: `descriptor`/`open`/`close`/`read`/`read_batch`, context manager) + `_CursorAdapterBase`. |
| `sentrixsim_adapter.py` | `SentrixSimAdapter` — in-memory + `from_parquet`; emits payload-by-reference URIs; the sole SentrixSim↔SentrixSync connector. |
| `pipeline.py` | `select_reference` (designated-anchor role policy) + `ingest_session` → `IngestionResult`. |

Contract bumped to **1.0.1** (PATCH): `payload_inline` named with the
exactly-one-of rule; payload-URI grammar defined. Two design notes added:
`SUBFRAME_BUCKETING.md` (recommends fixed-R repeat-pad + per-frame count, not yet
implemented) and `SYNTHETIC_ACCURACY_BUDGET.md` (CI targets for the deferred
estimator).

### Phase-4 decisions

- **Pull-based adapter, per-stream `read(stream_id)`.** Deterministic and
  simulator/real friendly; no async/networking/streaming (per approval).
- **`SampleBatch` is numpy-backed** for the timestamp column (synchronization
  workloads are timestamp-heavy); optional columns are lists, absent entirely
  when no sample carried them. It is a plain class (not a dataclass) to avoid
  numpy-equality pitfalls; round-trip fidelity is checked via `to_samples()`.
- **`SentrixSimAdapter` reads only timestamps**, never payloads; each Sample
  carries `parquet://…#stream=…&row=…` (or `memory://…`). It depends on
  SentrixSim *artifacts*, never imports SentrixSim — repository separation holds.
- **Simplification (documented):** every declared stream is emitted at the
  episode's master-grid timestamps; finer per-stream sampling/validity is a later
  concern and does not affect the plumbing.
- **Reference *role* selection is registration-time policy, not clock math** —
  it picks which clock the timeline will later be expressed in; it estimates
  nothing.

## Built in Phase 5 — Synchronization Infrastructure & Estimation

| Module | Responsibility |
|---|---|
| `clock/forward.py` | Forward corruption model: offset, skew/drift, jitter, quantization, Bernoulli + Gilbert-burst loss. Synthetic/stress only. |
| `clock/estimate.py` | `fit_offset`, `fit_affine` (offset + skew/drift, single-pass robust rejection), `identity_model`, heuristic clock-confidence. |
| `detect/` | `SyncEventDetector` plugin framework + registry; `tactile_tap` and `visual_flash` example detectors; cross-device `match_detections`. |
| `sync/join.py` | As-of join (latest-at + continuous bracket), validity mask, gap-decay confidence; `subframe_buckets` (fixed-R, repeat-pad, m_k, ceil). |
| `sync/timeline.py` | `TimelineBuilder` — configurable reference grid + per-stream alignment. |
| `sync/confidence.py` | Compositional confidence: source/clock/interpolation stored separately; `derived_scalar()` export-only. |
| `sync/metrics.py` | Event residual, round-trip accuracy (vs ground truth), QA gating. |
| `sync/engine.py` | `synchronize()` orchestrator → `SyncResult` (+ core `SyncReport`/`ValidationReport`). |
| `scenarios/synthetic.py` | Scenario builder + presets (offset/drift/jitter/loss/burst/dual) + `run_scenario`. |
| `benchmarks/run_sync_benchmark.py` | Scenario benchmark → `sync_benchmark_report.md` / `.json`. |

### Phase-5 decisions

- **Sub-frame bucketing** implemented per the approved note: fixed `R = ceil()`,
  repeat-pad, per-frame `m_k`, explicit validity; empty frames are gaps (never
  padded from a neighbour).
- **Detectors are plugins operating on in-memory `(t_us, signal)` arrays** and
  emit local detection times; the core consumes only matched `SyncEvent`s. No
  detector is required by the core. Payload resolution stays out of scope —
  synthetic scenarios supply the signal.
- **Confidence is never collapsed internally** — three components are stored
  separately; a derived scalar exists only as an export convenience.
- **Reference clock is the designated anchor (identity);** followers fit affine.
  Drift = non-unit `alpha`; piecewise/Kalman remain deferred.
- **Stream timestamps are clean (clock only); modelled jitter lives on the event
  observations**, separating estimation noise from join coverage.
- **Synthetic detection is made exact** (planted impulse dominates) so measured
  accuracy reflects the injected clock error, not detector quantization.

## Built in Phase 6 — Multimodal Synchronization Generalization

| Module | Change |
|---|---|
| `detect/matcher.py` | `associate_detections` — subset-aware event association via coarse-common-frame clustering (devices may observe different, partially-overlapping event subsets). The equal-count `match_detections` is retained for the simple path. |
| `clock/estimate.py` | `tls_affine` / `fit_affine_tls` — total-least-squares (orthogonal) fit robust to noise in *both* clocks (drops the exact-anchor assumption). |
| `sync/graph.py` | `build_edges` + `reconcile` — devices are nodes, co-observing pairs are TLS-fitted edges; a reliability-weighted spanning tree rooted at the reference composes transforms along each path. Transitive reconciliation; unreachable devices reported gracefully. |
| `sync/metrics.py` | `reconciliation_residual` — topology-agnostic residual (per-event agreement across observers); no anchor-sees-all assumption. |
| `sync/engine.py` | `synchronize` now reconciles via the graph (star is the degenerate 1-edge case) and exposes `ReconcileDiagnostics`. |
| `scenarios/multimodal.py` | N-device heterogeneous scenarios with event groups + visibility subsets; `mm_5device` preset (5 devices, transitive paths, no device sees all events). |

### Phase-6 decisions

- **Star → graph.** Reconciliation is a spanning tree rooted at the reference,
  minimizing cumulative edge residual; confidence is the product of edge
  confidences along the path (so transitive devices degrade gracefully).
- **OLS → TLS.** Pairwise edge fits use total least squares; the reference is no
  longer assumed noise-free.
- **Subset-aware association** uses a coarse per-device wall-clock (ms-class) only
  for *pre-alignment*; precise events still drive estimation. Devices need not
  observe the same events.
- **Graceful degradation:** missed events → fewer edges; disjoint observation →
  unreachable device (identity, confidence 0), never a crash.
- **Backward compatible:** all prior 2-device scenarios/tests pass unchanged
  (a star is a 1-edge graph).

## Built in Phase 7 — Detection & Estimation Robustness Hardening

| Module | Change |
|---|---|
| `detect/corrupt.py` | `corrupt_detections` — inject false negatives, duplicates, false positives, timestamp perturbation into detection arrays (modality-neutral, deterministic). |
| `detect/matcher.py` | `associate_detections` hardened: centroid clustering with one-per-device duplicate handling (keep nearest), robust to duplicates and same-window extras. |
| `clock/estimate.py` | `ransac_affine` (gross-outlier rejection via minimal-sample consensus + TLS refit) and `fit_piecewise_affine` (segmented drift). |
| `core/timeline.py` | `ClockModel.to_reference` now applies the optional `segments` (piecewise) mapping (the field was previously reserved). |
| `sync/graph.py` | Reliability-weighted path cost (`-log(confidence)`, not raw residual) so few-observation perfect-fit spurious edges aren't trusted; `min_events` / `method` threaded through. |
| `sync/confidence.py` | Distance-from-event decay on the **clock** component (`exp(-d/tau)`), so confidence reflects extrapolation uncertainty across long gaps. |
| `sync/engine.py` | `robust_estimation`, `ransac_threshold_us`, `confidence_decay_tau_us`, `min_events` options. |
| `scenarios/robustness.py` | Corruption runner, coarse-clock sweep, long-session nonlinear-drift generator + affine-vs-piecewise comparison. |
| `benchmarks/run_sync_benchmark.py` | Robustness section (corruption baseline vs robust, coarse sweep, piecewise). |

### Phase-7 findings & decisions

- **Spurious-edge trust is the key failure mode.** Under false positives, two
  mis-associated detections can form a *perfect-residual 2-point edge* that a
  residual-weighted shortest path trusts as a high-confidence shortcut,
  mis-routing a device. Two justified fixes applied: a **minimum-support
  threshold** (`min_events`, raised in robust mode) so a few coincidences cannot
  form an edge, and **confidence-weighted path selection**. Result on the heavy
  corruption case: worst α-error 3.9e-3 → 1.6e-4, worst β-error 5930 µs → 521 µs.
- **Association vs estimation division of labour:** association resolves
  duplicates / same-window extras; gross outlier *pairs* that survive into an
  edge are rejected by RANSAC; spurious *edges* are rejected by min-support.
- **Coarse-clock operating limit:** full reconciliation holds while wall-clock
  error stays well below the association tolerance (12 ms here → fine to ~8 ms);
  beyond the tolerance, association fragments and devices become unreachable
  (reported, not crashed).
- **Piecewise improves long nonlinear-drift sessions** (alignment RMSE 160 → 55
  µs); affine remains the default, piecewise is opt-in.
- **Backward compatible:** defaults unchanged (TLS, `min_events=2`, no decay),
  so all prior tests pass; robustness is opt-in via flags.

## Deliberately NOT built (deferred / out of scope)

- **Detectors on real payloads + payload resolvers** (synthetic signals only so far).
- **Kalman / continuous drift tracking** (affine default; piecewise opt-in; Kalman deferred).
- **Graph composition of piecewise edges** (piecewise demonstrated at the direct
  estimator level; composing segmented transforms along multi-hop paths is future).
- **Adaptive RANSAC threshold / auto min-support** (currently parameters).
- **Clock reset / wrap / reboot mid-session** handling.
- **Chunked/streamed timeline** for very long, high-rate, many-stream sessions.
- **Exporters / materialization** (`timelineio`); LeRobot/MCAP multimodal export.
- **Lifecycle wiring of the sync stages** (`SessionManager` stops at
  `DEVICES_REGISTERED`; the engine is callable directly).
- **Anything Data-Engine:** catalog, search, commerce, privacy, perception,
  vision generation, object tracking.

## Small implementation decisions

1. **`Serializable` is a plain mixin, not a dataclass**, so dataclass
   field-ordering rules are never disturbed by inheritance. `to_dict()` drops
   `None` values to keep manifests clean; empty lists/dicts are preserved.
2. **`ClockModel.to_reference()` is included** as the *definitional* affine map
   (`alpha·t + beta`). It is not estimation — nothing fits `alpha`/`beta` from
   evidence. Piecewise/segmented mapping raises `NotImplementedError` (affine is
   the v0.3 default; piecewise is the approved-optional extension).
3. **Microseconds are integers** everywhere (decision C7); helpers reject floats
   and `bool` (since `bool` subclasses `int`).
4. **`sync_method` is a free string** in `SyncReport` because it may be `none`
   (single device), `mixed`, or a tier value.
5. **Scenario files are loaded as raw dicts** with name-only validation, because
   their forward-model parameters are consumed by the deferred sync stages.

## Running

```bash
pip install -e ".[dev]"
pytest -q
```
