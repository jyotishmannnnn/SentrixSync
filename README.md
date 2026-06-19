# SentrixSync

**A modality-neutral, event-based, graph-based multi-device clock synchronization
platform.** SentrixSync takes timestamped streams from many independent devices —
each running its own clock — and reconciles them onto a single reference timeline,
reporting how trustworthy that reconciliation is.

Its defining principle:

> **SentrixSync does not synchronize raw sensor values. It synchronizes _clocks_,
> through _shared events_. Once each device's clock is aligned, every sample from
> every device inherits that alignment automatically.**

This makes it a foundation for synchronized **multimodal capture** — tactile +
vision, plus IMU, audio, depth, motion capture, force/torque, eye tracking, and
future sensors — without any modality-specific logic in the synchronization core.

---

## Project Overview

SentrixSync is, concretely:

- **A modality-neutral synchronization platform** — the core reasons only about
  clocks, timestamps, events, confidence, validity, and clock relationships. It
  never inspects images, tactile values, audio samples, or force values.
- **An event-based synchronization system** — devices are linked by sparse,
  physically shared events (a tap felt by a glove and seen by a camera), not by
  matching frames or samples.
- **A graph-based clock-reconciliation engine** — devices are nodes; co-observed
  events form edges; clocks are reconciled across a graph, including
  **transitively** (a device that shares no event with the reference can still be
  reconciled through intermediates).
- **A foundation for multimodal tactile/vision synchronization** — and for the
  broader Sentrix Physical Data Engine, which consumes the unified timeline
  SentrixSync produces.

It sits between data producers (the SentrixSim tactile simulator, future synthetic
vision generators, real Mark 2 glove hardware, cameras, trackers) and downstream
consumers. It is the *inter-device* timeline layer — one device = one clock domain.

---

## Key Design Principles

| Principle | What it means |
|---|---|
| **Modality neutrality** | The core branches only on a stream's declared `kernel`, `rate`, `units`, and `payload_kind` — never on what the data *means*. Modality-specific logic lives only in detectors (edge plugins). |
| **Event-based synchronization** | A handful of shared events is enough to fit a device's clock model; that model then maps *all* of its samples. Sample rate, phase, drops, and buffering stop being correspondence problems. |
| **Graph-based reconciliation** | Pairwise clock relationships compose along a reliability-weighted spanning tree rooted at a designated anchor; transitive paths are first-class. |
| **Confidence-aware** | Three confidence components (source, clock, interpolation) are tracked separately and never silently collapsed; clock confidence decays away from supporting events. |
| **Hardware independence** | Adding a device requires an adapter (and optionally a detector). The contract, graph, estimator, and confidence model never change. |
| **Payload by reference** | Bulky payloads (frames, taxel arrays) are referenced via URIs (`file://`, `mcap://`, `parquet://`, `memory://`); the core handles only timestamps and light metadata. |

```
            +------------------------------------------------------------+
            |                  SYNCHRONIZATION CORE                      |
            |   clocks · timestamps · events · confidence · validity     |
            |   contract · associate · estimate · graph · timeline       |
            |                 (modality-agnostic, frozen)                |
            +----------------------------▲-------------------------------+
                                         │  stable contract
            ┌───────────┬────────────────┼────────────────┬───────────┐
        [Adapter]   [Adapter]        [Adapter]         [Adapter]   [Adapter]
        +Detector   +Detector         (no det.)        +Detector   +Detector
          glove       camera             IMU             mic         mocap
        ── the only code written per device; the core is untouched ──
```

---

## Current Architecture

```
 Device Streams → Adapters → Detectors → Events → Association
               → Clock Estimation → Graph Reconciliation → Unified Timeline
```

