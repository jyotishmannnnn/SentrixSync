# SentrixSync — Session Schema

**Status:** Implemented (`core/session.py`); schema version 0.3.0.
**Scope:** the Session as a first-class object — its parts, the references it holds, and illustrative manifests. This describes a *document structure*, not a database schema or an API.
**Companion documents:** [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`CONTRACT.md`](./CONTRACT.md), [`REFERENCE_CLOCK_DECISION.md`](./REFERENCE_CLOCK_DECISION.md).

---

## 1. The Session as a First-Class Object

A **Session** is the unit of synchronized capture or generation across one or more devices. It is the thing SentrixSync ingests, synchronizes, scores, and emits. Everything SentrixSync produces is anchored to a Session.

A Session is **self-describing and reference-based**: it holds metadata, device registrations, and *pointers* (URIs/handles) to calibration artifacts, raw streams, the generated timeline, and reports. It does **not** embed bulk payloads — consistent with the contract's payload-by-reference rule. A Session manifest is small enough to read at a glance and version in plain text.

A Session is also the natural boundary for SentrixSim alignment: where SentrixSim speaks of an **episode** (one gesture realization on one device), a SentrixSync **Session** is the multi-device envelope around one or more such episodes that share a synchronization context. A single-device session is fully valid (the N=1 case).

### Top-level parts of a Session

| Part | Purpose |
|---|---|
| **Session metadata** | Identity, timing, provenance, contract/schema versions. |
| **Device registrations** | The DeviceDescriptors participating (per `CONTRACT.md`). |
| **Calibration references** | Pointers to temporal and spatial calibration artifacts. |
| **Timeline references** | Pointers to the generated reference timeline and aligned artifacts. |
| **Synchronization report** | Per-device clock models, reference-clock choice, residuals. |
| **Validation report** | Property checks, round-trip accuracy (synthetic), QA gate verdict. |
| **Export metadata** | What was materialized/exported and where. |
| **Ground-truth block** (synthetic only) | Segregated true clock relationships, for validation only. |

---

## 2. Session Metadata

Identity and provenance of the session. Conceptual fields:

| Field | Meaning |
|---|---|
| `session_id` | Stable unique identity (e.g. a ULID). |
| `schema_version` | Version of this Session document structure. |
| `contract_version` | Ingestion-contract version the devices conform to. |
| `sentrixsync_version` | Version of the framework that produced the session. |
| `created_at` | Creation timestamp (wall clock, informational). |
| `origin` | `synthetic` \| `real` \| `mixed`. |
| `producers` | List of producing systems (e.g. `sentrixsim`, `synthetic_vision`, `mark2_glove`). |
| `reference_clock_policy` | The policy used to choose the reference (see `REFERENCE_CLOCK_DECISION.md`). |
| `grid_rate_hz` | Reference-timeline rate (configurable; default = highest joined native rate). |
| `rejection_tolerance_us` | The gap tolerance `tau` beyond which values are flagged invalid. |
| `notes` | Free text. |

---

## 3. Device Registration

A list of the DeviceDescriptors (defined in `CONTRACT.md`) that participate in the session. The Session records, per device, only the descriptor plus the resolved role:

| Field | Meaning |
|---|---|
| `device_id` | From the descriptor. |
| `descriptor_ref` | Pointer to (or inline copy of) the DeviceDescriptor. |
| `role` | `reference` \| `follower`. Exactly one device is `reference` per session (the N=1 device is trivially the reference). |
| `stream_refs` | Pointers to where each stream's raw samples live (e.g. Parquet/MCAP URIs). |

The Session never stores samples inline; it points at them.

The DeviceDescriptor a registration carries may include two optional **opaque provenance** fields (CONTRACT.md §3), which flow verbatim into the manifest so the Data Engine can trace and package them:

| Field | Meaning |
|---|---|
| `topology_ref` | Hardware-revision topology-descriptor version the device's streams were produced under, e.g. `Mark2_v1`. |
| `topology_hash` | Content hash of that descriptor, e.g. `sha256:…`. |

Both are **never consumed by synchronization** (same discipline as `calibration_refs`). When an adapter reads a producer's parquet, it may auto-fill these from the file's self-describing metadata if the caller left them unset.

---

## 4. Calibration References

SentrixSync **references** calibration; it does not own or parse it. Two kinds:

| Kind | Owner | Referenced because |
|---|---|---|
| **Temporal calibration** | SentrixSync (clock-fit logs, PTP/event logs) | The synchronization report cites the evidence used. |
| **Spatial calibration** | Producers / capture rig (intrinsics, extrinsics, hand–eye) | Downstream Data Engine needs it; SentrixSync only carries the pointer. |

| Field | Meaning |
|---|---|
| `calibration_id` | Identity of the artifact. |
| `kind` | `clock_fit` \| `intrinsics` \| `extrinsics` \| `hand_eye` \| `other`. |
| `device_id` / `device_ids` | Which device(s) it pertains to. |
| `uri` | Pointer to the artifact. |
| `tier` / `confidence` | KNOWN/ESTIMATED/UNKNOWN classification, mirroring SentrixSim. |

