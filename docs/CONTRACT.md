# SentrixSync — Ingestion Contract

**Status:** Implemented in core (Phase 1) and ingestion (Phase 4).
**Contract version:** 1.0.1 (PATCH — clarifies `payload_inline` and the payload-reference URI grammar; no breaking change).
**Scope:** the data contract every device adapter must satisfy. This document defines *data shapes and rules*, not function signatures, APIs, or storage schemas.
**Companion documents:** [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`SESSION_SCHEMA.md`](./SESSION_SCHEMA.md), [`REFERENCE_CLOCK_DECISION.md`](./REFERENCE_CLOCK_DECISION.md).

> **Changelog**
> - **1.0.1** — Named the `payload_inline` field and the *exactly-one-of* `payload_ref`/`payload_inline` rule (§5); defined the payload-reference URI grammar (§6).
> - **1.0.0** — Initial contract.

---

## 1. Why a Contract

SentrixSync supports synthetic and real devices, across many modalities (RGB, depth, tracking, tactile, IMU, audio, and others), **without architectural change**. The mechanism that makes this possible is a single, stable, versioned ingestion contract. Anything that can present its data in the shapes below is a valid device. The core never knows or cares what kind of sensor produced the data.

This document defines four record kinds an adapter exposes:

1. **DeviceDescriptor** — what a device is, once per device per session.
2. **StreamDescriptor** — what a channel is, once per stream.
3. **Sample** — one timestamped record on a stream.
4. **SyncEvent** — one cross-device synchronization fiducial.

Field tables use conceptual types (e.g. *integer microseconds*, *string*, *URI*, *float in [0,1]*). These are contract semantics, not a language binding.

---

## 2. Modality-Neutrality Rule (load-bearing)

> The SentrixSync core MUST NOT branch on modality. The only declared properties that may influence core behaviour are **kernel**, **nominal rate**, **units**, and **payload kind** (treated as an opaque token).

Consequences, binding on all contributors:

- `modality` is metadata for humans and reports only. No code in `core`, `clock`, `timeline`, or `metrics` may read it to decide behaviour.
- Modality-specific logic lives **only** in adapters (`ingest`) and event detectors (`events`). These are edge plugins.
- Payloads are **carried by reference**. The core operates on timestamps and light metadata. There is no place in the contract for the core to require pixels, taxel arrays, or audio buffers.
- Adding RGB, depth, audio, or any future sensor is therefore a new adapter (and optionally a detector) — never a contract or core change.

A contract revision that special-cases a modality in the core is a contract violation, regardless of how convenient it is.

---

## 3. DeviceDescriptor

One per device per session. A **device is exactly one clock domain.**

### Required fields

| Field | Type | Meaning |
|---|---|---|
| `device_id` | string (stable, unique within session) | Identity of the clock domain. |
| `modality` | string (open vocabulary: `tactile`, `rgb`, `depth`, `pose`, `imu`, `audio`, …) | Human/report metadata only. Never read by the core. |
| `producer` | string | Origin, e.g. `sentrixsim`, `synthetic_vision`, `mark2_glove`. |
| `is_synthetic` | boolean | Whether timestamps are generated or measured. Affects validation (round-trip), not estimation. |
| `clock` | ClockDescriptor (below) | Properties of this device's clock. |
| `evidence_tiers` | list of strings | Which synchronization-evidence tiers this device can supply (see §7). Ordered best-first. |
| `streams` | list of StreamDescriptor | The channels this device produces. At least one. |

### Optional fields

| Field | Type | Meaning |
|---|---|---|
| `reference_candidate` | boolean (default false) | Whether this device may serve as the session reference clock. |
| `calibration_refs` | list of URIs | Pointers to spatial/temporal calibration artifacts owned elsewhere (intrinsics, extrinsics, clock-fit logs). Referenced, not parsed by the core. |
| `param_tiers` | object | KNOWN/ESTIMATED/UNKNOWN classification + confidence for clock/transport parameters, mirroring SentrixSim's registry. |
| `notes` | string | Free text. |

