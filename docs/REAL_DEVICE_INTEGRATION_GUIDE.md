# Real Device Synchronization Integration Guide

**Status:** Permanent architecture document. Audience: contributors, researchers,
and engineers integrating new devices (real or synthetic) into SentrixSync.
**Companions:** [`ARCHITECTURE.md`](./ARCHITECTURE.md) · [`CONTRACT.md`](./CONTRACT.md) ·
[`SESSION_SCHEMA.md`](./SESSION_SCHEMA.md) · [`REFERENCE_CLOCK_DECISION.md`](./REFERENCE_CLOCK_DECISION.md) ·
[`IMPLEMENTATION_NOTES.md`](./IMPLEMENTATION_NOTES.md) · [`SYNTHETIC_ACCURACY_BUDGET.md`](./SYNTHETIC_ACCURACY_BUDGET.md)

---

## 0. The One Idea You Must Internalize First

> **SentrixSync does not synchronize raw sensor data. It synchronizes _clocks_,
> using _shared events_. Once the clocks are aligned, every sample from every
> device inherits that alignment automatically.**

This is the entire architecture in one sentence. Everything below is an
elaboration of it. The synchronization **core** — clock estimation, the
reconciliation graph, the timeline builder, the confidence model — never sees a
pixel, a taxel, a force value, or an audio sample. It sees only:

```
clocks · timestamps · events · confidence · validity · clock relationships
```

A "device" is **one clock domain** that emits **timestamped samples** (carried
**by reference**, never by value into the core) and, optionally, **events**
(things that happened at a known local time, detectable in more than one device).

### Why "align clocks via events" beats "align data directly"

Two naive approaches and why they fail:

| Approach | What it tries | Why it breaks |
|---|---|---|
| **Frame-to-frame matching** | Pair camera frame _k_ with the "matching" frame on another device | Devices have different frame rates, phase, and drift; there is no 1:1 correspondence, and the mapping changes over time. |
| **Sample-to-sample matching** | Pair glove sample _i_ with camera sample _j_ | Rates differ by 10–100×; one stream has no sample at the other's instants; dropped samples destroy the index correspondence permanently. |
| **Modality-specific sync logic** | A bespoke "glove↔camera" aligner; another for "audio↔IMU"; … | O(N²) bespoke aligners; every new modality touches existing code; nothing is reusable; the "core" becomes a pile of special cases. |

The event-based approach sidesteps all three:

```
        a few shared EVENTS                  one CLOCK MODEL per device
  (sparse, robust, modality-agnostic)   (continuous, applies to ALL samples)
                 │                                      │
   detect ──► associate ──► estimate ──► reconcile ──► timeline
```

You only need a **handful of events** to fit a device's clock model
`t_ref = α·t_local + β`. That model then maps **every** sample of that device —
billions of them — into reference time with zero per-sample matching. Sample
rate, phase, drops, and buffering become irrelevant to *correspondence*: they
only affect *coverage*, which the timeline reports honestly via validity masks.

---

## 1. Why Event-Based Synchronization Exists

Real capture rigs violate every convenient assumption:

- **Different frequencies.** A tactile glove may run 1000–1600 Hz; a webcam 30 fps;
  an IMU 800 Hz; mocap 120 Hz; a microphone 48 kHz. There is no common tick.
- **Latency.** USB host scheduling, driver buffering, and OS jitter delay arrival
  by milliseconds that vary sample to sample.
- **USB buffering.** Frames/samples arrive in bursts; *arrival* time ≠ *capture*
  time. (The contract separates these: `t_device_us` is the device's own capture
  clock; optional `t_recv_us` is arrival.)
- **Frame drops / packet loss.** A dropped frame permanently shifts any
  index-based correspondence; a lost packet leaves a gap.
- **Independent clocks with offset and drift.** Each device powers on at its own
  epoch and its crystal runs at a slightly different rate (ppm-level skew that
  accumulates into milliseconds over a session).

