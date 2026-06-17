# Synchronization Platform User Guide

**Audience:** engineers, researchers, and developers who want to *use* SentrixSync
with real devices, synthetic generators, or multimodal datasets.
**Scope:** hands-on operation — from raw recordings to a unified timeline. This is
not an architecture document; for the "why", see
[`ARCHITECTURE.md`](./ARCHITECTURE.md) and
[`REAL_DEVICE_INTEGRATION_GUIDE.md`](./REAL_DEVICE_INTEGRATION_GUIDE.md).

> **How you operate the platform:** SentrixSync is a **Python library**, not a
> CLI app. You drive it from short scripts (`python my_script.py`). Synthetic
> generators, detectors, association, estimation, the graph, and the timeline are
> all built in. For **real hardware**, you supply two small edge pieces — a
> *loader/adapter* (read your device into timestamped samples) and, if your
> device contributes events, a way to turn its signal into a 1-D array a detector
> can read. Everything after that is the platform.

Install once:

```bash
python -m venv .venv && . .venv/Scripts/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # dev extra adds pyarrow (for parquet inputs) + pytest
```

---

## 1. What the Platform Does

**Input** — any device that can produce *timestamped samples*, and optionally
*events*: tactile streams, camera recordings, IMU data, synthetic generators, and
future modalities.

**Output** — for a recording session:

| Output | What it is |
|---|---|
| **Clock models** | `t_ref = α·t_local + β` per device (or piecewise) |
| **Unified timeline** | every device's samples placed on one reference time grid |
| **Confidence estimates** | per-grid-point source / clock / interpolation confidence |
| **Device alignment report** | residual, coverage, dropout, gate verdict |
| **Synchronization graph** | which devices are linked by shared events; hops; unreachable devices |

### Simplest possible example (works today, no hardware)

```python
from sentrixsync.scenarios import build_multimodal_preset, run_multimodal_scenario

scen = build_multimodal_preset("mm_5device")     # 5 synthetic devices, shared events
result = run_multimodal_scenario(scen)

print("reference:", result.reference_device_id)
print("verdict:", result.validation_report.gate_verdict.value)
print("residual (us):", round(result.metrics["sync_resid_us"], 1))
for dev, m in result.clock_models.items():
    print(f"  {dev}: alpha={m.alpha:.6f} beta_us={m.beta_us:.0f} conf={m.clock_confidence:.2f}")
```

That single call ran the whole pipeline (detect → associate → estimate → graph →
timeline) and produced clock models, a timeline, confidence, and a report.

---

## 2. Core Workflow Overview

```
        Record Data
            ↓
   Create Device Adapters          (real hw: you write loaders;  synthetic: provided)
            ↓
      Run Detectors                (turn each device's signal into local event times)
            ↓
     Generate Events
            ↓
     Associate Events              associate_detections(...)
            ↓
     Estimate Clocks               TLS / RANSAC / piecewise
            ↓
 Build Synchronization Graph       reconcile(...)  (inside synchronize)
            ↓
 Generate Unified Timeline         synchronize(...) -> SyncResult.timeline
            ↓
   Analyze Synchronized Data       clock_models, confidence, reports
```

| Step | You call | Produces |
|---|---|---|
| Record | (your tools) | raw logs / video |
| Adapters | your loader → arrays | per-device `t_device_us` arrays + signal arrays |
| Detectors | `get_detector(name).detect(t, signal)` | local event times per device |
| Events | (detector output) | `Detection.times_us` |
| Associate | `associate_detections(...)` | `list[SyncEvent]` |
| Estimate + Graph + Timeline | `synchronize(...)` | `SyncResult` (all outputs) |
| Analyze | read `SyncResult` fields | clock models, timeline, confidence, reports |

Later sections expand each step.

---

## 3. Supported Input Sources

The platform consumes **timestamped numeric arrays** (and references to bulky
payloads). How you get there depends on the source.

### Real hardware (MCUs: Arduino, ESP32, RP2040, STM32, Teensy)

- **Data formats you bring:** Serial lines, CSV, JSON, or binary logs.
- **Requirement:** each record has a **device-local timestamp in integer
  microseconds** (`t_device_us`) plus sensor values. Use the MCU's own clock
  (`micros()`, `esp_timer_get_time()`), **not** the host's wall clock.
- You write a tiny loader that yields `(t_device_us, values)`.

### Cameras (MP4, webcam, smartphone, industrial)

- **Data formats:** MP4 / image sequences plus a per-frame timestamp source.
- **Requirement:** a per-frame `t_device_us` (camera clock or capture PTS) and a
  way to compute a 1-D feature series for detection (e.g. mean luminance for a
  flash, hand-region motion energy for contact).