### ClockDescriptor

| Field | Type | Required | Meaning |
|---|---|---|---|
| `clock_id` | string | yes | Identity of the underlying clock (two devices may share one in wired setups). |
| `timestamp_unit` | string | yes | Must be `microseconds` for v0.3 (see §6). |
| `resolution_us` | integer | yes | Smallest representable timestamp step. |
| `nominal_epoch` | string | optional | `device_boot`, `unix`, `session_start`, or `unspecified`. Declares what t=0 means; does not need to match other devices. |
| `expected_offset_us` / `expected_skew_ppm` / `expected_drift` | numeric | optional | Declared priors (e.g. from a datasheet or a forward model). Tiered KNOWN/ESTIMATED/UNKNOWN. |

---

## 4. StreamDescriptor

One per channel. Declares the *only* properties the core uses to align it.

### Required fields

| Field | Type | Meaning |
|---|---|---|
| `stream_id` | string (unique within device) | Channel identity. |
| `device_id` | string | Owning device. |
| `kind` | string (open vocabulary) | Human/report metadata, e.g. `tactile_field`, `image`, `depth_map`, `pose6d`. Never read by the core. |
| `nominal_rate_hz` | float | Expected sampling rate. May be `null` for irregular/event streams. |
| `kernel` | string enum: `continuous` \| `hold` | How the core resamples it. `continuous` → band-limited interpolation; `hold` → latest-at (zero-order hold). **This is the core's only behavioural switch.** |
| `payload_kind` | string (opaque token) | Describes the referenced payload for downstream resolvers; opaque to the core. |
| `units` | string | Physical units of the payload, for reports and downstream consumers. |

### Optional fields

| Field | Type | Meaning |
|---|---|---|
| `payload_shape` | list of integers | Shape hint for materialization (e.g. tactile cluster count × axes). Not required by the core. |
| `subframe_capable` | boolean | Whether this stream can be bucketed into per-anchor-frame bursts (e.g. high-rate tactile). |
| `quality_floor` | float in [0,1] | A producer-declared lower bound on per-sample confidence. |

**Kernel guidance:** continuous physical quantities (force, IMU, pose, depth values) declare `continuous`; codec-locked or discrete channels (image frames, semantic flags, event markers) declare `hold`. When unsure, declare `hold` — it never fabricates intermediate values.

---

## 5. Sample

One timestamped record on a stream. This is the high-volume record; keep it minimal.

### Required fields

| Field | Type | Meaning |
|---|---|---|
| `stream_id` | string | Owning stream. |
| `t_device_us` | integer microseconds | Timestamp **in the device's own clock**. Not pre-corrected. |
| *payload* | — | **Exactly one** of `payload_ref` or `payload_inline` must be present (see below). |

### Payload fields (exactly one required)

| Field | Type | Meaning |
|---|---|---|
| `payload_ref` | URI / handle | Reference to the payload. When it is a URI it MUST conform to the grammar in §6; an opaque handle is also permitted. The normal case for image-, array-, or buffer-sized payloads. |
| `payload_inline` | scalar / tiny fixed value | Inline payload, permitted **only** for trivially small values (e.g. a single pose). Never image-, array-, or buffer-sized. |

A sample carrying neither, or both, is rejected at ingestion.

### Optional fields

| Field | Type | Meaning |
|---|---|---|
| `seq` | integer | Monotonic sequence number from the producer. Enables loss/reorder detection independent of timestamps. |
| `t_recv_us` | integer microseconds | Arrival time at the host, if the transport observed it. Enables packet-delay analysis. |
| `valid` | boolean (default true) | Producer-asserted validity (e.g. a sensor dropout the producer already knows about). |
| `confidence` | float in [0,1] | Producer-asserted per-sample confidence. |
| `meta` | object | Small per-sample metadata. Must not contain bulk payload. |

