# SentrixSync — Architecture

**Status:** Approved architecture, pre-implementation. No code exists yet.
**Scope of this document:** system purpose, repository boundaries, relationships, modules, data flow, synchronization lifecycle, extensibility.
**Companion documents:** [`CONTRACT.md`](./CONTRACT.md) (ingestion contract), [`SESSION_SCHEMA.md`](./SESSION_SCHEMA.md) (the Session object), [`REFERENCE_CLOCK_DECISION.md`](./REFERENCE_CLOCK_DECISION.md) (reference-clock policy).

---

## 1. System Purpose

SentrixSync is the **timeline layer** of the Sentrix ecosystem. It answers one question, for any set of sensors:

> Given streams from N devices that each keep their own clock, what is the single reference timeline, how does every sample map onto it, and how trustworthy is that mapping?

It models, end to end, the lifecycle of multi-device synchronization:

- independent device clocks, clock **offset**, clock **drift**, timestamp **jitter**, packet **delay**, packet **loss/dropout** (the *forward* / generative side — for synthetic devices and stress tests);
- **affine clock correction**, **synchronization confidence**, **residual/error metrics**, and **unified timeline generation** (the *inverse* / estimation side — run for every device, synthetic or real).

This **forward/inverse duality** is the framework's defining property. Because a synthetic scenario has a *known* ground-truth clock relationship, the estimator's accuracy can be **measured**, not asserted. This directly answers the CTO Review's central concern (§4.3): *"quality is consistency, never accuracy."* In SentrixSync, synchronization accuracy is a number with ground truth behind it.

SentrixSync is **not** a simulator, a renderer, a catalog, a commerce layer, or a privacy pipeline. Per the CTO philosophy (§4.4 — collapse operational surface area), it builds only the timeline moat and refuses to grow into the others.

### Design principles (inherited from the CTO Review and SentrixSim)

1. **Build only the minimum** for the next milestone; defer everything a customer or real device has not yet forced.
2. **Never fabricate.** Adopt SentrixSim's KNOWN / ESTIMATED / UNKNOWN parameter tiering and confidence scoring for every clock and transport parameter.
3. **Mark, don't invent.** Never interpolate across a dropout; flag a gap and decay confidence.
4. **Measure accuracy where ground truth exists.** Round-trip synthetic scenarios to produce real error numbers.
5. **One stable contract.** All extensibility flows through a single versioned ingestion contract; the core stays modality-agnostic.

---

## 2. Repository Boundaries

The boundary between SentrixSync and every data producer is defined by **clock domain**:

> A **device** in SentrixSync is anything that keeps **one clock**. SentrixSync never reaches inside a clock domain; it only relates clock domains to one another.

This produces two distinct, non-overlapping levels of synchronization:

