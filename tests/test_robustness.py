"""Scenario-level robustness: corrupted detections, spurious-edge rejection,
graceful degradation, coarse-clock operating limit, and piecewise gains."""
from __future__ import annotations

import pytest

from sentrixsync.scenarios import (
    CorruptionSpec,
    build_multimodal_preset,
    coarse_clock_sweep,
    compare_affine_vs_piecewise,
    make_piecewise_session,
    run_with_corruption,
)


def _worst_alpha(result):
    rt = result.metrics["roundtrip_accuracy"]
    return max(a["alpha_err"] for a in rt.values())


def _worst_beta(result):
    rt = result.metrics["roundtrip_accuracy"]
    return max(a["beta_err_us"] for a in rt.values())


HEAVY = CorruptionSpec(fn_rate=0.10, dup_rate=0.10, fp_rate=0.15, perturb_us=200, seed=3)


def test_robust_beats_baseline_under_heavy_corruption():
    scen = build_multimodal_preset("mm_5device")
    base = run_with_corruption(scen, HEAVY, robust_estimation=False, min_events=2)
    rob = run_with_corruption(scen, HEAVY, robust_estimation=True, min_events=6)
    assert _worst_alpha(rob) < _worst_alpha(base)
    assert _worst_beta(rob) < _worst_beta(base)
    # robust recovery stays within a sane bound despite heavy corruption
    assert _worst_alpha(rob) < 5e-4 and _worst_beta(rob) < 1500


def test_spurious_cross_group_edge_rejected_in_robust_mode():
    scen = build_multimodal_preset("mm_5device")
    rob = run_with_corruption(scen, HEAVY, robust_estimation=True, min_events=6)
    pairs = {frozenset((e.a, e.b)) for e in rob.diagnostics.edges}
    # glove (tap-only) shares no real event with the flash group -> these edges
    # must not appear (they would be false-positive coincidences)
    assert frozenset(("glove", "mocap")) not in pairs
    assert frozenset(("glove", "camera")) not in pairs
    assert rob.metrics["unreachable"] == []          # all still reconciled (transitively)


def test_extreme_corruption_degrades_gracefully():
    scen = build_multimodal_preset("mm_5device")
    extreme = CorruptionSpec(fn_rate=0.5, dup_rate=0.3, fp_rate=1.0, perturb_us=500, seed=9)
    result = run_with_corruption(scen, extreme, robust_estimation=True, min_events=6)
    # must not crash; reports must be well-formed; unreachable devices are allowed
    result.sync_report.validate()
    result.validation_report.validate()
    assert 0.0 <= result.metrics["coverage_min"] <= 1.0


def test_coarse_clock_operating_limit():
    # association tolerance for this preset is 12 ms.
    scen = build_multimodal_preset("mm_5device")
    rows = coarse_clock_sweep(scen, [0, 4000, 8000, 20000, 40000], seed=2)
    by = {r["coarse_noise_us"]: r for r in rows}
    # operating region: coarse error well below the association tolerance
    assert by[0]["n_unreachable"] == 0
    assert by[8000]["n_unreachable"] == 0
    assert by[8000]["max_alpha_err"] < 1e-4
    # breakdown region: coarse error at/above the association tolerance
    assert by[20000]["n_unreachable"] > 0
    assert by[40000]["n_unreachable"] > 0


def test_piecewise_improves_long_session_alignment():
    session = make_piecewise_session(seed=1)
    cmp = compare_affine_vs_piecewise(session)
    assert cmp["piecewise_alignment_rmse_us"] < cmp["affine_alignment_rmse_us"]
    assert cmp["piecewise_fit_residual_us"] <= cmp["affine_fit_residual_us"]