---

## 5. Timeline References

Pointers to the generated reference timeline and any aligned/materialized artifacts.

| Field | Meaning |
|---|---|
| `timeline_id` | Identity of the generated timeline. |
| `reference_clock_id` | The clock the timeline is expressed in. |
| `grid_rate_hz` | The grid rate actually used. |
| `t_start_us` / `t_end_us` | Reference-time span. |
| `n_grid` | Number of grid points. |
| `manifest_uri` | Pointer to the unified timeline manifest (per-stream join maps, validity masks, interpolation confidence). |
| `aligned_table_uri` | Pointer to the materialized aligned columnar table, if produced (optional). |
| `subframe_buckets` | Description of any per-anchor-frame sub-frame bucketing applied (e.g. high-rate tactile burst per image frame), including the resampling/padding rule used. |

---

## 6. Synchronization Report

The record of how synchronization was achieved and how good it is.

| Field | Meaning |
|---|---|
| `reference_clock_id` | Chosen reference. |
| `reference_selection` | How it was chosen (policy + rationale, e.g. `designated_anchor: highest_rate`). |
| `per_device` | For each follower device: its fitted ClockModel summary — `alpha`, `beta`, optional segment boundaries, `method` (the evidence tier used, e.g. `shared_event`), `fit_residual_us`, `n_events`, `clock_confidence`. |
| `sync_method` | The dominant evidence tier across the session (matches the Data Engine vocabulary). |
| `sync_resid_us` | Aggregate synchronization residual (e.g. RMS of matched-event disagreement in reference time). |
| `coverage` | Fraction of the grid each stream covers with valid (non-gap) values. |
| `dropout` | Per-stream dropout/loss fraction. |

---

## 7. Validation Report

The record of correctness and (where possible) accuracy.

| Field | Meaning |
|---|---|
| `property_checks` | Pass/fail for invariants: reference timestamps strictly monotonic, bounded grid step, no values fabricated across flagged gaps, join indices consistent. |
| `roundtrip_accuracy` | **Synthetic only.** Recovered-vs-true clock error (`alpha`/`beta` error) and end-to-end alignment RMSE, computed against the segregated ground-truth block. Absent for real sessions. |
| `gate_verdict` | `certified` \| `release` \| `needs_review` \| `blocked`, using the Data Engine thresholds (`< 2 ms` release, `< 0.5 ms` certified, `≥ 5 ms` hard fail), plus coverage/dropout gates. |
| `gate_detail` | Which metric drove the verdict. |

---

## 8. Export Metadata

What was produced for downstream consumption.

| Field | Meaning |
|---|---|
| `exports` | List of produced artifacts, each with `format` (e.g. `lerobot`, `mcap`, `aligned_parquet`), `uri`, `produced_at`, and `frame_count` / `sample_count`. |
| `consumer_hint` | Optional note on intended downstream phase (e.g. Data Engine export layer). |

SentrixSync stops at the aligned/exported artifact. Catalog ingestion, pricing, entitlements, and privacy are out of scope and not represented here.

---

## 9. Example Session Manifests

The examples below are **illustrative documents** showing structure and references — not a storage schema and not exhaustive. Values are placeholders.

### 9.1 Synthetic single-device session (N=1, tactile only — Phase A)

This is the first integration target: SentrixSim wrapped as one device, no vision, trivial synchronization.

```yaml
session_id: 01J9SYNTH0001
schema_version: 0.3.0
contract_version: 1.1.0
sentrixsync_version: 0.4.0
origin: synthetic
producers: [sentrixsim]
reference_clock_policy: designated_anchor
grid_rate_hz: 1600
rejection_tolerance_us: 1875        # ~3 master steps at 1600 Hz

devices:
  - device_id: glove_L
    descriptor_ref: devices/glove_L.descriptor.yaml
    role: reference                 # only device -> trivially the reference
    stream_refs:
      tactile_field: streams/glove_L/tactile.parquet
      dynamics:      streams/glove_L/dynamics.parquet

calibration_refs: []                # none needed in pure-synthetic N=1

timeline:
  timeline_id: tl_01J9SYNTH0001
  reference_clock_id: glove_L_hub
  grid_rate_hz: 1600
  manifest_uri: timeline/manifest.json
  subframe_buckets: null            # no anchor-frame modality present

sync_report:
  reference_clock_id: glove_L_hub
  reference_selection: "single device -> reference"
  per_device: {}                    # no followers to fit
  sync_method: none
  sync_resid_us: 0.0
  coverage: { tactile_field: 1.0, dynamics: 1.0 }

validation_report:
  property_checks: { monotonic: pass, bounded_step: pass, no_fabricated_gaps: pass }
  roundtrip_accuracy: { note: "trivial: reference clock, no correction applied" }
  gate_verdict: certified
  gate_detail: "single-clock session; residual not applicable"

exports:
  - format: aligned_parquet
    uri: export/aligned.parquet
    sample_count: 1024

ground_truth:
  clock_models:
    glove_L: { alpha: 1.0, beta_us: 0 }   # segregated; never seen by the estimator
```