Directly aligning **glove sample ↔ camera frame** is therefore fundamentally
fragile: the "correct" pairing is undefined (no shared instants), unstable
(drift), and brittle (one drop desynchronizes the index forever).

**The design philosophy instead:**

```
events  →  clock estimation  →  timeline reconciliation
```

- **Events** are discrete, physically real instants (a tap, a flash) that *more
  than one device can observe*. They are sparse and robust — you need only a few.
- **Clock estimation** turns matched event observations into a continuous
  `t_ref = α·t_local + β` relationship per device.
- **Timeline reconciliation** expresses every device's continuous sample stream
  on one reference timeline using that relationship — an *as-of join*, not a
  match.

Events are the scaffolding; the clock model is the bridge; the samples cross the
bridge for free.

---

## 2. Real Hardware Example — Two Devices, Treated Identically

### Device A — Tactile glove
Possible sources: **Arduino, ESP32, RP2040, STM32, Teensy, a custom MCU, or the
SentrixCapture real-hardware glove producer.**
Data stream (device-local clock):

```
t_device_us , sensor_values[...]     # e.g. 21 taxels, or 5 fingertip pressures
```

> **SentrixCapture** is the real-hardware counterpart to the SentrixSim simulator:
> it emits the *same* Parquet artifact contract (a device-local timestamp column +
> `sensor_id`-keyed payload columns + self-describing metadata). The shipped
> `SentrixSimAdapter` reads it identically — pass `ts_column="t_capture_us"`
> (versus `"t_master_us"` for SentrixSim). Opaque hardware-revision provenance
> (`topology_ref`, e.g. `Mark2_v1`, and `topology_hash`) is auto-carried from the
> file's metadata into the DeviceDescriptor and Session manifest; it is **never
> consumed by synchronization** (CONTRACT.md §3).

### Device B — Video camera
Possible sources: **USB webcam, smartphone, action cam, industrial camera, RGB or
RGB-D camera.**
Data stream (device-local clock):

```
t_device_us , frame_ref              # frame referenced, NOT inlined
```

### Why the system treats them identically — once events are extracted

Each device is wrapped by an **adapter** that presents the *same* shapes to the
system (per `CONTRACT.md`): a `DeviceDescriptor` (one clock), `StreamDescriptor`s,
and `Sample`s with `t_device_us` + a `payload_ref`. The bulky payloads (taxel
arrays, video frames) are **referenced** (`file://…`, `mcap://…`, `parquet://…`,
`memory://…`), never pushed into the core.

A **detector** (modality-specific, but living at the edge) turns each device's
signal into `SyncEvent`s — `{device_id: t_local_us}` observations of shared
fiducials. From that point on, the glove and the camera are **indistinguishable**
to the synchronization core: both are just nodes that observed some events at
some local times. The tactile glove is not "special"; the camera is not
"special". They are two clocks linked by shared events.

```
   GLOVE (MCU)                        CAMERA (USB)
   taxel arrays @1000Hz               frames @30fps
        │                                  │
   [glove adapter]                    [camera adapter]
        │  Samples(t_device_us, ref)       │  Samples(t_device_us, ref)
        │                                  │
   [tap detector]                     [flash/contact detector]
        │  Tap @ t_local                   │  FingerContact @ t_local
        └──────────────┬───────────────────┘
                       ▼
              SyncEvents  {glove: t1, camera: t2}      ← core sees only this
                       ▼
        association → estimation → graph → timeline
```

---

## 3. End-to-End Synchronization Pipeline

```
 Raw Device → Adapter → Detector → Event Stream → Association
            → Clock Estimation → Graph Reconciliation → Unified Timeline
```

