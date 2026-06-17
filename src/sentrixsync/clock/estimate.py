"""Clock estimation — the inverse of the forward model.

Given matched (t_local, t_ref) pairs from SyncEvents, estimate a device's affine
clock model (offset and skew/drift) to reference time, with a fit residual and a
derived clock-confidence. Two estimators:

  * `fit_offset` — offset only (alpha fixed = 1).
  * `fit_affine` — offset + skew (the affine-drift estimator), with optional
    single-pass robust outlier rejection.

Reference: t_ref = alpha * t_local + beta. The reference device is identity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..core.timeline import ClockModel
from ..core.types import EvidenceTier, require

# Residual scale (microseconds) for the confidence heuristic.
_CONF_SCALE_US = 1000.0


@dataclass
class FitResult:
    model: ClockModel
    residual_us: float
    n_events: int


def _confidence(residual_us: float, n: int) -> float:
    """Heuristic clock-confidence in [0,1] (ESTIMATED): rises with event count,
    falls with fit residual. Not a calibrated probability."""
    if n <= 0:
        return 0.0
    count_term = 1.0 - 1.0 / (n + 1)
    resid_term = math.exp(-max(residual_us, 0.0) / _CONF_SCALE_US)
    return float(max(0.0, min(1.0, count_term * resid_term)))


def clock_confidence(residual_us: float, n: int) -> float:
    """Public alias for the clock-confidence heuristic (used by the graph layer)."""
    return _confidence(residual_us, n)


def _rms(residuals: np.ndarray) -> float:
    if residuals.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(residuals ** 2)))


def fit_offset(t_local: np.ndarray, t_ref: np.ndarray, *, device_id: str,
               ref_clock_id: str, method: EvidenceTier | None = None) -> FitResult:
    tl = np.asarray(t_local, dtype=float)
    tr = np.asarray(t_ref, dtype=float)
    require(tl.shape == tr.shape and tl.ndim == 1, "t_local/t_ref must be equal-length 1-D")
    require(tl.size >= 1, "offset estimation needs at least one matched event")
    beta = float(np.mean(tr - tl))
    resid = (tl + beta) - tr
    rms = _rms(resid)
    model = ClockModel(device_id=device_id, ref_clock_id=ref_clock_id, alpha=1.0,
                       beta_us=beta, method=method, fit_residual_us=rms,
                       n_events=int(tl.size), clock_confidence=_confidence(rms, tl.size))
    return FitResult(model=model, residual_us=rms, n_events=int(tl.size))


def fit_affine(t_local: np.ndarray, t_ref: np.ndarray, *, device_id: str,
               ref_clock_id: str, method: EvidenceTier | None = None,
               robust: bool = True, reject_sigma: float = 3.0) -> FitResult:
    """Least-squares affine fit t_ref ≈ alpha*t_local + beta.

    With < 2 events (or zero-variance t_local) it falls back to offset-only. With
    `robust`, performs one pass of residual-based outlier rejection (> reject_sigma
    * RMS) and refits if any inliers remain.
    """
    tl = np.asarray(t_local, dtype=float)
    tr = np.asarray(t_ref, dtype=float)
    require(tl.shape == tr.shape and tl.ndim == 1, "t_local/t_ref must be equal-length 1-D")
    require(tl.size >= 1, "affine estimation needs at least one matched event")

    def _ls(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
        xm, ym = x.mean(), y.mean()
        var = float(((x - xm) ** 2).sum())
        if x.size < 2 or var == 0.0:
            return 1.0, float(ym - xm)          # fall back to offset-only
        alpha = float(((x - xm) * (y - ym)).sum() / var)
        beta = float(ym - alpha * xm)
        return alpha, beta

    alpha, beta = _ls(tl, tr)
    resid = (alpha * tl + beta) - tr
    rms = _rms(resid)

    if robust and tl.size >= 3 and rms > 0.0:
        keep = np.abs(resid) <= reject_sigma * rms
        if keep.any() and not keep.all():
            alpha, beta = _ls(tl[keep], tr[keep])
            resid = (alpha * tl + beta) - tr        # residual reported over ALL events
            rms = _rms(resid)

    model = ClockModel(device_id=device_id, ref_clock_id=ref_clock_id, alpha=alpha,
                       beta_us=beta, method=method, fit_residual_us=rms,
                       n_events=int(tl.size), clock_confidence=_confidence(rms, tl.size))
    return FitResult(model=model, residual_us=rms, n_events=int(tl.size))


def tls_affine(x: np.ndarray, y: np.ndarray, *, robust: bool = True,
               reject_sigma: float = 3.0) -> tuple[float, float, float]:
    """Total-least-squares affine fit y ≈ alpha*x + beta (orthogonal regression).

    Unlike OLS, TLS accounts for noise in BOTH variables — appropriate when the
    two clocks being related are each observed with error (the real case; OLS's
    exact-reference assumption no longer holds). Returns (alpha, beta, rms) where
    rms is the vertical-residual RMS for comparability. Falls back to offset-only
    when there are < 2 points or no x-variance/covariance.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    require(x.shape == y.shape and x.ndim == 1, "x/y must be equal-length 1-D")
    require(x.size >= 1, "tls_affine needs at least one point")

    def _fit(xx: np.ndarray, yy: np.ndarray) -> tuple[float, float]:
        xm, ym = xx.mean(), yy.mean()
        xc, yc = xx - xm, yy - ym
        sxx = float((xc * xc).sum())
        syy = float((yc * yc).sum())
        sxy = float((xc * yc).sum())
        if xx.size < 2 or sxy == 0.0:
            return 1.0, float(ym - xm)          # offset-only fallback
        alpha = (syy - sxx + math.sqrt((syy - sxx) ** 2 + 4.0 * sxy ** 2)) / (2.0 * sxy)
        return float(alpha), float(ym - alpha * xm)

    alpha, beta = _fit(x, y)
    resid = (alpha * x + beta) - y
    rms = _rms(resid)
    if robust and x.size >= 3 and rms > 0.0:
        keep = np.abs(resid) <= reject_sigma * rms
        if keep.any() and not keep.all():
            alpha, beta = _fit(x[keep], y[keep])
            resid = (alpha * x + beta) - y
            rms = _rms(resid)
    return alpha, beta, rms