| Stage | Module | Responsibility |
|---|---|---|
| Device Streams | (hardware / generator) | timestamped samples in each device's own clock |
| **Adapters** | `ingest/` (`DeviceAdapter`) | present a device as a descriptor + samples; payloads by reference |
| **Detectors** | `detect/` (`SyncEventDetector`) | turn a stream's signal into local **event times** (the only modality-aware stage) |
| **Events** | `core/events.py` (`SyncEvent`) | `{device_id → t_local_us}` per shared fiducial |
| **Association** | `detect/matcher.py` (`associate_detections`) | cluster cross-device detections of the same fiducial; subset-aware |
| **Clock Estimation** | `clock/estimate.py` | fit `t_ref = α·t_local + β` (TLS / RANSAC / piecewise) |
| **Graph Reconciliation** | `sync/graph.py` (`reconcile`) | compose pairwise fits to the reference over a spanning tree |
| **Unified Timeline** | `sync/timeline.py`, `sync/join.py`, `sync/engine.py` | reference grid + as-of join + validity + confidence |

The whole pipeline is invoked through one call, `sync.synchronize(...)`, which
returns a `SyncResult` (clock models, timeline, confidence, reports, graph
diagnostics).

---

## Supported Sources

Support is **adapter-based**: any source that can present a device-local
microsecond timestamp plus (referenced) samples is supported by writing a small
adapter. The synchronization core is identical for all of them.

| Category | Examples | Status |
|---|---|---|
| **Real hardware (MCUs)** | Arduino, ESP32, RP2040, STM32, Teensy, generic serial devices; the SentrixCapture glove producer | SentrixCapture emits the same Parquet artifact contract as SentrixSim and is read by the shipped adapter (`ts_column="t_capture_us"`). Other serial/CSV/JSON/binary devices are integrated via a user-written adapter — templates in the User Guide. |
| **Vision** | MP4 recordings, webcam / smartphone / industrial camera streams | Integrated via a user-written video adapter (frame timestamps + a 1-D feature series). Template in the User Guide. |
| **Synthetic sources** | tactile generators, simulated sensors, benchmark scenarios | **Ships in-repo** (`scenarios/`). No adapter needed — used identically to real devices. |

**What ships today:** the `SentrixSimAdapter` and two impulse detectors
(`tactile_tap`, `visual_flash`). The adapter reads a producer's Parquet (or
in-memory) episode by its self-describing metadata: it consumes only the timestamp
column (`t_master_us` for SentrixSim, `t_capture_us` for the SentrixCapture
real-hardware producer) and references payloads by URI. Both producers emit the
**same artifact contract**, so the adapter reads them identically; opaque
`topology_ref` / `topology_hash` provenance is auto-carried from the file's
metadata. Video adapters and additional detectors are written by the integrator
following the contract — see the guides below. Synthetic and real devices enter
through the **same contract**, so settings validated in simulation transfer to
hardware unchanged.

---

## Robustness Features

The latest completed milestone hardened the backbone against realistic detection
failures and long-session conditions. All of the following are implemented and
covered by tests.

### Detection robustness
Injectable corruption for stress-testing (`detect/corrupt.py`): **false
positives**, **false negatives**, **duplicates**, and **timestamp perturbation**
— deterministic and modality-neutral.

### Association robustness
Subset-aware centroid **clustering** (`detect/matcher.py`) with **duplicate
handling** and a **one-detection-per-device-per-event** constraint (nearest-to-
centroid wins). Devices may observe different, partially-overlapping event subsets.

### Estimation robustness
- **TLS** (total least squares) — robust to symmetric noise in *both* clocks.
- **RANSAC affine** — rejects gross outliers / mis-associated events.
- **Spurious-edge rejection** — a minimum-support threshold plus confidence-
  weighted path selection prevent a few false-positive coincidences from forming a
  high-trust shortcut edge (the key finding of the robustness work).

### Confidence modeling
Three components stored separately and never collapsed internally: **source
confidence**, **clock confidence**, **interpolation confidence** — plus
**long-gap decay** (clock confidence falls with distance from supporting events,
so confidence reflects real extrapolation uncertainty). A derived scalar is
available for export only.