| Stage | Module (today) | Responsibility | Modality-aware? |
|---|---|---|---|
| **Raw Device** | (hardware / generator) | Produce timestamped samples in its own clock | — |
| **Adapter** | `ingest/adapter.py` (`DeviceAdapter`) | Present the device as `DeviceDescriptor` + `Sample`s; timestamps device-local, payloads by reference | thin (knows the device wire format) |
| **Detector** | `detect/detector.py` (`SyncEventDetector`) | Turn a stream's signal into local **event times** | **yes — the only place modality logic lives** |
| **Event Stream** | `core/events.py` (`SyncEvent`) | `{device_id → t_local_us}` per fiducial | no |
| **Association** | `detect/matcher.py` (`associate_detections`) | Group cross-device detections of the *same* fiducial into events | no (uses only times + coarse clock) |
| **Clock Estimation** | `clock/estimate.py` (`tls_affine`, `ransac_affine`, `fit_piecewise_affine`) | Fit `t_ref = α·t_local + β` per device pair | no |
| **Graph Reconciliation** | `sync/graph.py` (`reconcile`) | Compose pairwise fits into per-device → reference models over a spanning tree | no |
| **Unified Timeline** | `sync/timeline.py` + `sync/join.py` (`TimelineBuilder`, `asof_join`) | Build the reference grid; as-of join every stream with validity + confidence | no |

### Stage responsibilities, with examples

**Adapter** — translates a producer into the contract. It must:
- describe **one clock domain** (`DeviceDescriptor.clock`);
- emit `Sample(t_device_us=…, payload_ref="…")` in the device's *own* clock,
  **not pre-corrected** and **not re-gridded** (the estimator needs the real,
  jittered timestamps);
- carry payloads **by reference**.

```python
# Pseudocode — a real MCU tactile adapter (conforms to ingest/adapter.py)
class Mark2GloveAdapter(DeviceAdapter):
    def descriptor(self):
        return DeviceDescriptor(
            device_id="glove_L", modality="tactile", producer="mark2",
            is_synthetic=False, reference_candidate=True,
            clock=ClockDescriptor(clock_id="glove_L_hub",
                                  timestamp_unit="microseconds", resolution_us=1),
            evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
            streams=[StreamDescriptor("tactile", "glove_L", kind="tactile_field",
                        kernel=Kernel.CONTINUOUS, payload_kind="taxel_uri",
                        units="raw", nominal_rate_hz=1000.0)])
    def read(self, stream_id):
        line = self._serial.readline()              # "t_us,v0,v1,...,vN"
        if not line: return None
        t_us, *vals = parse(line)
        ref = self._store_frame(t_us, vals)         # -> "mcap://sess/glove#row=K"
        return Sample(stream_id="tactile", t_device_us=t_us, payload_ref=ref)
```

**Detector** — the modality boundary. It reads a *resolved* signal (taxel series,
luminance series, audio envelope, …) and returns **local event times**. The core
never calls it directly with payloads; integrators run detectors and hand the
resulting events to the core.

**Association** — clusters per-device detections of the same physical fiducial
into `SyncEvent`s using a coarse common time (wall-clock/NTP, ms-class) for
pre-alignment. Subset-aware: devices may observe different, partially-overlapping
event subsets.

**Estimation + Graph + Timeline** — pure clock/timestamp math; see §8 and §10.

---

## 4. Tactile Event Detection

**Raw tactile samples are NOT synchronization evidence. Detected events ARE.**

A stream of 1 kHz taxel readings is just data; it carries no cross-device anchor
by itself. A *detector* condenses it into discrete, physically-meaningful instants
that another modality can also witness:

```
TouchStart · TouchEnd · GripStart · GripEnd · PressureSpike · Tap · Release · FingerContact
```

Each detected instant becomes a local event time `t_local_us` on the glove's
clock. A detector is a small plugin (`SyncEventDetector`) returning a
`Detection(times_us=[...])`:

```python
@register_detector
class TapDetector(SyncEventDetector):
    name = "tactile_tap"; tier = EvidenceTier.SHARED_EVENT
    def detect(self, t_us, signal):                 # signal = aggregate force series
        return Detection(times_us=find_impulse_peaks(t_us, signal, self.threshold))
```

- **Real tactile hardware:** resolve the glove's taxel payloads to a 1-D
  contact-force series, threshold/peak-pick → `Tap`/`TouchStart` local times.
- **Synthetic tactile generator (SentrixSim):** the generator already produces
  scripted contact profiles; the *same* `tactile_tap` detector runs on its
  signal and yields the same event type. The core cannot tell the difference —
  that is the point (see §6).

The detector is the **only** component that knows "this is tactile". Swap it for a
different modality's detector and nothing downstream changes.

---

## 5. Vision Event Detection

**Video frames are NOT synchronization evidence. Detected events ARE.**

A video stream is a sequence of referenced frames. A vision detector turns it into
the *same kind* of local event times the tactile detector produces:

```
ObjectContact · FingerContact · TouchDetected · HandApproach · HandDeparture · LEDFlashDetected · MotionPeak
```

```python
@register_detector
class FlashDetector(SyncEventDetector):
    name = "visual_flash"; tier = EvidenceTier.SHARED_EVENT
    def detect(self, t_us, signal):                 # signal = per-frame mean luminance
        return Detection(times_us=find_impulse_peaks(t_us, signal, self.threshold))
```

The vision detector reads a *scalar feature series* derived from frames (mean
luminance for a flash; hand-region motion energy for a contact). It emits
`LEDFlashDetected` / `FingerContact` at frame-resolution local times on the
camera's clock. Note the symmetry with §4: tactile and vision detectors have
identical signatures and emit identical `SyncEvent`s. The synchronization core
receives `{glove: t1, camera: t2}` and neither knows nor cares that `t1` came
from a taxel spike and `t2` from a luminance spike.

---

## 6. Synthetic Tactile Generator Integration

A first-class architectural guarantee: **real tactile devices and synthetic
tactile generators enter the system through the _same contract_, with _zero_
changes to the synchronization core.**

```
   REAL tactile device ─┐
                        ├─► same DeviceAdapter contract ─► same Detector ─► same SyncEvents ─► same core
   SYNTHETIC generator ─┘
```

The synchronization core's inputs are `DeviceDescriptor`, `Sample`
(`t_device_us`, `payload_ref`), and `SyncEvent`. A synthetic generator produces
exactly these — the only differences are confined to the edges and are explicitly
declared:

- `DeviceDescriptor.is_synthetic = True`;
- timestamps come from a forward clock-corruption model (offset/drift/jitter/loss)
  instead of measured hardware;
- an optional, **segregated** `ground_truth` block carries the *true* injected
  clock so accuracy can be measured — it is **never** visible to the estimator
  (`CONTRACT.md §9`).

What synthetic generators let you do **before any hardware exists**:

| Use | How |
|---|---|
| **Validate detectors** | Generate a signal with known event times; assert the detector recovers them. |
| **Validate association** | Feed partially-overlapping detections from N synthetic devices; assert the right subsets cluster. |
| **Validate estimators** | Inject known `(α, β)`; assert TLS/RANSAC recover them within the accuracy budget. |
| **Stress-test synchronization** | Inject false positives/negatives, duplicates, coarse-clock error, packet/burst loss; verify graceful degradation. |
| **Generate controlled ground-truth events** | Place fiducials at exact reference times to drive end-to-end round-trip accuracy tests. |

This is why the project reached a robust, graph-based, multimodal synchronizer
*entirely in simulation*: the contract makes "synthetic" and "real" the same
shape, so every line of the core is exercised before a wire is soldered. The
existing `scenarios/` module (synthetic 2-device, multimodal N-device, and
robustness scenarios) is precisely this generator path.

---

## 7. First Real-World Validation Experiment — LED + MCU + Camera