---

## 6. Timestamp Requirements

These rules are mandatory; violations are rejected at ingestion with a clear error (never silently corrected).

1. **Unit.** All timestamps are **integer microseconds**. v0.3 fixes the unit to remove ambiguity; nanosecond support is a future contract revision if a device justifies it.
2. **Device-local.** `t_device_us` is expressed in the device's own clock. Adapters MUST NOT pre-correct to reference time — correction is SentrixSync's job, and pre-correcting destroys the evidence it needs.
3. **Monotonic per stream.** Within a stream, `t_device_us` MUST be non-decreasing. Strictly increasing is preferred; equal timestamps are permitted only when `seq` disambiguates order.
4. **No fabricated regularity.** Adapters MUST pass real (possibly irregular, jittered, or gapped) timestamps. Adapters MUST NOT resample to a clean grid — that discards jitter/drift the estimator must observe.
5. **Resolution honesty.** Declared `resolution_us` must reflect the true quantization. A device that timestamps at 1 ms must declare `resolution_us: 1000`, not `1`.
6. **Inline payload limit.** Inline payload (`payload_inline`) is permitted only for scalar or tiny fixed-size values (e.g. a single pose). Anything image-, array-, or buffer-sized MUST be a `payload_ref`.

### Payload-reference URI grammar (1.0.1)

When `payload_ref` is a URI (as opposed to an opaque handle), it MUST take the form:

```
<scheme>://<location>[#<fragment>]
```

- **Supported schemes (v0.3):** `file`, `mcap`, `parquet`, `memory`. The set is **extensible** — additional schemes (e.g. `rerun`) may be registered without a contract change.
- `location` MUST be non-empty. `fragment` is optional and addresses a sub-element (e.g. `#stream=tactile_field&row=12`).
- This is an **addressing** grammar only. SentrixSync parses and validates payload URIs; it does **not** resolve, open, or read them (resolvers are deliberately out of scope for v0.3).
- An opaque (non-URI) handle remains permitted for producers that cannot express a URI; such handles are passed through untouched and are not validated against this grammar.

Examples:

```
file:///data/ep0001/frames.mp4
mcap://session-42/egocentric#channel=image
parquet:///abs/ep.parquet#stream=tactile_field&row=12
memory://glove_L#stream=tactile_field&row=0
```

---

## 7. SyncEvent Structure

A SyncEvent is the currency of cross-device alignment: a single physical or logical fiducial observed by two or more devices, used to fit the clock relationship.

### Required fields

| Field | Type | Meaning |
|---|---|---|
| `event_id` | string | Identity of the fiducial. |
| `tier` | string enum: `hardware_ptp` \| `shared_event` \| `wall_clock` | The evidence class. Drives estimator weighting and the recorded `sync_method`. |
| `observations` | mapping `device_id -> t_device_us` | Each observing device's local timestamp of the same event. At least two entries to be useful for cross-device fitting. |

### Optional fields

| Field | Type | Meaning |
|---|---|---|
| `detector` | string | Which detector produced it (e.g. `tap_impulse`, `flash`, `ptp_exchange`). |
| `quality` | float in [0,1] | Detector confidence in the match. Down-weights uncertain events in the fit. |
| `kind` | string | Physical nature (`impulse`, `optical_flash`, `clock_exchange`, …). Metadata. |
| `meta` | object | Small detector metadata. |

### Tiers

| Tier | Source | Typical residual | Role |
|---|---|---|---|
| `hardware_ptp` | IEEE-1588 / hardware timestamping in a shared clock domain | sub-microsecond | Best, when wired. |
| `shared_event` | A physical event observed cross-modally (tap impulse in tactile/IMU, flash in video) | millisecond-class, robust | **Primary path** for wireless/body-worn devices, per the CTO Review. |
| `wall_clock` | NTP / host clock | milliseconds or worse | Coarse fallback only; never primary for high-rate streams. |