### Piecewise drift handling
Optional **piecewise affine** clock models for long sessions with nonlinear
(thermal) drift; affine remains the default.

### Coarse-clock analysis
The wall-clock (NTP) **operating envelope** for association is characterized:
full reconciliation holds while coarse error stays well below the association
tolerance; beyond it, association fragments and devices are reported unreachable
(never a crash).

---

## Benchmark Highlights

Reproduce with `python benchmarks/run_sync_benchmark.py`; full tables in
[`benchmarks/sync_benchmark_report.md`](benchmarks/sync_benchmark_report.md).
Accuracy is *recovered-vs-injected* clock error against the synthetic accuracy
budget.

**Estimation accuracy (synthetic scenarios):**

| Scenario | α error | β error | alignment RMSE | budget |
|---|---|---|---|---|
| clean | 0 | 0 µs | 0 µs | PASS |
| dual_device_offset (offset+skew+jitter+loss) | 5.45e-6 | 20.9 µs | 12.6 µs | PASS |

**Multimodal (5 devices, no device observes all events):** all 5 reachable;
`camera` and `mocap` reconciled **transitively (2 hops) through `imu`**; every
device within its per-hop budget.

**Robustness — RANSAC + min-support vs TLS baseline under heavy corruption:**

| mode | spurious edges | worst α error | worst β error |
|---|---|---|---|
| TLS baseline | forms 1 spurious cross-group edge | 3.94e-3 | 5930 µs |
| **robust** | rejected | **1.62e-4** | **521 µs** |

**Coarse-clock operating limit (12 ms association tolerance):** full
reconciliation for coarse error ≤ ~8 ms; breakdown (devices unreachable) at
≥ 20 ms.

**Piecewise vs single affine (long nonlinear-drift session):** alignment RMSE
**160 µs → 55 µs**.

---

## Current Capabilities

- ✓ Multi-device clock synchronization (N devices)
- ✓ Subset-aware event association (partial, overlapping observability)
- ✓ Graph-based reconciliation, including transitive (multi-hop) paths
- ✓ Robust clock estimation (TLS, RANSAC, optional piecewise)
- ✓ Spurious-edge rejection (minimum support + confidence-weighted paths)
- ✓ Confidence propagation (source / clock / interpolation, with long-gap decay)
- ✓ Unified timeline generation (reference grid + as-of join + validity masks)
- ✓ Synthetic data generation and deterministic, reproducible scenarios
- ✓ Detection-corruption, drift, jitter, loss/burst, and coarse-clock stress tests
- ✓ Designated-anchor reference-clock policy
- ✓ Session manifests (JSON/YAML), validation reports, and QA gating
- ✓ Pluggable detector framework with two built-in impulse detectors

> **Artifacts vs. exports.** The persisted outputs above are *synchronization
> artifacts* — session manifests and benchmark reports that describe the
> synchronization (clock models, residuals, coverage, graph diagnostics). They are
> **not dataset exports**: SentrixSync currently has **no** code that writes
> synchronized data to ML/interchange formats (LeRobot, RLDS, HDF5, MCAP, Parquet).
> The unified timeline is produced **in memory** (`SyncResult.timeline`); materializing
> it to a file is not implemented (see *What Is NOT Included* and *Roadmap*).

---

## What Is NOT Included

SentrixSync is **synchronization infrastructure only**. The repository does **not**
implement, and is not intended to implement:

- object detection or tracking
- vision models or perception
- tactile perception / interpretation models
- labeling or annotation systems
- training pipelines or foundation models
- dataset generation / curation pipelines (beyond synthetic *test* scenarios)
- catalog, marketplace, commerce, or privacy systems
- **data export / timeline materialization to any format** (LeRobot, RLDS, HDF5,
  MCAP, Parquet, …) — there is **no exporter in the codebase**

