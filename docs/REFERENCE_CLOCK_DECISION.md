# SentrixSync — Reference-Clock Policy Decision

**Status:** Approved architecture, pre-implementation. **Decision record.**
**Question:** When no hardware grandmaster exists, how does SentrixSync choose the reference clock that the unified timeline is expressed in?
**Companion documents:** [`ARCHITECTURE.md`](./ARCHITECTURE.md), [`CONTRACT.md`](./CONTRACT.md), [`SESSION_SCHEMA.md`](./SESSION_SCHEMA.md).

---

## 1. The Problem

The unified timeline must be expressed in *some* time base. If a physical master clock exists (PTP grandmaster, GPS-disciplined oscillator), the choice is trivial: use it. The open question is the common case for body-worn, wireless, and synthetic setups where **no device's clock is privileged by hardware**.

Two families of policy answer this:

- **Designated-anchor** — pick one device's clock to *be* the reference; fit every other device to it.
- **Virtual-consensus** — define a synthetic reference time that best fits *all* devices jointly (e.g. least-squares consensus), and fit every device, including the "best" one, to that abstract base.

"Reference time" is in any case only defined up to a global affine transform — no choice is more physically "true" than another. The decision is therefore about **engineering properties**, not physics.

---

## 2. The Two Approaches

### 2.1 Designated-anchor

One device is named the reference (`role: reference`). Its raw clock *is* reference time (`alpha = 1, beta = 0` for it by definition). Every follower gets an affine clock model fit against the anchor's clock.

Selection rule (deterministic, in priority order):
1. a hardware master, if present;
2. else the `reference_candidate` device with the highest nominal rate;
3. else the most-trusted clock (best declared evidence tier / lowest declared skew);
4. else a stable tie-break (e.g. lexicographic `device_id`).

### 2.2 Virtual-consensus

A synthetic timeline is constructed so that the *sum of weighted residuals across all devices* is minimized. No real clock is the reference; all devices, including the most accurate, carry a non-identity clock model mapping them to the abstract consensus.

---

## 3. Evaluation

| Criterion | Designated-anchor | Virtual-consensus |
|---|---|---|
| **Complexity** | Low. One device is identity; followers are independent affine fits. No global optimization. | Higher. Requires a joint estimator, a weighting scheme, and a definition of the abstract base; couples all devices' fits together. |
| **Robustness** | High and *legible*. A bad follower fit affects only that follower. Failure is localized and obvious. | Lower in failure modes. One bad device can shift the consensus and silently degrade *every* device's mapping; failures are global and harder to attribute. |
| **Determinism / reproducibility** | Strong. Selection rule is deterministic; results are stable run-to-run and easy to diff. | Weaker. Joint solutions can move with weighting, initialization, or the addition/removal of a device. |
| **Interpretability of residuals** | Direct. `sync_resid_us` for a follower is "disagreement with the anchor" — exactly the Data Engine's `sessions.sync_resid_us` and Phase-7 gate semantics. | Indirect. Residuals are measured against an abstract base no instrument observes; harder to explain to a downstream consumer or in a data card. |
| **Validation against ground truth** | Clean. In synthetic round-trips, recovered follower `(alpha, beta)` compares directly to the injected truth. | Messy. Ground truth must first be projected into the consensus frame, adding a confounding transform to every accuracy number. |
| **N=1 behaviour** | Trivially correct — the one device is the reference. | Degenerate — consensus over one device is just that device, i.e. it collapses back to designated-anchor anyway. |
| **Maintainability** | One small, well-understood code path; few moving parts to test. | A joint solver is a standing maintenance and numerical-stability burden. |
| **Theoretical optimality** | Slightly sub-optimal: total error is referenced to one (imperfect) clock rather than balanced. | Marginally better *average* residual when many comparable-quality clocks are present. |
| **Fit for Sentrix today** | Excellent. Sessions are small (1–4 devices), one clock is usually clearly best (the glove hub), and legibility matters more than shaving microseconds. | Over-built for current scale; its only real advantage appears at many co-equal clocks, which Sentrix does not have. |

