"""Robustness scenarios: corrupted detections, coarse-clock sweeps, and long-
session nonlinear drift. Built on the modality-neutral multimodal scenarios; no
modality-specific logic.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..clock.estimate import fit_affine_tls, fit_piecewise_affine
from ..clock.forward import ForwardClock
from ..config import GateThresholds
from ..core.types import EvidenceTier
from ..detect import associate_detections, corrupt_detections
from ..sync.engine import SyncResult, synchronize
from .multimodal import MultimodalScenario, detect_scenario


@dataclass
class CorruptionSpec:
    fn_rate: float = 0.0
    dup_rate: float = 0.0
    fp_rate: float = 0.0
    perturb_us: float = 0.0
    seed: int = 0


def run_with_corruption(scen: MultimodalScenario, corruption: CorruptionSpec, *,
                        robust_estimation: bool = True,
                        confidence_decay_tau_us: float | None = None,
                        ransac_threshold_us: float = 1500.0, min_events: int = 6,
                        coarse_clock_override: dict[str, tuple[float, float]] | None = None,
                        gates: GateThresholds | None = None) -> SyncResult:
    """Detect -> corrupt detections -> associate -> synchronize (RANSAC by
    default). A higher `min_events` rejects spurious few-coincidence edges that
    false positives can otherwise create. `coarse_clock_override` lets a caller
    inject wall-clock error."""
    detections = detect_scenario(scen)
    detections = corrupt_detections(
        detections, duration_us=scen.duration_us, fn_rate=corruption.fn_rate,
        dup_rate=corruption.dup_rate, fp_rate=corruption.fp_rate,
        perturb_us=corruption.perturb_us, seed=corruption.seed)
    coarse = coarse_clock_override or scen.coarse_clocks()
    events = associate_detections(
        detections, tier=EvidenceTier.SHARED_EVENT,
        association_tolerance_us=scen.association_tolerance_us, coarse_clocks=coarse)
    return synchronize(
        reference_device_id=scen.reference_device_id, descriptors=scen.descriptors(),
        stream_timestamps=scen.stream_timestamps(), sync_events=events,
        grid_rate_hz=scen.grid_rate_hz, rejection_tolerance_us=scen.rejection_tolerance_us,
        gates=gates, ground_truth=scen.ground_truth(), expected_counts=scen.expected_counts(),
        robust_estimation=robust_estimation, ransac_threshold_us=ransac_threshold_us,
        confidence_decay_tau_us=confidence_decay_tau_us, min_events=min_events)


def coarse_clock_sweep(scen: MultimodalScenario, noise_levels_us: list[float], *,
                       seed: int = 0, robust_estimation: bool = True) -> list[dict]:
    """Inject increasing wall-clock (coarse) error and measure degradation.
    Returns one row per noise level with worst-device accuracy and unreachable
    count — used to find the practical association operating limit."""
    rng = np.random.default_rng(seed)
    gt = scen.ground_truth()
    rows: list[dict] = []
    for noise in noise_levels_us:
        coarse = {d: (1.0, gt[d].beta_us + float(rng.normal(0.0, noise)))
                  for d in scen.descriptors()}
        result = run_with_corruption(
            scen, CorruptionSpec(seed=seed), robust_estimation=robust_estimation,
            coarse_clock_override=coarse)
        rt = result.metrics["roundtrip_accuracy"] or {}
        rows.append({
            "coarse_noise_us": noise,
            "n_unreachable": len(result.metrics["unreachable"]),
            "max_alpha_err": max((a["alpha_err"] for a in rt.values()), default=0.0),
            "max_beta_err_us": max((a["beta_err_us"] for a in rt.values()), default=0.0),
            "sync_resid_us": result.metrics["sync_resid_us"],
        })
    return rows


def make_piecewise_session(*, duration_s: float = 120.0, n_events: int = 60,
                           rate_hz: float = 200.0, jitter_us: float = 200.0,
                           breakpoints_ppm: tuple[float, ...] = (5.0, 35.0, 12.0),
                           seed: int = 0) -> dict:
    """A long single-follower session whose TRUE clock skew changes in segments
    (nonlinear drift). Returns matched (t_local, t_ref) event evidence plus the
    follower's full sample times, for comparing single-affine vs piecewise fits.

    The reference is identity; the follower's local time is built by integrating
    a piecewise-constant skew over the session (continuous, kinked rate).
    """
    rng = np.random.default_rng(seed)
    dur_us = duration_s * 1e6
    segs = len(breakpoints_ppm)
    bounds = np.linspace(0.0, dur_us, segs + 1)
    alphas = [1.0 + p * 1e-6 for p in breakpoints_ppm]

    def ref_to_local(t_ref: np.ndarray) -> np.ndarray:
        # Integrate 1/alpha over piecewise-constant-skew segments (continuous local).
        t_ref = np.asarray(t_ref, dtype=float)
        local = np.zeros_like(t_ref)
        for i, t in enumerate(t_ref):
            acc = 0.0
            for s in range(segs):
                lo, hi = bounds[s], bounds[s + 1]
                if t <= lo:
                    break
                seg_t = min(t, hi) - lo
                acc += seg_t / alphas[s]
            local[i] = acc
        return local

    event_ref = np.sort(rng.uniform(0.02 * dur_us, 0.98 * dur_us, n_events))
    obs_local = ref_to_local(event_ref) + rng.normal(0.0, jitter_us, n_events)
    sample_ref = np.arange(0, dur_us, 1e6 / rate_hz)
    sample_local = ref_to_local(sample_ref)
    return {
        "t_local": np.round(obs_local).astype(np.int64),
        "t_ref": np.round(event_ref).astype(np.int64),
        "sample_local": np.round(sample_local).astype(np.int64),
        "sample_ref_true": np.round(sample_ref).astype(np.int64),
        "n_segments": segs,
    }


def compare_affine_vs_piecewise(session: dict, *, device_id: str = "follower",
                                ref_clock_id: str = "ref_clk") -> dict:
    """Fit the long-session evidence with single-affine (TLS) and piecewise; report
    fit residual and alignment RMSE vs the true mapping over the full stream."""
    tl, tr = session["t_local"], session["t_ref"]
    s_local, s_true = session["sample_local"], session["sample_ref_true"]

    aff = fit_affine_tls(tl, tr, device_id=device_id, ref_clock_id=ref_clock_id).model
    pw = fit_piecewise_affine(tl, tr, n_segments=session["n_segments"],
                              device_id=device_id, ref_clock_id=ref_clock_id).model

    def rmse(model) -> float:
        pred = np.array([model.to_reference(int(t)) for t in s_local], dtype=float)
        return float(np.sqrt(np.mean((pred - s_true.astype(float)) ** 2)))

    return {
        "affine_fit_residual_us": aff.fit_residual_us,
        "piecewise_fit_residual_us": pw.fit_residual_us,
        "affine_alignment_rmse_us": rmse(aff),
        "piecewise_alignment_rmse_us": rmse(pw),
    }
