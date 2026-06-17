"""Heterogeneous multimodal synchronization scenarios.

Generalizes the 2-device synthetic scenarios to N devices with event *groups*
that are visible only to subsets of devices. This exercises subset-aware
association and graph-based reconciliation: no single device observes all events,
and some devices reach the reference only transitively through intermediates.

Modalities are opaque labels here purely for readability; the synchronization
core never reads them. Each device has its own clock (offset/skew), per-event
jitter, optional stream packet loss, and a coarse wall-clock (ms-class) used only
for association pre-alignment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..clock.forward import ForwardClock, bernoulli_keep_mask, gilbert_keep_mask
from ..config import GateThresholds
from ..core.device import ClockDescriptor, DeviceDescriptor, StreamDescriptor
from ..core.types import EvidenceTier, Kernel
from ..detect import associate_detections, get_detector
from ..sync.engine import SyncResult, synchronize
from .synthetic import _event_signal, _stream_local


@dataclass
class DeviceSpec:
    device_id: str
    modality: str
    rate_hz: float
    detector: str
    offset_us: float = 0.0
    skew_ppm: float = 0.0
    jitter_us: float = 0.0
    loss_p: float = 0.0
    gilbert: tuple[float, float] | None = None
    reference_candidate: bool = False
    coarse_noise_us: float = 2000.0       # ms-class wall-clock uncertainty
    kernel: Kernel = Kernel.CONTINUOUS


@dataclass
class EventGroup:
    name: str
    visible: tuple[str, ...]              # device_ids that observe this group


@dataclass
class MultimodalScenarioSpec:
    name: str
    devices: list[DeviceSpec]
    pattern: list[str]                    # cyclic group assignment along the event grid
    groups: dict[str, EventGroup]
    reference_device_id: str
    duration_s: float = 8.0
    n_events: int = 100
    grid_rate_hz: float = 1600.0
    rejection_tolerance_us: int = 8000
    association_tolerance_us: float = 12000.0
    seed: int = 11


@dataclass
class MMDevice:
    descriptor: DeviceDescriptor
    stream_timestamps: dict[str, np.ndarray]
    event_signal_times: np.ndarray
    event_signal: np.ndarray
    detector_name: str
    ground_truth: ForwardClock
    coarse_clock: tuple[float, float]
    expected_counts: dict[str, int]


@dataclass
class MultimodalScenario:
    name: str
    reference_device_id: str
    grid_rate_hz: float
    rejection_tolerance_us: int
    association_tolerance_us: float
    devices: dict[str, MMDevice] = field(default_factory=dict)
    event_ref_times_us: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))
    event_groups: list[str] = field(default_factory=list)
    duration_us: int = 0

    def descriptors(self):
        return {d: dev.descriptor for d, dev in self.devices.items()}

    def stream_timestamps(self):
        return {(d, sid): ts for d, dev in self.devices.items()
                for sid, ts in dev.stream_timestamps.items()}

    def ground_truth(self):
        return {d: dev.ground_truth for d, dev in self.devices.items()}

    def coarse_clocks(self):
        return {d: dev.coarse_clock for d, dev in self.devices.items()}

    def expected_counts(self):
        return {(d, sid): c for d, dev in self.devices.items()
                for sid, c in dev.expected_counts.items()}


def _descriptor(spec: DeviceSpec) -> DeviceDescriptor:
    sid = f"{spec.modality}_stream"
    return DeviceDescriptor(
        device_id=spec.device_id, modality=spec.modality, producer="synthetic",
        is_synthetic=True, reference_candidate=spec.reference_candidate,
        clock=ClockDescriptor(clock_id=f"{spec.device_id}_clock", resolution_us=1),
        evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
        streams=[StreamDescriptor(stream_id=sid, device_id=spec.device_id, kind=spec.modality,
                                  kernel=spec.kernel, payload_kind=f"{spec.modality}_uri",
                                  units="a.u.", nominal_rate_hz=spec.rate_hz)])


def build_multimodal_scenario(spec: MultimodalScenarioSpec) -> MultimodalScenario:
    rng = np.random.default_rng(spec.seed)
    dur = spec.duration_s
    # One global, well-separated event grid; cyclic group assignment so every
    # group spans the full session (good lever arm for skew on every edge).
    t_ref = np.round(np.linspace(0.05 * dur, 0.95 * dur, spec.n_events) * 1e6).astype(np.int64)
    groups = [spec.pattern[i % len(spec.pattern)] for i in range(spec.n_events)]

    scen = MultimodalScenario(
        name=spec.name, reference_device_id=spec.reference_device_id,
        grid_rate_hz=spec.grid_rate_hz, rejection_tolerance_us=spec.rejection_tolerance_us,
        association_tolerance_us=spec.association_tolerance_us,
        event_ref_times_us=t_ref, event_groups=groups, duration_us=int(dur * 1e6))

    for ds in spec.devices:
        fwd = ForwardClock.from_offset_skew(ds.offset_us, ds.skew_ppm)
        # which fiducials this device observes (its visible groups)
        visible_groups = {g for g, eg in spec.groups.items() if ds.device_id in eg.visible}
        mask = np.array([g in visible_groups for g in groups], dtype=bool)
        obs = fwd.local_from_ref(t_ref[mask])
        if ds.jitter_us > 0:
            obs = obs + rng.normal(0.0, ds.jitter_us, size=obs.shape)
        obs = np.round(obs).astype(np.int64)
        sig_t, sig = _event_signal(obs, dur, rng)

        n_nom = int(round(ds.rate_hz * dur))
        if ds.gilbert is not None:
            keep = gilbert_keep_mask(n_nom, ds.gilbert[0], ds.gilbert[1], rng)
        elif ds.loss_p > 0:
            keep = bernoulli_keep_mask(n_nom, ds.loss_p, rng)
        else:
            keep = None
        ts, _ = _stream_local(fwd, ds.rate_hz, dur, keep)

        desc = _descriptor(ds)
        sid = desc.streams[0].stream_id
        coarse = (1.0, ds.offset_us + float(rng.normal(0.0, ds.coarse_noise_us)))
        scen.devices[ds.device_id] = MMDevice(
            descriptor=desc, stream_timestamps={sid: ts}, event_signal_times=sig_t,
            event_signal=sig, detector_name=ds.detector, ground_truth=fwd,
            coarse_clock=coarse, expected_counts={sid: n_nom})
    return scen


def detect_scenario(scen: MultimodalScenario) -> dict[str, np.ndarray]:
    """Run each device's detector over its signal -> per-device local detections."""
    out = {}
    for dev_id, dev in scen.devices.items():
        det = get_detector(dev.detector_name)
        out[dev_id] = det.detect(dev.event_signal_times, dev.event_signal).times_us
    return out