The simplest possible cross-modal synchronization proof. It establishes a single
shared event channel between two independent clocks.

```
   ┌────────── MCU (Arduino/ESP32/…) ──────────┐         ┌──── Camera ────┐
   │  loop:                                     │  light  │  records frames │
   │   t = micros()                             │ ───────►│  @ 30–120 fps   │
   │   log "FLASH t" over serial                │  flash  │                 │
   │   pulse LED HIGH (brief)                    │         │                 │
   └────────────────────────────────────────────┘         └────────────────┘
```

**Procedure**

1. **MCU records timestamp** `t_mcu` (`micros()` / `esp_timer_get_time()`), logs
   it, and immediately
2. **triggers an LED flash**.
3. **Camera captures the flash** in some frame at camera-clock time `t_cam`.
4. **Detector extracts the flash event** from the camera's luminance series
   (`visual_flash` → `LEDFlashDetected @ t_cam`). The MCU's logged `t_mcu` is the
   MCU-side observation (no detector needed — the MCU *is* the event source; its
   adapter emits the event directly).
5. **Synchronization estimates the clock mapping.** Repeat the flash ~10–40 times
   spread over the session; associate the `(t_mcu, t_cam)` pairs; fit
   `t_cam = α·t_mcu + β`.

**Why this is the canonical proof:** one unambiguous event, observable by both
clocks, with the MCU acting as `reference_candidate`. It isolates the clock-
relationship machinery from every other concern (no tactile physics, no hand
tracking, no payloads of interest). If this works, the architecture works; every
later modality is "another way to produce events".

**Expected outputs**

```
clock_models["camera"] = ClockModel(alpha≈1.00000±ppm, beta_us≈<board offset>,
                                    fit_residual_us≈ camera frame period / 2,
                                    clock_confidence≈0.9+)
sync_report.sync_resid_us ≈ half a camera frame interval (e.g. ~16 ms @30fps,
                            ~4 ms @120fps) — the detection-resolution floor
gate_verdict ∈ {release, needs_review}   # camera frame period dominates residual
```

The residual floor here is the **camera frame interval**, because flash detection
can only localize to the frame that saw it. Faster cameras or sub-frame flash
localization tighten it. This immediately teaches the operating limit of
event-based sync for a given camera.

---

## 8. Synchronizing a Tactile Glove With Video

**Scenario:** the user taps a table **five times**.

```
   physical taps:     ▮      ▮       ▮     ▮        ▮      (real-world instants)
   glove (1000 Hz):   Tap    Tap     Tap   Tap      Tap    → t_glove = [g0..g4]
   camera (30 fps):   FC     FC      FC    FC       FC     → t_cam   = [c0..c4]
                      (FingerContact, frame-resolution)
```

**Step 1 — detect (per device, modality-specific):**

```python
glove_taps = TapDetector().detect(glove_t, glove_force).times_us       # [g0..g4]
cam_contacts = ContactDetector().detect(cam_t, cam_motion).times_us    # [c0..c4]
```

**Step 2 — associate (modality-neutral):**

```python
events = associate_detections(
    {"glove_L": glove_taps, "ego_cam": cam_contacts},
    tier=EvidenceTier.SHARED_EVENT,
    association_tolerance_us=20_000,                # > coarse-clock error, < tap spacing
    coarse_clocks={"glove_L": (1,0), "ego_cam": (1, ntp_offset_us)})
# -> 5 SyncEvents, each {"glove_L": g_k, "ego_cam": c_k}
```

**Step 3 — estimate + reconcile (pure clock math):**

```python
result = synchronize(
    reference_device_id="glove_L",
    descriptors={"glove_L": glove_desc, "ego_cam": cam_desc},
    stream_timestamps={("glove_L","tactile"): glove_all_sample_times,
                       ("ego_cam","image"):   cam_all_frame_times},
    sync_events=events, grid_rate_hz=1000, rejection_tolerance_us=20_000,
    robust_estimation=True)                          # RANSAC, in case a tap was mis-detected
cam_model = result.clock_models["ego_cam"]           # t_glove_ref = α·t_cam + β
```