def fit_affine_tls(t_local: np.ndarray, t_ref: np.ndarray, *, device_id: str,
                   ref_clock_id: str, method: EvidenceTier | None = None) -> FitResult:
    alpha, beta, rms = tls_affine(np.asarray(t_local, float), np.asarray(t_ref, float))
    n = int(np.asarray(t_local).size)
    model = ClockModel(device_id=device_id, ref_clock_id=ref_clock_id, alpha=alpha,
                       beta_us=beta, method=method, fit_residual_us=rms,
                       n_events=n, clock_confidence=_confidence(rms, n))
    return FitResult(model=model, residual_us=rms, n_events=n)


def ransac_affine(x: np.ndarray, y: np.ndarray, *, threshold_us: float = 1000.0,
                  n_iters: int = 200, seed: int = 0,
                  refine: bool = True) -> tuple[float, float, float, np.ndarray]:
    """RANSAC affine fit robust to gross outliers (false-positive / mis-associated
    events). Samples minimal 2-point models, scores inliers within `threshold_us`,
    then refits the best consensus set with TLS. Returns (alpha, beta, rms,
    inlier_mask). Deterministic given `seed`."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    require(x.shape == y.shape and x.ndim == 1, "x/y must be equal-length 1-D")
    n = x.size
    if n < 3:
        a, b, rms = tls_affine(x, y)
        return a, b, rms, np.ones(n, dtype=bool)

    rng = np.random.default_rng(seed)
    best_mask = None
    best_count = -1
    for _ in range(n_iters):
        i, j = rng.choice(n, size=2, replace=False)
        if x[i] == x[j]:
            continue
        alpha = (y[j] - y[i]) / (x[j] - x[i])
        beta = y[i] - alpha * x[i]
        mask = np.abs(alpha * x + beta - y) <= threshold_us
        c = int(mask.sum())
        if c > best_count:
            best_count, best_mask = c, mask

    if best_mask is None or best_mask.sum() < 2:
        a, b, rms = tls_affine(x, y)
        return a, b, rms, np.ones(n, dtype=bool)

    if refine:
        a, b, _ = tls_affine(x[best_mask], y[best_mask])
        # re-evaluate inliers after refinement
        best_mask = np.abs(a * x + b - y) <= threshold_us
        a, b, _ = tls_affine(x[best_mask], y[best_mask])
    else:
        a, b, _ = tls_affine(x[best_mask], y[best_mask])
    rms = _rms((a * x[best_mask] + b) - y[best_mask])
    return a, b, rms, best_mask


def fit_piecewise_affine(t_local: np.ndarray, t_ref: np.ndarray, *, n_segments: int,
                         device_id: str, ref_clock_id: str,
                         method: EvidenceTier | None = None) -> FitResult:
    """Segmented (piecewise) affine clock model for nonlinear drift over long
    sessions. Splits the (sorted) evidence into `n_segments` equal-count time
    segments and fits each with TLS. Affine remains the default elsewhere; this
    is opt-in."""
    x = np.asarray(t_local, dtype=float)
    y = np.asarray(t_ref, dtype=float)
    require(x.shape == y.shape and x.ndim == 1, "t_local/t_ref must be equal-length 1-D")
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    n = xs.size
    k = max(1, min(int(n_segments), n // 2))      # >= 2 points per segment
    edges = np.linspace(0, n, k + 1).astype(int)

    _LO, _HI = -1e18, 1e18
    segments: list[dict] = []
    for s in range(k):
        lo, hi = edges[s], edges[s + 1]
        a, b, _ = tls_affine(xs[lo:hi], ys[lo:hi])
        t_lo = _LO if s == 0 else float((xs[lo - 1] + xs[lo]) / 2.0)
        t_hi = _HI if s == k - 1 else float((xs[hi - 1] + xs[hi]) / 2.0)
        segments.append({"t_lo_us": t_lo, "t_hi_us": t_hi, "alpha": float(a), "beta_us": float(b)})

    model = ClockModel(device_id=device_id, ref_clock_id=ref_clock_id,
                       alpha=segments[0]["alpha"], beta_us=segments[0]["beta_us"],
                       segments=segments, method=method)
    pred = np.array([model.to_reference(int(t)) for t in x], dtype=float)
    rms = _rms(pred - y)
    model.fit_residual_us = rms
    model.n_events = int(n)
    model.clock_confidence = _confidence(rms, n)
    return FitResult(model=model, residual_us=rms, n_events=int(n))


def identity_model(device_id: str, ref_clock_id: str) -> ClockModel:
    """The reference device's own model: t_ref == t_local."""
    return ClockModel(device_id=device_id, ref_clock_id=ref_clock_id, alpha=1.0,
                      beta_us=0.0, fit_residual_us=0.0, clock_confidence=1.0)