### 9.2 Synthetic visuotactile session (two devices, independent clocks — Phase B / v0.3 milestone)

Vision is shown here only to illustrate cross-device sync; it is not assumed by the framework.

```yaml
session_id: 01J9VT00002
schema_version: 0.3.0
contract_version: 1.1.0
origin: synthetic
producers: [sentrixsim, synthetic_vision]
reference_clock_policy: designated_anchor
grid_rate_hz: 1600
rejection_tolerance_us: 1875

devices:
  - device_id: glove_L
    role: reference                 # highest-rate clock -> anchor
    stream_refs: { tactile_field: streams/glove_L/tactile.parquet }
  - device_id: ego_cam
    role: follower
    stream_refs: { image: streams/ego_cam/frames.mcap }

calibration_refs:
  - { calibration_id: cam_intr_01, kind: intrinsics, device_id: ego_cam, uri: calib/ego_cam_intrinsics.json, tier: ESTIMATED, confidence: 0.6 }

timeline:
  timeline_id: tl_01J9VT00002
  reference_clock_id: glove_L_hub
  grid_rate_hz: 1600
  subframe_buckets:
    anchor_stream: image            # tactile bucketed per image frame
    rule: "fixed R per frame; boundary-padded; R = round(1600/fps)"

sync_report:
  reference_clock_id: glove_L_hub
  reference_selection: "designated_anchor: highest_rate"
  per_device:
    ego_cam:
      alpha: 1.000018
      beta_us: 20431
      method: shared_event          # tap impulse seen in tactile + flash in video
      fit_residual_us: 280.0
      n_events: 6
      clock_confidence: 0.93
  sync_method: shared_event
  sync_resid_us: 280.0
  coverage: { tactile_field: 1.0, image: 0.998 }
  dropout: { image: 0.002 }

validation_report:
  property_checks: { monotonic: pass, bounded_step: pass, no_fabricated_gaps: pass }
  roundtrip_accuracy:
    ego_cam: { alpha_err: 0.000004, beta_err_us: 35, alignment_rmse_us: 190 }
  gate_verdict: release             # < 2 ms residual, not yet < 0.5 ms certified
  gate_detail: "sync_resid_us=280 -> release band"

exports:
  - { format: lerobot, uri: export/lerobot/, frame_count: 1800 }

ground_truth:
  clock_models:
    ego_cam: { alpha: 1.000020, beta_us: 20466 }   # segregated; validation only
```

### 9.3 Real hardware session (multi-device — Phase C)

Structurally identical to 9.2. The differences are confined to the edges: `origin: real`, no `ground_truth` block, measured evidence, real calibration.

```yaml
session_id: 01J9REAL0003
schema_version: 0.3.0
contract_version: 1.1.0
origin: real
producers: [sentrixcapture, rgb_cam, depth_cam, optical_tracker]
reference_clock_policy: designated_anchor
grid_rate_hz: 1000
rejection_tolerance_us: 3000

devices:
  # topology_ref/topology_hash are opaque provenance carried verbatim from the
  # producer's parquet metadata; synchronization never reads them.
  - { device_id: glove_R, role: reference, topology_ref: Mark2_v1, topology_hash: "sha256:…",
      stream_refs: { tactile_field: ... , dynamics: ... } }
  - { device_id: rgb_cam,  role: follower, stream_refs: { image: ... } }
  - { device_id: depth_cam, role: follower, stream_refs: { depth_map: ... } }
  - { device_id: tracker,  role: follower, stream_refs: { pose6d: ... } }

calibration_refs:
  - { calibration_id: clkfit_rgb, kind: clock_fit, device_id: rgb_cam, uri: ..., tier: KNOWN, confidence: 0.95 }
  - { calibration_id: extr_rig,   kind: extrinsics, device_ids: [glove_R, rgb_cam, depth_cam, tracker], uri: ... }

sync_report:
  reference_clock_id: glove_R_hub
  reference_selection: "designated_anchor: most_trusted_clock"
  per_device:
    rgb_cam:    { method: shared_event, fit_residual_us: 410, clock_confidence: 0.9 }
    depth_cam:  { method: shared_event, fit_residual_us: 520, clock_confidence: 0.88 }
    tracker:    { method: hardware_ptp, fit_residual_us: 8,   clock_confidence: 0.99 }
  sync_method: mixed
  sync_resid_us: 520.0

validation_report:
  property_checks: { monotonic: pass, bounded_step: pass, no_fabricated_gaps: pass }
  roundtrip_accuracy: null          # no ground truth for real data
  gate_verdict: release
  gate_detail: "worst follower depth_cam at 520 us"

exports:
  - { format: mcap, uri: export/session.mcap }
```

---

## 10. What the Schema Deliberately Omits

Per the CTO philosophy, the Session schema does **not** include: customer/licensing fields, pricing, entitlements, watermarking/lineage, PII/redaction audit, vector embeddings, or search metadata. These belong to Data Engine phases SentrixSync does not implement. The schema also avoids any field that would force the core to understand a specific modality — modality appears only as free metadata and as stream `kind`, never as behaviour.
