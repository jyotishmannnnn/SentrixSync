"""Tests for the clock estimators."""
from __future__ import annotations

import numpy as np
import pytest

from sentrixsync.clock import (
    fit_affine,
    fit_affine_tls,
    fit_offset,
    fit_piecewise_affine,
    identity_model,
    ransac_affine,
    tls_affine,
)
from sentrixsync.core.types import EvidenceTier, ValidationError


def test_fit_offset_recovers_known_offset():
    t_local = np.arange(0, 1_000_000, 100_000)
    beta = 20000.0
    t_ref = t_local + beta
    fr = fit_offset(t_local, t_ref, device_id="cam", ref_clock_id="ref")
    assert fr.model.alpha == 1.0
    assert abs(fr.model.beta_us - beta) < 1e-6
    assert fr.residual_us < 1e-6
    assert 0.0 <= fr.model.clock_confidence <= 1.0


def test_fit_affine_recovers_offset_and_skew():
    alpha, beta = 1.000025, 15000.0
    t_local = np.linspace(0, 8_000_000, 50)
    t_ref = alpha * t_local + beta
    fr = fit_affine(t_local, t_ref, device_id="cam", ref_clock_id="ref",
                    method=EvidenceTier.SHARED_EVENT)
    assert abs(fr.model.alpha - alpha) < 1e-9
    assert abs(fr.model.beta_us - beta) < 1e-3
    assert fr.model.method is EvidenceTier.SHARED_EVENT


def test_fit_affine_robust_rejects_outlier():
    alpha, beta = 1.0, 1000.0
    t_local = np.linspace(0, 8_000_000, 40)
    t_ref = alpha * t_local + beta
    t_ref[20] += 500_000.0                     # gross outlier
    robust = fit_affine(t_local, t_ref, device_id="d", ref_clock_id="r", robust=True)
    naive = fit_affine(t_local, t_ref, device_id="d", ref_clock_id="r", robust=False)
    # robust fit stays closer to the true offset than the contaminated naive fit
    assert abs(robust.model.beta_us - beta) < abs(naive.model.beta_us - beta)


def test_fit_affine_single_point_falls_back_to_offset():
    fr = fit_affine(np.array([1000.0]), np.array([3000.0]), device_id="d", ref_clock_id="r")
    assert fr.model.alpha == 1.0
    assert abs(fr.model.beta_us - 2000.0) < 1e-6


def test_identity_model():
    m = identity_model("ref", "ref_clk")
    assert m.alpha == 1.0 and m.beta_us == 0.0
    assert m.to_reference(12345) == 12345
    assert m.clock_confidence == 1.0


def test_empty_input_rejected():
    with pytest.raises(ValidationError):
        fit_affine(np.array([]), np.array([]), device_id="d", ref_clock_id="r")


def test_tls_exact_on_noiseless_data():
    alpha, beta = 1.00003, 8000.0
    x = np.linspace(0, 8e6, 60)
    y = alpha * x + beta
    a, b, rms = tls_affine(x, y)
    assert abs(a - alpha) < 1e-9 and abs(b - beta) < 1e-3 and rms < 1e-6


def test_tls_handles_noise_in_both_variables():
    rng = np.random.default_rng(0)
    alpha, beta = 1.00002, 5000.0
    x_true = np.linspace(0, 8e6, 400)
    y_true = alpha * x_true + beta
    x = x_true + rng.normal(0, 300, x_true.shape)     # both observed with error
    y = y_true + rng.normal(0, 300, y_true.shape)
    a, b, _ = tls_affine(x, y)
    assert abs(a - alpha) < 5e-5
    assert abs(b - beta) < 600


def test_tls_offset_only_fallback_single_point():
    a, b, rms = tls_affine(np.array([1000.0]), np.array([3000.0]))
    assert a == 1.0 and abs(b - 2000.0) < 1e-9


def test_ransac_rejects_gross_outliers():
    alpha, beta = 1.00002, 5000.0
    x = np.linspace(0, 8e6, 50)
    y = alpha * x + beta
    y[::10] += 1e5                          # 5 gross outliers
    a, b, rms, mask = ransac_affine(x, y, threshold_us=2000, seed=0)
    assert abs(a - alpha) < 1e-5 and abs(b - beta) < 2000
    assert mask.sum() >= 40                 # outliers excluded from consensus
    # RANSAC is at least as accurate as one-pass TLS on contaminated data
    ta, _, _ = tls_affine(x, y)
    assert abs(a - alpha) <= abs(ta - alpha)


def test_ransac_deterministic():
    x = np.linspace(0, 1e6, 30); y = x + 1000
    y[5] += 5e4
    r1 = ransac_affine(x, y, threshold_us=1000, seed=3)
    r2 = ransac_affine(x, y, threshold_us=1000, seed=3)
    assert r1[0] == r2[0] and r1[1] == r2[1]


def test_piecewise_beats_affine_on_kinked_clock():
    x = np.linspace(0, 120e6, 120)
    mid = 60e6
    y = np.where(x < mid, 1.00001 * x, 1.00001 * mid + 1.00005 * (x - mid)) + 1000.0
    aff = fit_affine_tls(x, y, device_id="d", ref_clock_id="r")
    pw = fit_piecewise_affine(x, y, n_segments=2, device_id="d", ref_clock_id="r")
    assert pw.residual_us < 0.5 * aff.residual_us
    assert pw.model.segments is not None and len(pw.model.segments) == 2