---

## 4. Recommendation for v0.3

> **Adopt designated-anchor.** Select the reference deterministically by the priority rule in §2.1. Express the unified timeline in the anchor's clock. Fit every follower with an independent affine model and report each follower's residual as its disagreement with the anchor.

This is the lower-regret choice on every axis Sentrix actually cares about right now: it is simpler to build and test, its failures are localized and explainable, it produces residuals that mean exactly what the Data Engine's QA gates expect, it validates cleanly against synthetic ground truth, and it degrades perfectly to the N=1 and "hardware-master-present" cases.

### Why this fits the CTO philosophy

- **Build the minimum.** Virtual-consensus buys a marginal average-residual improvement that only materializes with many co-equal clocks — a situation Sentrix does not have and may never have. Building a joint solver now is precisely the speculative over-engineering the CTO Review (§4.4) warns against.
- **Falsifiable quality.** The CTO Review (§4.3) demands measured accuracy. Designated-anchor makes the residual a direct, instrument-grounded number and makes synthetic round-trip accuracy unambiguous. Consensus residuals, measured against an unobservable base, are exactly the kind of internally-consistent-but-not-grounded metric the review criticizes.
- **Operational legibility.** A small team can reason about "everything is relative to the glove hub" far more easily than about a drifting abstract consensus — and legibility is a stated survival requirement for a small team.

### Practical notes for the v0.3 design

- The anchor is recorded explicitly in every Session (`reference_clock_id`, `reference_selection`), so the choice is always auditable.
- When a hardware master *is* present, the same machinery applies — the master is simply the anchor with the best evidence tier. No separate code path.
- The anchor's own clock imperfections are not corrected (it defines the reference). This is acceptable and standard; if the anchor is later found to be the worst clock, the selection rule should have avoided it — which is why "most-trusted clock" is a selection criterion.

---

## 5. Migration Path (if Sentrix later needs a different strategy)

The decision is intentionally reversible. Three signals would justify revisiting it, and each has a clean migration:

1. **Many co-equal clocks appear** (e.g. a multi-camera rig with no dominant device, several body-worn nodes of similar quality). *Migration:* introduce a virtual-consensus reference *selection policy* alongside the existing one. Because the reference choice is already isolated behind a policy (recorded as `reference_clock_policy` in the Session), this is an additive change — the estimator, timeline, and metrics stages are unaffected; only the production of the reference time base changes.

2. **A hardware grandmaster becomes standard** (PTP across the rig). *Migration:* none needed — designated-anchor already prefers a hardware master. The residuals simply shrink toward sub-microsecond and `sync_method` becomes `hardware_ptp`.

3. **Cross-session global time is required** (aligning many sessions to one wall-clock-anchored timeline). *Migration:* add a session-to-session reference layer *above* the per-session anchor; the per-session policy is untouched. This is a new outer concern, not a change to the inner one.

Design guard-rails that keep migration cheap (to be honored in implementation):

- The reference-clock policy is a **named, swappable selection step** that emits a `reference_clock_id` and a `reference_selection` rationale — never an assumption baked into the estimator or timeline modules.
- Residuals and confidences are always expressed **relative to the recorded reference**, so a future change of reference re-expresses them without changing their definition.
- The Session manifest records the policy used, so datasets produced under different policies remain self-describing and comparable.

---

## 6. Decision Summary

| | |
|---|---|
| **Decision** | Designated-anchor reference clock for v0.3. |
| **Selection** | Deterministic priority: hardware master → highest-rate candidate → most-trusted clock → stable tie-break. |
| **Rationale** | Lowest complexity, localized/legible failures, instrument-grounded residuals matching Data Engine QA semantics, clean synthetic validation, perfect N=1 degradation. |
| **Rejected** | Virtual-consensus — over-built for current 1–4 device sessions; advantage only at many co-equal clocks; residuals harder to ground and validate. |
| **Reversibility** | High. Reference selection is isolated behind a recorded policy; consensus can be added later as an additive policy without touching estimation, timeline, or metrics. |