A clarification, since the schema hints at it: the `Session` object has an
`ExportRecord` field (`session.exports`) and `TimelineRef.aligned_table_uri`.
These are **metadata slots** for *recording* that an export happened (its format,
URI, counts) — there is **no code that produces such an export**. Likewise,
`parquet` and `mcap` appear only as payload-reference URI *schemes* (addressing),
and `parquet` is used solely for **reading** SentrixSim episodes
(`SentrixSimAdapter.from_parquet`), never for writing output.

These are downstream Sentrix Physical Data Engine concerns. SentrixSync's single
job is to make every device's clock agree, via shared events, so that every sample
lands on one trustworthy timeline.

---

## Hardware Integration Path

```
   Real Sensor → Adapter → (Detector) → Event Stream → [ SYNCHRONIZATION CORE ]
```

Integrating a new device generally requires only:

1. **An adapter** — present the device's clock and timestamped samples (payload by
   reference). Always required.
2. **A detector** *(optional)* — turn the device's signal into local event times,
   *if* the device should contribute synchronization events.

It does **not** require changes to the contract, graph reconciliation, clock
estimation, or the confidence model. Validate adapter/detector/estimation settings
in simulation first (synthetic generator), then apply them to hardware unchanged.
Step-by-step instructions: [`docs/REAL_DEVICE_INTEGRATION_GUIDE.md`](docs/REAL_DEVICE_INTEGRATION_GUIDE.md)
and [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md).

---

## Example Use Cases

- **Tactile glove + camera** — align a 1 kHz glove with a 30 fps video using a few
  shared taps; every glove sample is then expressible in camera time.
- **Multiple cameras** — cameras sharing flash/contact events are reconciled onto
  one timeline, even when each only overlaps with some of the others.
- **Tactile + IMU** — an IMU that also senses taps is a direct synchronization
  partner; it can also bridge tactile and vision groups transitively.
- **Multimodal robotics capture** — camera, depth, glove, IMU, microphone,
  force/torque, eye tracker on one reference timeline, each added as an
  adapter (+ detector) with no core change.

In every case the workflow is identical: detect events per device →
`associate_detections(...)` → `synchronize(...)` → read the unified timeline and
per-device clock models.

---

## Install & Test

```bash
python -m venv .venv && . .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
```

Runtime dependencies: **`pyyaml`** and **`numpy`**. **`pyarrow`** is an optional
extra (`pip install -e ".[parquet]"`) needed only for reading SentrixSim Parquet
episodes; it is included in the `dev` extra.

---

## Testing

- **215 tests pass** across **27 test modules** (`pytest -q`).
- Coverage spans every layer: core contracts/entities, payload-URI grammar,
  ingestion, clock estimation (offset/affine/TLS/RANSAC/piecewise), detectors and
  association, graph reconciliation, timeline/join, confidence (incl. decay), and
  metrics/gating.
- **Robustness / corruption testing:** `test_corrupt.py`, `test_robustness.py`
  (false positives/negatives, duplicates, perturbation; RANSAC-vs-baseline;
  spurious-edge rejection; graceful degradation).
- **Drift & estimator testing:** `test_clock_estimate.py`, `test_clock_forward.py`
  (TLS, RANSAC, piecewise; offset/skew/jitter/loss/burst).
- **Graph testing:** `test_graph.py`, `test_multimodal.py` (star, transitive,
  disconnected/unreachable, per-hop accuracy).
- **Benchmark suite:** `python benchmarks/run_sync_benchmark.py` regenerates the
  scenario, multimodal, and robustness reports; the synthetic accuracy budget is
  enforced as a CI gate (`test_scenarios.py`, `test_multimodal.py`).

---

## Documentation