- You write a loader (e.g. with OpenCV) that yields `(frame_t_us, feature_value)`.

### Synthetic sources (built in)

- **Tactile generators / simulated sensors / benchmark datasets** ship with the
  platform (`scenarios/`). They produce the same shapes as real devices and need
  **no loader**. Use them to learn the platform and to validate before hardware.

**Universal input requirements (all sources):**

1. Timestamps are **integer microseconds**, in the **device's own clock**, **not
   pre-corrected** to any reference, and **not re-sampled to a clean grid**.
2. Timestamps are **non-decreasing** per stream.
3. Bulky payloads (frames, taxel arrays) are **referenced**, not inlined.

---

## 4. Preparing Tactile Data

**Workflow:** `Arduino → Serial Logger → CSV → Platform`

**Example record format (CSV, one row per sample):**

```
t_device_us,f0,f1,f2,f3,f4         # microcontroller micros() + 5 fingertip forces
1000,12,3,0,0,1
2000,40,9,1,0,2
3000,210,80,4,0,9                  # a tap: sharp rise
...
```

**Step by step:**

```python
import numpy as np

# 1) Load your CSV (you write this small loader).
rows = np.loadtxt("glove.csv", delimiter=",", skiprows=1)
t_us   = rows[:, 0].astype(np.int64)      # device-local microseconds
forces = rows[:, 1:]                      # raw taxel/finger values

# 2) Reduce to a 1-D detection signal (sum/aggregate contact force).
signal = forces.sum(axis=1).astype(float)

# 3) Detect events (Section 7).
from sentrixsync.detect import get_detector
taps = get_detector("tactile_tap", threshold=150.0).detect(t_us, signal).times_us
print("glove tap times (us):", taps)
```

**Timestamp requirements:** integer µs from the MCU clock; monotonic; if your MCU
only gives milliseconds, multiply by 1000 and set the device's `resolution_us`
honestly (e.g. `1000`).

**Best practices:** log the timestamp *before* doing other work in the loop;
keep a fixed loop rate; include a few deliberate **synchronization gestures**
(firm taps) at the start, middle, and end so events span the session.

**Common mistakes:**

| Mistake | Consequence | Fix |
|---|---|---|
| Using host arrival time as `t_device_us` | bakes in USB/OS jitter | use the MCU clock |
| Pre-subtracting an offset | destroys the evidence the estimator needs | pass raw device time |
| Resampling to a clean grid | hides real jitter/drops | pass real timestamps |
| Float seconds | unit ambiguity | integer microseconds |

---

## 5. Preparing Video Data

**Workflow:** `Camera → MP4 → Video loader → Vision detector`

```python
import cv2, numpy as np                    # you provide this loader

cap = cv2.VideoCapture("session.mp4")
fps = cap.get(cv2.CAP_PROP_FPS)
t_list, lum = [], []
i = 0
while True:
    ok, frame = cap.read()
    if not ok: break
    # frame timestamp: prefer real PTS; fall back to index/fps if absent
    t_us = int(round(cap.get(cv2.CAP_PROP_POS_MSEC) * 1000)) or int(round(i / fps * 1e6))
    t_list.append(t_us)
    lum.append(float(frame.mean()))        # mean luminance -> flash feature
    i += 1
cam_t = np.array(t_list, dtype=np.int64)
cam_signal = np.array(lum, dtype=float)

from sentrixsync.detect import get_detector
flashes = get_detector("visual_flash", threshold= lum_threshold ).detect(cam_t, cam_signal).times_us
```

**Considerations:**

- **Frame timestamps:** use the camera's PTS when available; it is the camera
  clock. Index/fps is acceptable only for constant-rate cameras.
- **Frame rate:** the residual floor is roughly **half a frame interval**
  (≈16 ms @30fps, ≈4 ms @120fps). Faster cameras → tighter sync.
- **Variable frame rate (VFR):** common with phones — **always** use real PTS,
  never index/fps, or your camera "clock" will be wrong. Keep the timestamps you
  actually read; do not assume uniform spacing.
- **Long recordings:** clock drift accumulates; ensure events span the *whole*
  recording (not just the start) so skew is observable.

**Best practices:** include a bright, brief, well-separated flash or a clear
hand-contact a handful of times; avoid two events within your association
tolerance of each other.

---

## 6. Creating Device Adapters

**What an adapter is:** a thin object that presents your device to the platform
in the contract shapes — one **clock domain**, a **descriptor**, and
**timestamped samples** (payload by reference). Adapters are the *only* code you
write per device.