**Step 4 — every glove sample inherits the alignment, for free:**

Because the camera's clock model is known, any timestamp on either device maps to
the reference timeline with a single affine evaluation — no per-sample matching:

```python
# put a camera frame's time into glove-reference time:
t_ref = cam_model.to_reference(frame_t_cam)
# the timeline builder has already done this for ALL samples of ALL streams:
#   result.timeline.per_stream[("ego_cam::image")]  -> grid alignment + validity
#   result.timeline.per_stream[("glove_L::tactile")]
```

The **5 events** produced a clock model; that model aligns **all** of the glove's
thousands of samples and all of the camera's frames. Tactile samples that fall
between camera frames are not "dropped" — they live on the high-rate reference
grid with full validity; the camera stream is the one that gets held/interpolated
(its `kernel` says how). This is the payoff of clock-level synchronization.

---

## 9. Multi-Hardware Compatibility (the load-bearing section)

**The architecture is hardware-agnostic. Adding hardware = an Adapter (always)
+ a Detector (only if the device must contribute events).**

```
                         ┌───────────────────────────────────────────────┐
   ANY device  ──────►   │  Adapter  →  (Detector)  →  SyncEvents/Samples │  ──► CORE (unchanged)
                         └───────────────────────────────────────────────┘
        the ONLY parts you write per device      contract · graph · estimation · confidence
                                                  NEVER change
```

Supported sources (illustrative, not exhaustive):

```
 Microcontrollers : Arduino · ESP32 · RP2040 · STM32 · Teensy · custom boards
 Vision           : USB webcam · smartphone · industrial camera · RGB-D camera
 Other modalities : IMU · microphone / mic array · force/torque · tactile array ·
                    motion capture · depth sensor · eye tracker
```

### Why new hardware never touches the core

```
   +-----------------------------------------------------------+
   |                    SYNCHRONIZATION CORE                    |
   |   (clocks · timestamps · events · confidence · validity)   |
   |   contract  ·  associate  ·  estimate  ·  graph  ·  timeline|
   |                  ── FROZEN per device ──                   |
   +------------------------------▲----------------------------+
                                  │  stable contract (DeviceDescriptor,
                                  │  Sample, SyncEvent — see CONTRACT.md)
        ┌──────────┬──────────────┼──────────────┬──────────────┐
     [Adapter]  [Adapter]      [Adapter]      [Adapter]      [Adapter]
     +Detector  +Detector       (no det.)     +Detector       +Detector
       glove     camera           IMU*          mic            mocap
```

Adding a device is an **edge change**:

1. Write an **Adapter** — present its clock + timestamped samples (payload by
   reference). This is the only mandatory piece.
