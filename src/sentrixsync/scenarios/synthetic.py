"""Synthetic multi-device synchronization scenarios.

Builds deterministic scenarios that inject a KNOWN clock relationship (offset,
drift/skew, jitter, packet loss, burst loss) plus a shared physical event train,
then runs the full pipeline: detector plugins -> matcher -> synchronize. Because
the injected clock is known, the estimator's recovery can be measured against
ground truth (docs/SYNTHETIC_ACCURACY_BUDGET.md).

Generation is in each device's own local epoch (starting at 0); the true clock
maps local -> reference. The reference device is identity. Stream timestamps are
clean (clock only); the modelled jitter lives on the event observations, where
the estimator consumes it — keeping estimation and join concerns separate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..clock.forward import (
    ForwardClock,
    bernoulli_keep_mask,
    enforce_monotonic_int_us,
    gilbert_keep_mask,
    quantize_us,
)
from ..config import GateThresholds
from ..core.device import ClockDescriptor, DeviceDescriptor, StreamDescriptor
from ..core.types import EvidenceTier, Kernel
from ..detect import get_detector, match_detections
from ..sync.engine import SyncResult, synchronize


# --------------------------------------------------------------------------- #
# Scenario specification
# --------------------------------------------------------------------------- #
@dataclass
class FollowerSpec:
    device_id: str
    modality: str
    rate_hz: float
    detector: str
    offset_us: float = 0.0
    skew_ppm: float = 0.0
    jitter_us: float = 0.0
    loss_p: float = 0.0
    gilbert: tuple[float, float] | None = None      # (p_good_to_bad, p_bad_to_good)


@dataclass
class ScenarioSpec:
    name: str
    followers: list[FollowerSpec]
    duration_s: float = 8.0
    n_events: int = 80
    grid_rate_hz: float = 1600.0
    rejection_tolerance_us: int = 6000
    seed: int = 7
    ref_device_id: str = "glove_L"
    ref_modality: str = "tactile"
    ref_rate_hz: float = 1600.0
    ref_detector: str = "tactile_tap"


# --------------------------------------------------------------------------- #
# Generated scenario
# --------------------------------------------------------------------------- #
@dataclass
class ScenarioDevice:
    descriptor: DeviceDescriptor
    stream_timestamps: dict[str, np.ndarray]
    event_signal_times: np.ndarray
    event_signal: np.ndarray
    detector_name: str
    ground_truth: ForwardClock
    expected_counts: dict[str, int]


@dataclass
class SyntheticScenario:
    name: str
    reference_device_id: str
    grid_rate_hz: float
    rejection_tolerance_us: int
    devices: dict[str, ScenarioDevice] = field(default_factory=dict)
    event_ref_times_us: np.ndarray = field(default_factory=lambda: np.empty(0, np.int64))

    def descriptors(self) -> dict[str, DeviceDescriptor]:
        return {d: dev.descriptor for d, dev in self.devices.items()}

    def stream_timestamps(self) -> dict[tuple[str, str], np.ndarray]:
        out: dict[tuple[str, str], np.ndarray] = {}
        for dev_id, dev in self.devices.items():
            for sid, ts in dev.stream_timestamps.items():
                out[(dev_id, sid)] = ts
        return out

    def ground_truth(self) -> dict[str, ForwardClock]:
        return {d: dev.ground_truth for d, dev in self.devices.items()}

    def expected_counts(self) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for dev_id, dev in self.devices.items():
            for sid, c in dev.expected_counts.items():
                out[(dev_id, sid)] = c
        return out


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _descriptor(device_id: str, modality: str, rate_hz: float, *, reference_candidate: bool
                ) -> DeviceDescriptor:
    stream_id = f"{modality}_stream"
    return DeviceDescriptor(
        device_id=device_id, modality=modality, producer="synthetic", is_synthetic=True,
        reference_candidate=reference_candidate,
        clock=ClockDescriptor(clock_id=f"{device_id}_clock", resolution_us=1),
        evidence_tiers=[EvidenceTier.SHARED_EVENT, EvidenceTier.WALL_CLOCK],
        streams=[StreamDescriptor(stream_id=stream_id, device_id=device_id, kind=modality,
                                  kernel=Kernel.CONTINUOUS, payload_kind=f"{modality}_uri",
                                  units="a.u.", nominal_rate_hz=rate_hz)])


def _stream_local(fwd: ForwardClock, rate_hz: float, duration_s: float,
                  keep_mask: np.ndarray | None) -> tuple[np.ndarray, int]:
    """Device-local stream timestamps (epoch 0), optional loss applied."""
    n = int(round(rate_hz * duration_s))
    t_local = np.arange(n, dtype=float) / rate_hz * 1e6
    t_local = enforce_monotonic_int_us(quantize_us(t_local, 1))
    if keep_mask is not None:
        t_local = t_local[keep_mask]
    return t_local, n


def _event_signal(obs_local_us: np.ndarray, duration_s: float, rng: np.random.Generator,
                  *, base_rate_hz: float = 2000.0, amp: float = 1.0,
                  width_us: float = 2000.0, noise: float = 0.01) -> tuple[np.ndarray, np.ndarray]:
    """Signal whose sampling grid includes the exact observation times, so the
    detector recovers each observation precisely (detection adds no error;
    modelled jitter already lives in `obs_local_us`)."""
    end = duration_s * 1e6
    base = np.arange(0, end, 1e6 / base_rate_hz)
    times = np.unique(np.concatenate([base, obs_local_us.astype(float)])).astype(np.int64)
    sig = rng.normal(0.0, noise, size=times.size)
    for c in obs_local_us:
        sig = sig + amp * np.exp(-0.5 * ((times - c) / width_us) ** 2)
    # Make the planted observation samples dominate so the detector recovers them
    # exactly (a strong, unambiguous impulse) — detection adds no error; the
    # modelled jitter already lives in obs_local_us.
    peak_idx = np.searchsorted(times, obs_local_us.astype(np.int64))
    sig[peak_idx] = amp * 10.0
    return times, sig


def build_scenario(spec: ScenarioSpec) -> SyntheticScenario:
    rng = np.random.default_rng(spec.seed)
    dur = spec.duration_s
    # Shared physical events at reference times within the session interior.
    t_ref_events = np.round(np.linspace(0.1 * dur, 0.9 * dur, spec.n_events) * 1e6).astype(np.int64)

    scen = SyntheticScenario(name=spec.name, reference_device_id=spec.ref_device_id,
                             grid_rate_hz=spec.grid_rate_hz,
                             rejection_tolerance_us=spec.rejection_tolerance_us,
                             event_ref_times_us=t_ref_events)

    # Reference device (identity clock).
    ref_fwd = ForwardClock(alpha=1.0, beta_us=0.0)
    ref_desc = _descriptor(spec.ref_device_id, spec.ref_modality, spec.ref_rate_hz,
                           reference_candidate=True)
    ref_stream_id = ref_desc.streams[0].stream_id
    ref_ts, ref_n = _stream_local(ref_fwd, spec.ref_rate_hz, dur, None)
    ref_obs = np.round(ref_fwd.local_from_ref(t_ref_events)).astype(np.int64)  # == t_ref_events
    ref_sig_t, ref_sig = _event_signal(ref_obs, dur, rng)
    scen.devices[spec.ref_device_id] = ScenarioDevice(
        descriptor=ref_desc, stream_timestamps={ref_stream_id: ref_ts},
        event_signal_times=ref_sig_t, event_signal=ref_sig,
        detector_name=spec.ref_detector, ground_truth=ref_fwd,
        expected_counts={ref_stream_id: ref_n})

    # Followers.
    for fs in spec.followers:
        fwd = ForwardClock.from_offset_skew(fs.offset_us, fs.skew_ppm)
        n_nominal = int(round(fs.rate_hz * dur))
        if fs.gilbert is not None:
            keep = gilbert_keep_mask(n_nominal, fs.gilbert[0], fs.gilbert[1], rng)
        elif fs.loss_p > 0:
            keep = bernoulli_keep_mask(n_nominal, fs.loss_p, rng)
        else:
            keep = None
        desc = _descriptor(fs.device_id, fs.modality, fs.rate_hz, reference_candidate=False)
        sid = desc.streams[0].stream_id
        ts, _ = _stream_local(fwd, fs.rate_hz, dur, keep)
        # Event observations in follower-local time, plus modelled jitter.
        obs = fwd.local_from_ref(t_ref_events)
        if fs.jitter_us > 0:
            obs = obs + rng.normal(0.0, fs.jitter_us, size=obs.shape)
        obs = np.round(obs).astype(np.int64)
        sig_t, sig = _event_signal(obs, dur, rng)
        scen.devices[fs.device_id] = ScenarioDevice(
            descriptor=desc, stream_timestamps={sid: ts},
            event_signal_times=sig_t, event_signal=sig, detector_name=fs.detector,
            ground_truth=fwd, expected_counts={sid: n_nominal})
    return scen


# --------------------------------------------------------------------------- #
# Runner: detect -> match -> synchronize
# --------------------------------------------------------------------------- #
def run_scenario(scenario: SyntheticScenario, *, gates: GateThresholds | None = None
                 ) -> SyncResult:
    detections: dict[str, np.ndarray] = {}
    for dev_id, dev in scenario.devices.items():
        detector = get_detector(dev.detector_name)
        detections[dev_id] = detector.detect(dev.event_signal_times, dev.event_signal).times_us

    events = match_detections(detections, tier=EvidenceTier.SHARED_EVENT)
    return synchronize(
        reference_device_id=scenario.reference_device_id,
        descriptors=scenario.descriptors(),
        stream_timestamps=scenario.stream_timestamps(),
        sync_events=events,
        grid_rate_hz=scenario.grid_rate_hz,
        rejection_tolerance_us=scenario.rejection_tolerance_us,
        gates=gates,
        ground_truth=scenario.ground_truth(),
        expected_counts=scenario.expected_counts())


# --------------------------------------------------------------------------- #
# Presets (cover offset, drift, jitter, loss, burst, and a combined case)
# --------------------------------------------------------------------------- #
def _cam(**kw) -> FollowerSpec:
    base = dict(device_id="ego_cam", modality="rgb", rate_hz=200.0, detector="visual_flash")
    base.update(kw)
    return FollowerSpec(**base)


PRESETS: dict[str, ScenarioSpec] = {
    "clean":   ScenarioSpec("clean", [_cam(offset_us=5000)], seed=1),
    "offset":  ScenarioSpec("offset", [_cam(offset_us=20000)], seed=2),
    "drift":   ScenarioSpec("drift", [_cam(skew_ppm=25.0)], seed=3),
    "jitter":  ScenarioSpec("jitter", [_cam(offset_us=10000, jitter_us=500.0)], seed=4),
    "loss":    ScenarioSpec("loss", [_cam(offset_us=10000, jitter_us=100.0, loss_p=0.05)], seed=5),
    "burst":   ScenarioSpec("burst", [_cam(offset_us=10000, jitter_us=100.0,
                                           gilbert=(0.01, 0.30))], seed=6),
    "dual_device_offset": ScenarioSpec(
        "dual_device_offset",
        [_cam(offset_us=20431, skew_ppm=18.0, jitter_us=300.0, loss_p=0.01)], seed=7),
}


def build_preset(name: str) -> SyntheticScenario:
    if name not in PRESETS:
        raise KeyError(f"unknown scenario preset {name!r}; have {sorted(PRESETS)}")
    return build_scenario(PRESETS[name])