**Responsibilities:** read data; expose device-local timestamps; produce
contract-compatible samples. (For synchronization you mainly need the **timestamp
arrays** and a **signal array** for the detector; a full `DeviceAdapter` is for
streaming/manifest workflows.)

### Tactile hardware adapter (sketch)

```python
from sentrixsync.ingest import DeviceAdapter
from sentrixsync.core import (DeviceDescriptor, StreamDescriptor, ClockDescriptor,
                              Sample, EvidenceTier, Kernel)

class GloveCSVAdapter(DeviceAdapter):
    def __init__(self, csv_path): self.rows = load_csv(csv_path); self.i = 0
    def descriptor(self):
        return DeviceDescriptor(
            device_id="glove", modality="tactile", producer="arduino",
            is_synthetic=False, reference_candidate=True,
            clock=ClockDescriptor(clock_id="glove_clk", resolution_us=1),
            evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
            streams=[StreamDescriptor("tactile","glove", kind="tactile_field",
                       kernel=Kernel.CONTINUOUS, payload_kind="csv_row",
                       units="raw", nominal_rate_hz=1000.0)])
    def open(self): self.i = 0
    def close(self): pass
    def read(self, stream_id):
        if self.i >= len(self.rows): return None
        t, vals = self.rows[self.i]; self.i += 1
        return Sample("tactile", t_device_us=int(t), payload_ref=f"file://glove.csv#row={self.i-1}")
```

### Video adapter
Same pattern: `modality="rgb"`, one stream with `kernel=Kernel.HOLD`, payload
`payload_ref="file://session.mp4#frame=K"`.

### Synthetic generator
No adapter needed — `scenarios/` builds descriptors, sample times, and signals for
you (Section 13).