| Document | Purpose |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System purpose, boundaries, modules, data flow, lifecycle |
| [`docs/CONTRACT.md`](docs/CONTRACT.md) | The ingestion contract every adapter must satisfy (incl. payload-URI grammar) |
| [`docs/SESSION_SCHEMA.md`](docs/SESSION_SCHEMA.md) | The Session object and example manifests |
| [`docs/REFERENCE_CLOCK_DECISION.md`](docs/REFERENCE_CLOCK_DECISION.md) | Why the designated-anchor reference policy was chosen |
| [`docs/SUBFRAME_BUCKETING.md`](docs/SUBFRAME_BUCKETING.md) | Design note: fixed-R / repeat-pad sub-frame bucketing rule |
| [`docs/SYNTHETIC_ACCURACY_BUDGET.md`](docs/SYNTHETIC_ACCURACY_BUDGET.md) | CI accuracy budgets (per-scenario and per-hop) |
| [`docs/IMPLEMENTATION_NOTES.md`](docs/IMPLEMENTATION_NOTES.md) | What is built vs. deferred; per-phase decisions and findings |
| [`docs/REAL_DEVICE_INTEGRATION_GUIDE.md`](docs/REAL_DEVICE_INTEGRATION_GUIDE.md) | Architecture-level guide to integrating real hardware |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | Hands-on operating guide: raw recordings → unified timeline |
| [`benchmarks/sync_benchmark_report.md`](benchmarks/sync_benchmark_report.md) | Generated benchmark results (accuracy, multimodal, robustness) |

---

## Repository Layout

```
configs/      reference.yaml · devices/*.descriptor.yaml · scenarios/*.yaml
src/sentrixsync/
  core/       types · uri · device · events · timeline · session     (contracts & entities)
  ingest/     adapter · batch · sentrixsim_adapter · pipeline         (device ingestion)
  clock/      forward (corruption model) · estimate (offset/affine/TLS/RANSAC/piecewise)
  detect/     detector framework · detectors/{tactile_tap,visual_flash} · matcher · corrupt
  sync/       join · timeline · confidence (+decay) · metrics · graph (reconciliation) · engine
  scenarios/  synthetic (2-device) · multimodal (N-device graph) · robustness (corruption/coarse/piecewise)
  config.py · manifest.py · lifecycle.py
benchmarks/   run_sync_benchmark.py (+ generated report / json)
tests/        215 tests across 27 modules
docs/         architecture, contract, design notes, integration & user guides
```

---

## Roadmap

**Completed**

- ✓ Synchronization backbone — contract, ingestion, event association, clock
  estimation, graph reconciliation, unified timeline, confidence, QA gating.
- ✓ Multimodal generalization — subset-aware association, transitive graph
  reconciliation, total-least-squares estimation.
- ✓ Robustness hardening — RANSAC, spurious-edge rejection, confidence decay,
  piecewise drift, coarse-clock characterization.

**Next (in dependency order)**

- Real-hardware adapters and real-payload detectors (Serial/CSV, MP4/camera).
- Payload **resolvers** (turn a `payload_ref` URI into a signal a detector reads).
- Clock-reset / wrap / reboot detection and session re-anchoring.
- Piecewise-drift composition across multi-hop graph paths.
- Streaming / chunked timeline construction for very long, high-rate sessions.
- Timeline materialization and data exporters (e.g. LeRobot / MCAP) — **not yet
  implemented**; the `ExportRecord` schema slot exists to record such exports once
  a producer is built.

The repository deliberately avoids speculative long-term claims beyond this; each
item above is grounded in the "deferred" list in `IMPLEMENTATION_NOTES.md`.

---

## Relationship to SentrixSim

SentrixSim (the tactile simulator) is *a* producer; SentrixSync is the
*synchronization framework*. SentrixSim is no longer the only producer — the
SentrixCapture real-hardware glove emits the **same Parquet artifact contract**
(a device-local timestamp column + `sensor_id`-keyed payload columns +
self-describing metadata), so the shipped `SentrixSimAdapter` reads both
identically (it differs only by `ts_column`: `t_master_us` for Sim,
`t_capture_us` for Capture). SentrixSim's internal multi-rate consolidation
(BMM350 / LIS2DTW12 / temperature onto one hub clock) is *intra-device* and stays
in SentrixSim. To SentrixSync, the whole glove is **one device, one clock** — with
no code change in either producer, and nothing flowing from SentrixSync back into
them.
