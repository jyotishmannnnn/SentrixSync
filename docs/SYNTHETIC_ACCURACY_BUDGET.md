# Synthetic Accuracy Budget — CI Validation Targets

**Status:** Defined for the (deferred) synchronization-estimation phase.
**These are CI validation thresholds, NOT customer-facing performance
guarantees.** They state how accurately SentrixSync's estimator must recover a
*known, injected* clock relationship in synthetic scenarios — the round-trip
accuracy that the forward/inverse duality makes measurable. They will be applied
by the metrics stage once it exists; nothing enforces them yet.

## 1. What these targets are (and are not)

- **Are:** pass/fail gates for the synthetic scenario regression suite. They
  answer *"did the estimator recover the offset/skew we injected, within
  tolerance?"* against the segregated ground-truth clock model.
- **Are not:** claims about real-hardware synchronization quality, and not the
  Data Engine release/certified gates (those — `< 2 ms` release, `< 0.5 ms`
  certified, `≥ 5 ms` hard fail — apply to a *session's* measured residual, real
  or synthetic). A scenario may pass the Data Engine gate yet fail its CI budget
  if the estimator was lucky rather than accurate.

## 2. Metrics

| Metric | Definition |
|---|---|
| `alpha_err` | \|recovered α − injected α\| (skew error, dimensionless) |
| `beta_err_us` | \|recovered β − injected β\| (offset error, microseconds) |
| `alignment_rmse_us` | RMS over all samples of \|recovered t_ref − true t_ref\| |
| `dropout_err` | \|estimated dropout fraction − injected loss fraction\| |
| `coverage_min` | minimum per-stream valid coverage on the grid |

`alpha`/`beta` ground truth comes from the synthetic `ground_truth` block, which
the estimator never sees (CONTRACT.md §9).

## 3. Targets by scenario

These map to `configs/scenarios/`. They are deliberately loose at first (correct
before tight) and will be ratcheted as the estimator matures.

| Scenario | Conditions | `alpha_err` | `beta_err_us` | `alignment_rmse_us` | `dropout_err` |
|---|---|---|---|---|---|
| `clean` | fixed offset, no skew/jitter/loss | ≤ 1e-6 | ≤ 50 | ≤ 100 | n/a |
| `dual_device_offset` | offset + 18 ppm skew + 300 µs jitter + 1% loss | ≤ 5e-5 | ≤ 500 | ≤ 600 | ≤ 0.005 |
| *(future)* `heavy_drift` | piecewise/segmented drift | TBD when piecewise lands | TBD | TBD | — |
| *(future)* `lossy_bursty` | Gilbert-model burst loss | — | — | TBD | ≤ 0.01 |

Notes:

- The `dual_device_offset` `alignment_rmse_us` target (600 µs) sits comfortably
  inside the Data Engine **release** band (2 ms) but outside **certified**
  (0.5 ms) — synthetic scenarios are not expected to be certified-grade; that
  bar is for clean, well-synced captures.
- `clean` is the estimator sanity check: near-exact recovery is required because
  there is nothing to confound it. A `clean` failure is a code bug, not a
  tolerance question.

## 3a. Multimodal scenarios (per-hop budget)

For graph-reconciled multimodal scenarios (subset-aware association, no device
observes all events), accuracy is budgeted **per reconciliation hop** — devices
reached transitively through intermediates accumulate error and are held to a
looser bound. Verified on `mm_5device` (5 devices, 3 event groups, glove
reference; camera/mocap reached via `imu` at 2 hops).

| Tier | Condition | `alpha_err` | `beta_err_us` | `alignment_rmse_us` |
|---|---|---|---|---|
| Direct | hops == 1 | ≤ 8e-5 | ≤ 500 | ≤ 500 |
| Transitive | hops >= 2 | ≤ 2e-4 | ≤ 1500 | ≤ 1500 |

Achieved on `mm_5device`: direct devices (imu, audio) ~6e-6–2.4e-5 α-error;
transitive devices (camera, mocap) ~1.3e-5–5.5e-5 α-error, ≤ 151 µs RMSE — all
PASS. A device sharing no events with any other is reported **unreachable**
(identity, confidence 0), never an error.

## 4. Process

- Targets live here and are versioned with the repo.
- When the estimator + metrics stages are implemented, the scenario regression
  suite asserts each metric against these targets and fails CI on breach.
- Tightening a target is a deliberate PR (with the supporting measurement),
  never a silent edit.
- Adding a scenario requires adding its row here first.

**No estimator or metric is implemented yet.** This document defines the bar the
deferred work must clear.