**Detectors are edge plugins.** A detector reads a stream's payload (it is allowed to, being modality-specific) and emits SyncEvents. The core consumes only SyncEvents and never the payload — preserving the modality-neutrality rule.

---

## 8. Confidence and Validity Rules

SentrixSync distinguishes three confidence concepts; downstream consumers must not conflate them.

| Concept | Range | Produced by | Meaning |
|---|---|---|---|
| **Sample confidence** | [0,1] | Producer (optional) | Trust in an individual raw sample. |
| **Clock confidence** | [0,1] | SentrixSync estimator | Trust in a device's fitted clock model, derived from fit residual, number/quality of matched events, and geometric conditioning. |
| **Interpolation confidence** | [0,1] | SentrixSync timeline | Trust in a resampled value on the grid, decaying with the gap to the nearest real sample. |

**Validity rules:**

- A grid value is **valid** only if it was produced from real observed samples within tolerance. A value held or interpolated across a gap exceeding the rejection tolerance `tau` is marked **invalid** (gap-flagged), never silently produced.
- Producer-asserted `valid=false` samples are excluded from both estimation and join, and are counted in dropout statistics.
- Confidence values are **carried, never thresholded inside the core** beyond what is needed to flag gaps. Release/certified thresholding happens in the metrics/gates stage, where it is explicit and reportable.

---

## 9. How Synthetic and Real Devices Must Expose Data

Both expose data through the **same** contract. The only differences are at the edges:

| Aspect | Synthetic device | Real device |
|---|---|---|
| `is_synthetic` | `true` | `false` |
| Timestamps | Produced by the forward-corruption model (offset/drift/jitter/delay/loss applied to ideal samples) | Measured from the real device/transport |
| Sync evidence | Simulated events / simulated PTP exchanges, tagged with the same tiers | Real detected events / real PTP exchanges |
| **Ground-truth clock** | MAY be supplied as a separate, clearly-labelled artifact for validation only (see below) | Not available |
| Estimation, correction, timeline, metrics | **Identical** | **Identical** |

**Ground-truth clock disclosure (synthetic only).** A synthetic adapter MAY attach the true clock relationship it used, in a clearly-segregated `ground_truth` block referenced from the Session — **never** inside Sample records and **never** visible to the estimator. The metrics stage may read it to compute round-trip accuracy. This separation is mandatory: leaking ground truth into the estimation path would invalidate every accuracy number.

This is the mechanism that satisfies *"support both synthetic and real without architectural change"*: synthetic and real differ only in where timestamps and evidence originate, not in how they are processed.

---

## 10. Versioning Strategy

- **Semantic versioning** of the contract: `MAJOR.MINOR.PATCH`.
  - **PATCH** — clarifications, no shape change.
  - **MINOR** — additive optional fields; older adapters remain valid.
  - **MAJOR** — any required-field change, removal, rename, or semantic change; breaks compatibility.
- Every DeviceDescriptor and Session manifest carries a `contract_version`.
- The core declares a **supported contract range** and rejects, with a clear message, descriptors outside it — never best-effort guessing.
- **Stability commitment:** the contract is the single most stable surface in the ecosystem. The bar for a MAJOR bump is deliberately high. New modalities and evidence sources are expected to be handled by adapters/detectors under the *current* contract; needing a contract change to add a modality is a signal that the change is wrong.
- The contract evolves independently of (and more slowly than) the internal module versions.

---

## 11. Summary

An adapter is contract-conformant if it: registers a DeviceDescriptor (one clock domain) with at least one StreamDescriptor; emits Samples with device-local integer-microsecond timestamps it did not pre-correct or re-grid; declares an honest kernel, rate, units, and payload kind; carries payloads by reference; supplies whatever sync evidence its tiers allow; and, if synthetic, segregates any ground-truth clock for validation only. Nothing in that list mentions a modality — which is the point.