def run_multimodal_scenario(scen: MultimodalScenario, *, gates: GateThresholds | None = None
                            ) -> SyncResult:
    detections = detect_scenario(scen)

    events = associate_detections(
        detections, tier=EvidenceTier.SHARED_EVENT,
        association_tolerance_us=scen.association_tolerance_us,
        coarse_clocks=scen.coarse_clocks())

    return synchronize(
        reference_device_id=scen.reference_device_id, descriptors=scen.descriptors(),
        stream_timestamps=scen.stream_timestamps(), sync_events=events,
        grid_rate_hz=scen.grid_rate_hz, rejection_tolerance_us=scen.rejection_tolerance_us,
        gates=gates, ground_truth=scen.ground_truth(), expected_counts=scen.expected_counts())


# --------------------------------------------------------------------------- #
# Preset: 5 devices, 3 event groups, transitive paths, no device sees all events
# --------------------------------------------------------------------------- #
#   tap   -> {glove, imu, audio}     (mechanical impulse)
#   flash -> {camera, mocap}         (optical)
#   bridge-> {imu, camera, mocap}    (combined event; imu is the hinge to flash group)
# reference = glove (sees tap only). camera/mocap reach glove ONLY transitively
# via imu. No device observes all three groups.
_MM_5 = MultimodalScenarioSpec(
    name="mm_5device",
    reference_device_id="glove",
    pattern=["tap", "flash", "tap", "flash", "bridge"],   # tap 40%, flash 40%, bridge 20%
    n_events=100, duration_s=8.0,
    groups={
        "tap": EventGroup("tap", ("glove", "imu", "audio")),
        "flash": EventGroup("flash", ("camera", "mocap")),
        "bridge": EventGroup("bridge", ("imu", "camera", "mocap")),
    },
    devices=[
        DeviceSpec("glove", "tactile", 1600.0, "tactile_tap", offset_us=0.0,
                   skew_ppm=0.0, jitter_us=150.0, reference_candidate=True),
        DeviceSpec("imu", "imu", 800.0, "tactile_tap", offset_us=12000.0,
                   skew_ppm=12.0, jitter_us=200.0),
        DeviceSpec("audio", "audio", 1000.0, "tactile_tap", offset_us=-8000.0,
                   skew_ppm=-6.0, jitter_us=200.0, loss_p=0.02),
        DeviceSpec("camera", "rgb", 200.0, "visual_flash", offset_us=20431.0,
                   skew_ppm=18.0, jitter_us=300.0, loss_p=0.01, kernel=Kernel.HOLD),
        DeviceSpec("mocap", "pose", 120.0, "visual_flash", offset_us=-15000.0,
                   skew_ppm=9.0, jitter_us=250.0, gilbert=(0.01, 0.30)),
    ],
    seed=11)

MULTIMODAL_PRESETS: dict[str, MultimodalScenarioSpec] = {"mm_5device": _MM_5}


def build_multimodal_preset(name: str) -> MultimodalScenario:
    if name not in MULTIMODAL_PRESETS:
        raise KeyError(f"unknown multimodal preset {name!r}; have {sorted(MULTIMODAL_PRESETS)}")
    return build_multimodal_scenario(MULTIMODAL_PRESETS[name])