**Registration workflow:** adapters are plain classes — instantiate and use them.
**Detectors** are what you register (so they're findable by name):

```python
from sentrixsync.detect import register_detector, SyncEventDetector, find_impulse_peaks, Detection
from sentrixsync.core import EvidenceTier

@register_detector
class GripDetector(SyncEventDetector):
    name = "grip_start"; tier = EvidenceTier.SHARED_EVENT
    def __init__(self, threshold=100.0): self.threshold = threshold
    def detect(self, t_us, signal):
        return Detection(times_us=find_impulse_peaks(t_us, signal, self.threshold))
```

---

## 7. Running Detectors

A detector turns a device's **1-D signal** into **local event times**. Built-in
detectors: `tactile_tap`, `visual_flash` (both impulse-style). You add your own
(Section 6).

```python
from sentrixsync.detect import get_detector, registered_detectors
print(registered_detectors())                       # ['tactile_tap', 'visual_flash', ...]

det = get_detector("tactile_tap", threshold=150.0)
detection = det.detect(t_us, signal)
print("event times (us):", detection.times_us)      # e.g. [302145, 905880, ...]
```

| Modality | Example detectors | Signal you feed it |
|---|---|---|
| Tactile | touch / grip / spike (`tactile_tap`) | aggregate contact force series |
| Vision | contact / flash / motion (`visual_flash`) | mean luminance or motion-energy series |
| Synthetic | ground-truth event generator | provided by the generator |

**Outputs & confidence:** `Detection.times_us` are the local event times; an
optional `Detection.confidences` may accompany them. Detector confidence is about
*this detection*; it is distinct from **clock confidence** (trust in the fitted
model) and **interpolation confidence** (trust in a resampled grid value). The
platform keeps these three separate and never collapses them.

---

## 8. Event Inspection

**Always inspect events before synchronizing.** Bad events → bad clocks.

```python
from sentrixsync.detect import associate_detections
from sentrixsync.core import EvidenceTier

detections = {"glove": glove_taps, "camera": cam_flashes}   # local times per device
events = associate_detections(detections, tier=EvidenceTier.SHARED_EVENT,
                              association_tolerance_us=20_000,
                              coarse_clocks={"glove": (1,0), "camera": (1, ntp_offset_us)})

# Event table
for e in events:
    print(e.event_id, {d: t for d, t in e.observations.items()})
```

Example **event table**:

```
evt_0 {'glove': 302145, 'camera': 21340000}
evt_1 {'glove': 905880, 'camera': 21943000}
evt_2 {'glove': 1502300, 'camera': 22540000}      # consistent spacing -> good
```

**Verifying detector quality before sync:**

- **Count check:** does each device have roughly the expected number of events?
  (e.g. 5 taps → ~5 events.) Far too many → false positives (lower sensitivity);
  far too few → missed detections (raise sensitivity).
- **Spacing check:** event spacing should match your gestures (e.g. ~600 ms
  apart). Random tiny spacings indicate noise triggering.
- **Subset check:** each `SyncEvent` should observe the devices that *physically*
  saw that fiducial. A tap event listing a camera that can't see taps is a
  mis-association (Section 9).

---

## 9. Association

**What it does:** groups per-device detections of the *same physical fiducial*
into one `SyncEvent`. It pre-aligns devices using a coarse common clock
(wall-clock/NTP, ms-class) and clusters detections within
`association_tolerance_us`, keeping at most one detection per device per event.

**How matching occurs:** detections are mapped to a common frame via
`coarse_clocks`, sorted, and greedily clustered by proximity to the running
centroid. Devices may observe **different, partially-overlapping** subsets —
association does not require everyone to see everything.

**Interpreting association reports:**

```
GOOD                                  POOR                              FAILURE
evt_0 {glove, camera}                 evt_0 {glove}                     (no events with >=2 obs)
evt_1 {glove, camera}                 evt_1 {glove, camera}             -> nothing to fit
evt_2 {glove, camera}                 evt_2 {glove}   <- camera missed   -> devices unreachable
... consistent subsets                ... unstable subsets
```

- **Good:** stable subsets, counts match gestures, spacing matches.
- **Poor:** subsets flicker (some events miss a device) → detector misses or
  tolerance too tight.
- **Failure:** few/no multi-device events → no edges → devices unreachable.

**Troubleshooting:**

| Symptom | Likely cause | Fix |
|---|---|---|
| Events split (each device alone) | `association_tolerance_us` too small vs coarse-clock error | raise tolerance (but keep < gesture spacing) |
| Two fiducials merged into one | tolerance too large vs spacing | lower tolerance, or space gestures further apart |
| A device never appears | detector missing it, or coarse offset wrong | check detector; supply correct `coarse_clocks` |
| Spurious cross-modality events | false positives coinciding | raise detector threshold; use `min_events`/RANSAC downstream |

Rule of thumb: **coarse-clock error < association_tolerance_us < gesture spacing.**

---

## 10. Running Synchronization

One call does estimation + graph + timeline:

```python
from sentrixsync.sync import synchronize

result = synchronize(
    reference_device_id="glove",
    descriptors={"glove": glove_desc, "camera": cam_desc},
    stream_timestamps={("glove","tactile"): glove_all_t,      # ALL sample times
                       ("camera","image"):  cam_all_t},
    sync_events=events,
    grid_rate_hz=1000,                    # reference grid rate (use the highest native rate)
    rejection_tolerance_us=20_000,        # gap beyond which a grid point is flagged invalid
    robust_estimation=True,               # RANSAC edge fits (recommended for real data)
    ransac_threshold_us=1500.0,
    min_events=6,                         # reject spurious few-coincidence edges
    confidence_decay_tau_us=2_000_000)    # clock confidence decays over long gaps
```

### Estimation modes — when to use which

| Mode | How to enable | Use when |
|---|---|---|
| **TLS** (default) | `robust_estimation=False` | clean evidence; symmetric noise in both clocks; fastest |
| **RANSAC** | `robust_estimation=True` | **real data** — detector false positives / mis-associations present |
| **Piecewise** | `fit_piecewise_affine(...)` (direct) | long sessions with nonlinear (thermal) drift; affine residual too high |

### Key parameters

| Parameter | Meaning | Practical guidance |
|---|---|---|
| `grid_rate_hz` | reference timeline rate | the highest device rate (e.g. tactile 1000 Hz) |
| `rejection_tolerance_us` | max gap before "invalid" | ~1–2× the slowest stream's sample interval |
| `ransac_threshold_us` | inlier band for edge fits | ~3–5× expected event jitter (e.g. 1000–2000 µs) |
| `min_events` | min shared events to form an edge | 2 for clean; **5–8 for noisy** (rejects spurious edges) |
| `confidence_decay_tau_us` | confidence half-life away from events | ~the spacing between your sync gestures |

**Quick recipes:**

- *Clean lab capture, dense events:* `robust_estimation=False, min_events=2`.
- *Real hardware, possible false detections:* `robust_estimation=True,
  min_events=6, ransac_threshold_us=1500`.
- *Hour-long session, thermal drift suspected:* fit piecewise and compare
  (Section 13 / `compare_affine_vs_piecewise`).

---

## 11. Understanding Outputs

Everything lives on the returned `SyncResult`.

```python
r = result
r.clock_models                 # dict[device -> ClockModel]
r.timeline                     # BuiltTimeline: .grid_us, .per_stream[key] (StreamAlignment)
r.confidence                   # dict[stream_key -> ConfidenceComponents]
r.sync_report                  # SyncReport (per-device models, residual, coverage, dropout)
r.validation_report            # ValidationReport (gate verdict, property checks, roundtrip)
r.diagnostics                  # ReconcileDiagnostics (edges, reachable, unreachable, hops, paths)
r.metrics                      # dict: sync_resid_us, coverage_min, dropout_max, roundtrip_accuracy, ...
```

| Artifact | Read it via | Interpretation |
|---|---|---|
| **Clock model** | `r.clock_models["camera"]` → `.alpha, .beta_us, .clock_confidence` | `t_ref = α·t_local + β`; α≈1 (skew), β = offset; map any time with `.to_reference(t)` |
| **Sync graph** | `r.diagnostics.edges`, `.hops`, `.paths`, `.unreachable` | which devices are linked, via how many hops; empty `unreachable` = all aligned |
| **Unified timeline** | `r.timeline.grid_us`, `r.timeline.per_stream[key].valid` | the reference grid; per-stream which grid points are real vs gaps |
| **Confidence report** | `r.confidence[key].source / .clock / .interpolation`, `.derived_scalar()` | three separate trust components per grid point |
| **Alignment statistics** | `r.metrics["coverage"]`, `["coverage_min"]`, `["dropout"]` | fraction of grid each stream covers; per-stream dropout |
| **Residual report** | `r.sync_report.sync_resid_us` | how well all devices agree on events in reference time (lower = better) |
| **Device status** | `r.metrics["reachable"]/["unreachable"]`, `r.validation_report.gate_verdict` | per-device reachability; overall release/certified/needs_review/blocked |

**Map a device timestamp to the reference timeline:**

```python
cam = result.clock_models["camera"]
t_ref = cam.to_reference(frame_t_us)        # camera frame -> glove-reference microseconds
```

---

## 12. Synchronizing a Tactile Glove and Video (worked example)

```
Arduino tactile glove        +        MP4 recording
   glove.csv                          session.mp4
```

1. **Record data** — run a gesture protocol: tap the table firmly **5 times**,
   spread across the recording, in clear view of the camera.
2. **Export tactile logs** — serial logger → `glove.csv` (`t_device_us` + forces).
3. **Load video** — extract per-frame `t_us` + mean luminance / hand motion.
4. **Run detectors:**

```python
from sentrixsync.detect import get_detector, associate_detections
from sentrixsync.core import EvidenceTier
glove_taps = get_detector("tactile_tap", threshold=150).detect(glove_t, glove_signal).times_us
cam_evts   = get_detector("visual_flash", threshold=cam_thr).detect(cam_t, cam_signal).times_us
```

5. **Associate:**

```python
events = associate_detections({"glove": glove_taps, "camera": cam_evts},
                              tier=EvidenceTier.SHARED_EVENT,
                              association_tolerance_us=20_000,
                              coarse_clocks={"glove": (1,0), "camera": (1, ntp_offset_us)})
assert len(events) >= 3, "too few shared events — check detectors/tolerance"
```

6. **Synchronize:**

```python
from sentrixsync.sync import synchronize
result = synchronize(reference_device_id="glove",
                     descriptors={"glove": glove_desc, "camera": cam_desc},
                     stream_timestamps={("glove","tactile"): glove_t, ("camera","image"): cam_t},
                     sync_events=events, grid_rate_hz=1000, rejection_tolerance_us=20_000,
                     robust_estimation=True, min_events=4)
```

7. **Unified timeline / transform:**

```python
cam = result.clock_models["camera"]
print(f"camera_time -> glove_time:  t_ref = {cam.alpha:.6f}*t + {cam.beta_us:.0f}")
ref_times_for_frames = [cam.to_reference(t) for t in cam_t]   # every frame, in glove time
```

**Expected outputs:**

```
verdict: release            # < 2 ms residual band
sync_resid_us ≈ 8000–16000  # ~half a 30 fps frame (the camera detection floor)
clock_models["camera"]: alpha≈1.000±ppm, beta_us≈<offset>, clock_confidence≈0.9
diagnostics.unreachable == []   # both devices aligned (1 edge, 1 hop)
```

**Validation:** confirm `len(events)` matches your 5 taps; residual is near the
camera frame floor (not seconds); `coverage_min` ≈ 1.0; verdict is
`release`/`certified`. Spot-check by overlaying a known tap: the glove tap time,
mapped to camera time, should land within ~one frame of the visible contact.

---

## 13. Using the Synthetic Tactile Generator

The generator lets you exercise the entire platform with **known ground truth**,
before (or alongside) hardware. Real and synthetic devices are processed
**identically** — same detectors, association, estimator, graph.

**Generate a synthetic session and run it:**

```python
from sentrixsync.scenarios import build_preset, run_scenario           # 2-device
r = run_scenario(build_preset("dual_device_offset"))
acc = r.metrics["roundtrip_accuracy"]["ego_cam"]      # measured vs injected truth
print(acc)   # {'alpha_err':..., 'beta_err_us':..., 'alignment_rmse_us':...}
```

**Multi-device synthetic (no device sees all events, transitive paths):**

```python
from sentrixsync.scenarios import build_multimodal_preset, run_multimodal_scenario
r = run_multimodal_scenario(build_multimodal_preset("mm_5device"))
print(r.metrics["hops"], r.metrics["unreachable"])
```

**Create ground-truth events / validate detectors:** synthetic scenarios plant
events at exact reference times. Run your detector on the generated signal and
compare recovered times to the planted ones — this validates the detector in
isolation.

**Validate association / estimators / stress-test:**

```python
from sentrixsync.scenarios import run_with_corruption, CorruptionSpec, build_multimodal_preset
scen = build_multimodal_preset("mm_5device")
r = run_with_corruption(scen, CorruptionSpec(fn_rate=0.1, dup_rate=0.1, fp_rate=0.15,
                                             perturb_us=200, seed=3),
                        robust_estimation=True, min_events=6)
# inject missed/duplicate/false detections + jitter; verify graceful, accurate recovery
```

**Benchmark synchronization quality (reproducible):**

```bash
python benchmarks/run_sync_benchmark.py --out benchmarks
# -> benchmarks/sync_benchmark_report.md  (accuracy vs the synthetic budget,
#    multimodal transitive results, robustness/coarse/piecewise tables)
```

**Reproduce experiments:** every scenario is seeded; the same preset + seed gives
the same result. Pin the seed in your script to reproduce a finding exactly.

**Why this matters operationally:** because synthetic and real devices enter
through the same contract, you can build and trust your detector thresholds,
association tolerance, and estimation parameters in simulation, then apply the
*same* settings to hardware.

---

## 14. Multi-Hardware Workflows

Adding a device = add its detections to the `detections` dict and its descriptor +
sample times to `synchronize`. **No synchronization logic changes.**

**Example A — Glove + Camera**

```python
detections = {"glove": glove_taps, "camera": cam_contacts}
descriptors = {"glove": glove_desc, "camera": cam_desc}
stream_timestamps = {("glove","tactile"): glove_t, ("camera","image"): cam_t}
```

**Example B — Glove + Camera + IMU** (IMU also senses the taps)

```python
detections["imu"] = get_detector("tactile_tap", threshold=2.0).detect(imu_t, imu_accel_mag).times_us
descriptors["imu"] = imu_desc
stream_timestamps[("imu","accel")] = imu_t
```

**Example C — Multiple cameras + tactile** (cameras share *flash* events with each
other; tactile shares *taps* with an IMU that bridges to the cameras → transitive)

```python
detections = {"glove": glove_taps, "imu": imu_taps,
              "cam1": cam1_flashes, "cam2": cam2_flashes}
# cam1<->cam2 via flashes; glove<->imu via taps; imu<->cam via a bridge gesture.
```

**Example D — Full multimodal** (camera, depth, glove, IMU, mic, force, eye
tracker): each contributes a detector output and a descriptor; the platform builds
one graph and one timeline. The 5-device preset (`mm_5device`) is a working
template.

In every case you call the **same** `associate_detections` + `synchronize`. The
graph figures out who links to whom and reconciles transitively; devices that
share no events are reported `unreachable` (not errors).

---

## 15. Validation and Verification

Check these on every real run:

| Metric | Where | Acceptable | Warning sign |
|---|---|---|---|
| **Residual** | `metrics["sync_resid_us"]` | < 2000 µs (release); < 500 µs (certified) | ≫ camera frame period, or seconds |
| **Confidence** | `clock_models[d].clock_confidence` | ≳ 0.8 for direct devices | < 0.5 → weak/few events |
| **Graph connectivity** | `metrics["unreachable"]` | `[]` (all reachable) | any device unreachable |
| **Event support** | `diagnostics.edges[*].n_events` | ≥ 5–10 per edge | 2–3 → fragile / possibly spurious |
| **Coverage** | `metrics["coverage_min"]` | ≥ 0.99 | low → many gaps/drops |
| **Alignment error** *(synthetic only)* | `metrics["roundtrip_accuracy"]` | within accuracy budget | exceeds budget → parameters wrong |
| **Gate verdict** | `validation_report.gate_verdict` | release / certified | needs_review / blocked |

**Warning signs & meaning:** residual in seconds → wrong/missing events or wrong
coarse clock; unreachable device → no shared events (add a bridging gesture);
many low-`n` edges → false positives (raise threshold / `min_events`);
`needs_review` → coverage or dropout out of band (check drops, not necessarily a
sync failure).

---

## 16. Common Failure Modes (troubleshooting)

| Symptom | Likely cause | Workflow to fix |
|---|---|---|
| **No associations found** | events not overlapping in coarse frame; tolerance too tight; wrong `coarse_clocks` | print detections per device; widen `association_tolerance_us`; supply NTP-based `coarse_clocks` |
| **Low confidence** | too few events; high residual | add more sync gestures spanning the session; check detector precision |
| **Disconnected graph** (device unreachable) | device shares no event with the rest | add a **bridge gesture** the missing device can sense; verify its detector fires |
| **Poor clock fit** (large residual/α off) | outlier events; nonlinear drift | enable `robust_estimation=True`; raise `min_events`; try piecewise for long sessions |
| **Duplicate events** | detector double-fires | already handled by association (one per device); or raise detector min-distance/threshold |
| **Missing detections** | threshold too high; weak gesture | lower threshold; perform firmer/brighter gestures |
| **Coarse-clock problems** | wall-clock error ≥ association tolerance | tighten host NTP; or increase event separation and raise tolerance accordingly |
| **Clock drift** | long session, skew accumulates | ensure events span the whole session; piecewise if nonlinear |
| **Device restarts** *(known limitation)* | epoch jump mid-session breaks the affine model | split the recording at the restart into separate sessions |

---

## 17. Best Practices

- **Data collection:** one clock per device; log device-local µs; never
  pre-correct or re-grid; reference bulky payloads.
- **Recording sessions:** bound session length (avoid mid-session device
  restarts); start/stop cleanly.
- **Synchronization gestures:** perform clear, well-separated shared events
  (firm taps / bright flashes) at the **start, middle, and end** so skew is
  observable across the whole session; aim for **≥ 8–10** spread events.
- **Detector design:** emit *sparse, unambiguous* events; favor precision over
  recall (a missed event is fine; a false one is costly — though RANSAC +
  `min_events` mitigate it).
- **Threshold selection:** tune on a short recording (or the synthetic generator)
  until event counts match your gestures; reuse the same thresholds on full runs.
- **Confidence interpretation:** treat the three components separately; for
  long-gap regions expect (and trust) lower clock confidence.
- **Multi-device capture:** ensure every device shares events with at least one
  other (directly or via a bridge); the reference should be your highest-rate /
  most-trusted clock.
- **Long recordings:** keep events spanning the whole duration; consider
  piecewise drift; watch coverage.
- **Real hardware integration:** validate the exact detector/association/estimation
  settings in simulation first, then apply them unchanged to hardware.

---

## 18. Complete Example Walkthrough (start to finish)

A reproducible glove + video synchronization.

**Directory structure**

```
my_capture/
├── glove.csv            # t_device_us, f0..f4   (from serial logger)
├── session.mp4          # the video recording
├── run_sync.py          # the script below
└── out/
    └── session.json     # synchronized session manifest (written by the script)
```

**Inputs**

- `glove.csv`: MCU microsecond timestamps + 5 forces; ≥ 5 firm taps spread out.
- `session.mp4`: same taps visible to the camera; a bright contact each time.

**`run_sync.py`**

```python
import numpy as np, cv2
from sentrixsync.detect import get_detector, associate_detections
from sentrixsync.sync import synchronize
from sentrixsync.core import (DeviceDescriptor, StreamDescriptor, ClockDescriptor,
                              EvidenceTier, Kernel, DeviceRegistration, DeviceRole,
                              Origin, Session, SessionMetadata, TimelineRef)
from sentrixsync.manifest import save_session

# --- 1. load glove ---
g = np.loadtxt("glove.csv", delimiter=",", skiprows=1)
glove_t = g[:, 0].astype(np.int64)
glove_signal = g[:, 1:].sum(axis=1).astype(float)

# --- 2. load video (frame times + luminance) ---
cap = cv2.VideoCapture("session.mp4"); fps = cap.get(cv2.CAP_PROP_FPS)
ct, lum, i = [], [], 0
while True:
    ok, fr = cap.read()
    if not ok: break
    ct.append(int(round(cap.get(cv2.CAP_PROP_POS_MSEC)*1000)) or int(round(i/fps*1e6)))
    lum.append(float(fr.mean())); i += 1
cam_t = np.array(ct, np.int64); cam_signal = np.array(lum, float)

# --- 3. detect ---
glove_taps = get_detector("tactile_tap", threshold=150).detect(glove_t, glove_signal).times_us
cam_evts   = get_detector("visual_flash", threshold=float(np.mean(lum)+3*np.std(lum))
                          ).detect(cam_t, cam_signal).times_us
print("glove events:", len(glove_taps), " camera events:", len(cam_evts))

# --- 4. associate ---
events = associate_detections({"glove": glove_taps, "camera": cam_evts},
                              tier=EvidenceTier.SHARED_EVENT,
                              association_tolerance_us=20_000)
print("associated events:", len(events))

# --- 5. descriptors ---
def desc(dev, modality, rate, kernel, ref):
    sid = f"{modality}_s"
    return DeviceDescriptor(device_id=dev, modality=modality, producer="capture",
        is_synthetic=False, reference_candidate=ref,
        clock=ClockDescriptor(clock_id=f"{dev}_clk", resolution_us=1),
        evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
        streams=[StreamDescriptor(sid, dev, kind=modality, kernel=kernel,
                 payload_kind=f"{modality}_uri", units="raw", nominal_rate_hz=rate)])
gd = desc("glove","tactile",1000,Kernel.CONTINUOUS,True)
cd = desc("camera","rgb",fps,Kernel.HOLD,False)

# --- 6. synchronize ---
result = synchronize(reference_device_id="glove", descriptors={"glove": gd, "camera": cd},
    stream_timestamps={("glove","tactile_s"): glove_t, ("camera","rgb_s"): cam_t},
    sync_events=events, grid_rate_hz=1000, rejection_tolerance_us=20_000,
    robust_estimation=True, min_events=4)

# --- 7. report + save a session manifest ---
print("verdict:", result.validation_report.gate_verdict.value)
print("residual us:", round(result.metrics["sync_resid_us"],1))
cam = result.clock_models["camera"]
print(f"camera->glove:  t = {cam.alpha:.6f}*t_cam + {cam.beta_us:.0f}")

session = Session(
    metadata=SessionMetadata(session_id="capture001", origin=Origin.REAL, producers=["capture"],
                             grid_rate_hz=1000),
    devices=[DeviceRegistration("glove", DeviceRole.REFERENCE, descriptor=gd),
             DeviceRegistration("camera", DeviceRole.FOLLOWER, descriptor=cd)],
    timeline=TimelineRef(timeline_id="tl", reference_clock_id=result.reference_clock_id,
                         grid_rate_hz=1000, t_start_us=result.timeline.t_start_us,
                         t_end_us=result.timeline.t_end_us, n_grid=result.timeline.n_grid),
    sync_report=result.sync_report, validation_report=result.validation_report)
save_session(session, "out/session.json")
print("wrote out/session.json")
```

**Run it**

```bash
python run_sync.py
```

**Expected results**

```
glove events: 5  camera events: 5
associated events: 5
verdict: release
residual us: ~12000           # ~half a 30fps frame
camera->glove:  t = 1.000007*t_cam + 21337000
wrote out/session.json
```

**Validation procedure**

1. Event counts (`5` and `5`) match your taps.
2. `associated events == 5` (no split/merge).
3. Residual near the camera frame floor (ms), not seconds.
4. `verdict` is `release` (or `certified` with a fast camera).
5. Sanity overlay: take a known tap's glove time, the matching camera time, and
   confirm `cam.to_reference(cam_time)` lands within ~one frame of the glove tap.
6. (Optional) Re-record with the synthetic generator using the *same* thresholds
   and confirm round-trip accuracy is within the budget before trusting hardware.

---

### Where to go next

- New device type? Write an adapter + (optional) detector — see
  [`REAL_DEVICE_INTEGRATION_GUIDE.md`](./REAL_DEVICE_INTEGRATION_GUIDE.md).
- Want the guarantees and limits? See
  [`SYNTHETIC_ACCURACY_BUDGET.md`](./SYNTHETIC_ACCURACY_BUDGET.md) and the failure
  table in [`IMPLEMENTATION_NOTES.md`](./IMPLEMENTATION_NOTES.md).
- Reminder of what's deferred (payload resolvers for live detector signals, clock-
  reset handling, streaming timeline for very long sessions): see the "Deliberately
  NOT built" list in `IMPLEMENTATION_NOTES.md`.