2. Write a **Detector** *iff* the device should contribute synchronization events
   (most do; a device that only carries hardware-PTP evidence, or that piggybacks
   on another device's events, may not need one). `*`A device that observes no
   shared event is simply *unreachable* and reported as such — never an error.

It does **not** require modifying: the **contract**, **graph synchronization**,
**clock estimation**, or the **confidence system**. This is guaranteed by the
modality-neutrality rule (`CONTRACT.md §2`): the core branches only on the
declared `kernel`, `nominal_rate`, `units`, and `payload_kind` — never on what the
data *means*. (A grep of the core confirms zero modality references.)

---

## 10. Multi-Device Future Vision

The exact same backbone scales — with no architectural change — to a rich rig:

```
   Camera ── Depth ── Tactile Glove ── IMU ── Microphone ── Force ── Eye Tracker
      \        \           │           /          /          /         /
       \        \          │          /          /          /         /
        ╲        ╲         │         ╱          ╱          ╱         ╱
                 SHARED EVENTS form a GRAPH (each edge = a co-observed fiducial)
                                   │
                          graph reconciliation
                                   │
                            ONE reference timeline
```

Every modality, however exotic, is reduced by its detector to the universal
triple:

```
   (timestamp, event, confidence)
```

and therefore becomes just another **node** in the synchronization **graph**.
Devices that share events form **edges**; the graph need not be complete — a
device that shares events only with an intermediate is reconciled **transitively**
along a spanning path (already implemented and validated: a 5-device scenario
where the reference observes *none* of the camera's events still reconciles the
camera through an IMU at 2 hops). Confidence degrades gracefully with hop count,
so the system tells you how much to trust each transitive alignment.

The reference clock is chosen by the **designated-anchor** policy
(`REFERENCE_CLOCK_DECISION.md`); a hardware-PTP grandmaster, when present, is
simply the highest-tier anchor and uses the same machinery. No modality is
privileged; the highest-rate / most-trusted clock anchors the timeline.

---

## 11. Failure Modes in Real Hardware

| Failure mode | Status | How it is handled (or what remains) |
|---|---|---|
| **Dropped frames** | ✅ handled | Reduce a device's event count and stream coverage; the as-of join flags gaps (validity mask), it does not fabricate. |
| **Packet loss** | ✅ handled | Modeled (Bernoulli) and tested; surfaces as dropout/coverage; estimation uses surviving events. |
| **Burst loss** | ✅ handled | Gilbert burst model tested; wider coverage gaps reported, gate trips to `needs_review`. |
| **Duplicated events** | ✅ handled | Association keeps one detection per device per fiducial (nearest-to-centroid). |
| **Missed detections** | ✅ handled | Subset-aware association + graph: fewer edges, transitive paths; a device with no shared events is reported *unreachable*, not fatal. |
| **False-positive detections** | ✅ handled | RANSAC rejects outlier event *pairs*; a **minimum-support** threshold + confidence-weighted paths reject spurious few-coincidence *edges* (the key finding of the robustness milestone). |
| **Coarse-clock (NTP) error** | ✅ characterized | Operating limit quantified: full reconciliation while wall-clock error stays well below the association tolerance; beyond it, association fragments (devices unreachable). Pick `association_tolerance_us` accordingly. |
| **Clock drift (linear)** | ✅ handled | Affine `α` captures skew; fit over events spanning the session. |
| **Nonlinear drift (thermal), long sessions** | ⚠️ partial | Optional **piecewise** affine improves long sessions (demonstrated); graph composition of piecewise edges is future work. |
| **Buffering latency (capture vs arrival)** | ⚠️ partial | Contract separates `t_device_us` (capture) from `t_recv_us` (arrival); using capture time avoids the issue, but arrival-only devices need a latency model (future). |
| **Device restart / clock reset / wrap mid-session** | ❌ future | An epoch jump breaks the affine model; detection + re-anchoring (segmenting the session at the reset) is not yet implemented. Session-scope (`C8`) mitigates by bounding sessions. |
| **Very long / high-rate / many-stream scale** | ❌ future | The dense union grid is in-memory; chunked/streamed timeline construction is future work. |
| **Real-payload detectors + payload resolvers** | ❌ future | Detectors run on resolved signals; the `file://…/parquet://…/mcap://…` resolver layer (turning a `payload_ref` into a signal a detector can read) is the main missing piece for live hardware. |

The honest summary: **detection/association/estimation/topology failures are
handled or characterized; clock-reset, scale, and the payload-resolver layer
remain before live multi-device capture.**

---

## 12. Recommended Hardware Integration Roadmap

| Phase | Goal | Success criteria | Expected outputs |
|---|---|---|---|
| **1 — LED sync validation** | Prove one shared-event channel between an MCU and a camera (§7) | Recovered `α≈1`, stable `β`; `sync_resid_us` ≈ half the camera frame period; verdict release/needs_review | A `ClockModel` for the camera; a residual equal to the camera's detection-resolution floor |
| **2 — Tactile glove + video** | Real cross-modal sync via taps/contacts (§8) | Tap↔contact association recall high; glove samples map into camera time; residual near the camera frame floor | A 2-node timeline; all glove samples expressed in reference time; per-stream validity/confidence |
| **3 — Multiple cameras** | Several cameras + the glove; some cameras share events only with each other | All cameras reachable (directly or transitively); consistent reference times across cameras within tolerance | A multi-node graph; per-camera `ClockModel`s; hop/topology diagnostics |
| **4 — Additional sensors** | Add IMU, microphone, force/torque, depth — each via Adapter (+Detector) | Each new device reachable with no core change; confidence reflects hop distance and event density | A growing graph; per-device confidence; gate verdicts per device |
| **5 — Large multimodal graph** | Full rig: camera · depth · glove · IMU · mic · force · eye tracker | Whole graph reconciled on one timeline; graceful degradation under real drops/drift; certified where evidence is strong | One unified, confidence-annotated reference timeline feeding downstream consumers |

**Cross-phase rule:** at *every* phase the only new code is **adapters and
detectors**. If a phase tempts you to modify the contract, the graph, the
estimator, or the confidence model to accommodate a specific device, stop — that
is a signal the change belongs in an adapter/detector, or that the contract needs
a *general* (not modality-specific) extension reviewed on its own merits.

---

## Appendix A — Minimal Integration Checklist (per new device)

1. **Adapter** (`DeviceAdapter` subclass): one clock domain; `descriptor()`;
   `read()/read_batch()` emitting `Sample(t_device_us, payload_ref)` in the
   device's own clock, not pre-corrected, not re-gridded; payloads by reference
   using a registered URI scheme (`file/mcap/parquet/memory`, extensible).
2. **Detector** (optional, `SyncEventDetector` subclass): consume the resolved
   signal, return local event times of a physically shared fiducial; register it
   with `@register_detector`.
3. **Coarse clock** (optional): provide an ms-class wall-clock estimate for
   association pre-alignment if the device's offset is large.
4. **Wire-up**: detect per device → `associate_detections(...)` →
   `synchronize(reference_device_id, descriptors, stream_timestamps, sync_events,
   …)`. Use `robust_estimation=True` for real (noisy) evidence; set
   `confidence_decay_tau_us` for long-gap honesty; raise `min_events` if false
   positives are expected.
5. **Validate first in simulation**: model the device as a synthetic generator
   with a known injected clock; confirm the detector/association/estimator recover
   it within the accuracy budget *before* trusting hardware.

## Appendix B — Glossary (core vocabulary only)

- **Device** — one clock domain.
- **Stream** — one channel of a device; declares a `kernel` (`continuous`→
  interpolate, `hold`→latest-at), nominal rate, units, payload kind.
- **Sample** — one timestamped record; `t_device_us` (device-local µs) +
  `payload_ref`/`payload_inline`.
- **SyncEvent** — a shared fiducial: `{device_id → t_local_us}` + evidence tier.
- **Evidence tier** — `hardware_ptp` | `shared_event` | `wall_clock`.
- **ClockModel** — `t_ref = α·t_local + β` (or piecewise `segments`).
- **Reconciliation graph** — devices = nodes, co-observed-event pairs = edges;
  spanning tree rooted at the reference yields each device's `ClockModel`.
- **Timeline** — the reference grid + per-stream as-of-join alignment, validity
  mask, and (source/clock/interpolation) confidence components.

---

*This document describes how to integrate devices. It deliberately says nothing
about what to do with the aligned data (catalog, labeling, export, perception) —
those are downstream Data-Engine concerns outside the synchronization backbone.
The backbone's single job is to make every device's clock agree, via shared
events, so that every sample lands on one trustworthy timeline.*