| Level | Scope | Owner |
|---|---|---|
| **Intra-device** multi-rate consolidation | Multiple sensors sharing **one** hub clock | The producer (e.g. SentrixSim's existing L6 sync layer) |
| **Inter-device** clock reconciliation | Multiple **independent** clocks | **SentrixSync** |

**SentrixSync owns:**
- the device/stream/sample/sync-event ingestion contract;
- per-device clock modelling (forward corruption + inverse estimation);
- reference-clock selection and unified timeline generation;
- cross-modal as-of joining, gap flagging, and confidence;
- synchronization residual/error metrics and QA gating;
- the Session object and its manifests, synchronization reports, and validation reports;
- multimodal alignment artifacts and (thin) multimodal export, because alignment is inherently cross-modal.

**SentrixSync does NOT own:**
- sensor physics, signal generation, or rendering (producers own these);
- intra-device multi-rate consolidation (the producer delivers one clock per device);
- payload content — payloads are carried **by reference** (URI/handle); the core touches timestamps and light metadata only;
- the catalog, search, pricing, entitlements, or privacy/PII pipeline (Data Engine concerns, deferred).

---

## 3. Relationship to SentrixSim

**SentrixSim is unchanged by SentrixSync.** It keeps owning tactile physics, sensor models, dataset generation, validation, benchmarking, and tactile-only export. Its internal L6 layer stays — it consolidates the glove's own sensors (BMM350 @≤400 Hz, LIS2DTW12 @1600 Hz, temperature @≤50 Hz) onto **one** hub grid. That is *intra-device* consolidation, not cross-device sync.

To SentrixSync, the entire glove presents as **one device, one clock, one (multi-channel) tactile stream**. SentrixSim already emits everything required: a microsecond master timeline, per-channel validity masks, and per-sample data with provenance.

Integration is via a thin **adapter that lives in SentrixSync**, wrapping SentrixSim output (in-memory Episode, Parquet, or MCAP) as a Device that satisfies the ingestion contract. **No code change is required in SentrixSim** for first integration.

A corollary discipline: SentrixSim should *not* re-acquire any ambition to do cross-device synchronization. That responsibility now has a dedicated home.

---

## 4. Relationship to Future Data Engine Components

SentrixSync sits **between producers and the Data Engine**. It produces the aligned, timeline-correct, confidence-annotated representation that downstream Data Engine phases consume:

| Data Engine phase (Architecture Manual) | How SentrixSync relates |
|---|---|
| **2.1 Ingestion & Hardware Sync** | SentrixSync *is* the software realization of this phase's clock model and as-of join. |
| **3.1 Event Alignment & Temporal Interpolation** | SentrixSync produces the unified timeline, per-modality interpolation kernels, validity/confidence masks, and gap flags. |
| **5 Export Layer** | SentrixSync emits the canonical aligned artifact and the multimodal LeRobot/MCAP exports; the sub-frame tactile burst is bucketed per anchor frame here. |
| **7 QA Gates** | SentrixSync's synchronization residual and validation reports feed the release/certified gates (`< 2 ms` release, `< 0.5 ms` certified, `≥ 5 ms` hard fail). |
| **3.2–3.4 Autolabeling, 4 Catalog, 6 Privacy** | **Out of scope.** SentrixSync provides their input timeline; it does not implement them. |

SentrixSync deliberately records the same vocabulary the Data Engine expects (synchronization method, residual in microseconds, certified/release verdicts), so later catalog integration is a mapping exercise, not a redesign.

---

## 5. Major Modules

The repository is organized into a small number of responsibility areas. The moat is **clock**, **timeline**, and **metrics**; everything else is thin.

| Module area | Responsibility |
|---|---|
| **core** | Canonical entity definitions (Device, Stream, Sample, SyncEvent, ClockModel, Timeline, Session, reports) and the parameter registry with KNOWN/ESTIMATED/UNKNOWN tiering. No modality logic. |
| **ingest** | The `DeviceAdapter` contract (the one stable interface) and concrete adapters (SentrixSim tactile now; synthetic vision, MCAP replay, real hardware later). Adapters translate a producer into contract-conformant records. |
| **clock** | The affine (+ optional piecewise) clock model; the **forward** corruption model (offset, drift, jitter, packet delay, loss); the **inverse** estimator (robust affine fit) with uncertainty/confidence. |
| **events** | Modality-specific **shared-event detectors** (e.g. tap impulse, flash) that emit generic SyncEvents, and the cross-device matcher. Detectors are edge plugins; the core consumes only SyncEvents. |
| **timeline** | Reference-clock selection, master-grid generation (configurable rate), as-of join / interpolation kernels, gap flagging, and sub-frame bucketing. |
| **metrics** | Synchronization residual, coverage, dropout, interpolation-confidence statistics, round-trip accuracy (when ground truth exists), and QA gates. |
| **timelineio** | The unified Timeline manifest (canonical output), optional materialized aligned table, and thin multimodal exporters. |
| **cli / configs / docs / tests** | Operator entry points, device/scenario/reference profiles, this documentation, and the scenario regression suite. |

---

## 6. Data Flow

A **forward** path (synthetic devices and stress tests only) feeds an **inverse** path (always run):

```
            [ FORWARD — synthetic / stress only ]
ideal samples ─► clock.forward (offset, drift, jitter, delay, loss) ─► "as-received" timestamped samples
                                                                                  │
                                                                                  ▼
[ INVERSE — always ]
S0  ingest      adapters -> Sample records (t_device, payload_ref, seq, meta)
S1  evidence    gather sync evidence: hardware/PTP timestamps | detected shared events | wall-clock coarse
S2  estimate    fit per-device ClockModel (alpha, beta [+ segments]) to the reference clock + uncertainty
S3  correct     map every t_device -> t_ref
S4  timeline    select reference clock -> build master grid -> as-of join / interpolate each stream
                -> validity mask + interpolation confidence + gap flags (reject beyond tolerance tau)
S5  metrics     synchronization residual | coverage | dropout | round-trip accuracy | QA gates
S6  emit        unified Timeline manifest (+ optional materialized table, + multimodal export)
```

**Payloads never enter the core.** They are referenced (URI/handle) and resolved only at materialization/export time. This keeps the engine lightweight and modality-agnostic.

The **real-hardware flow is identical from S1 onward.** Only S0 (which adapter) and the source of evidence change: the forward-corruption block is replaced by *measured* timestamps and *measured* PTP/event evidence. The estimator and timeline logic do not change.

---

## 7. Synchronization Lifecycle

A single Session moves through these stages:

1. **Register.** Each device registers with its descriptor: identity, modality, clock descriptor, declared evidence tiers, and stream descriptors. (See `CONTRACT.md`.)
2. **Stream.** Adapters emit timestamped Sample records (payload by reference) for each stream.
3. **Collect evidence.** The best available synchronization evidence is gathered per device — hardware/PTP timestamps, detected shared physical events, or coarse wall-clock — and recorded with its tier.
4. **Estimate clocks.** A per-device ClockModel is fit against the chosen reference clock; uncertainty and fit residual are retained and converted to a confidence.
5. **Select reference & build timeline.** A reference clock is selected (policy in `REFERENCE_CLOCK_DECISION.md`); a master grid is generated at a configurable rate (default: the highest native rate among joined streams — **not** anchored to any assumed video rate).
6. **Align.** Each stream is joined onto the grid using its declared kernel (continuous → interpolate; hold → latest-at). Gaps beyond tolerance are flagged, not filled; interpolation confidence decays with gap size.
7. **Score.** Synchronization residual, coverage, dropout, and (for synthetic) round-trip accuracy are computed; QA gates produce a release/certified/blocked verdict.
8. **Emit.** A unified Timeline manifest, a synchronization report, and a validation report are written into the Session. Optional materialization and multimodal export follow.

The lifecycle **degrades gracefully to N=1**: a single device makes its own clock the reference, residual is trivial/undefined, confidence is 1, and the timeline is that device's stream. **Vision is never assumed at any stage.**

---

## 8. Future Extensibility

Extensibility is concentrated at two well-defined edges, never in the core:

- **New modality (RGB, depth, tracking, IMU, audio, …):** add a new **adapter** (and, if it can observe shared events, a **detector**). Because the core branches only on declared **kernel**, **rate**, **units**, and **payload_kind** — never on modality — no core change is required.
- **New evidence source (PTP grandmaster, GPS-disciplined oscillator, latency probe):** add a new evidence tier producer. The estimator consumes evidence uniformly; synthetic and real evidence are interchangeable by construction.

Planned integration path:

| Phase | What plugs in | What it proves |
|---|---|---|
| **A — Single-modality wiring** | SentrixSim tactile adapter; N=1 | The contract, the manifest, graceful N=1, the QA gates — end to end on existing data |
| **B — Synthetic visuotactile (v0.3)** | A second device: synthetic vision on an independent clock | First true cross-modal sync; sub-frame tactile burst per anchor frame; multimodal export; measured alignment error |
| **C — Real hardware** | Mark 2 glove + RGB + depth + tracker adapters | Same engine on real timestamps — no architectural change |
| **D — Scale** | Persistent timeline store; native viz; many devices | Throughput and multi-session operations |

The hardest, most uncertain Data Engine component — markerless 6-DoF object-state inference — is **not required** for Phases A–B, because synthetic devices supply ground-truth pose by construction. SentrixSync's job is timeline correctness, not perception; the perception stack stays deferred until real video (Phase C) makes it unavoidable.
