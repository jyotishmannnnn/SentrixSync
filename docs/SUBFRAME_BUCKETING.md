# Design Note — Sub-Frame Tactile Bucketing

**Status:** DESIGN NOTE FOR REVIEW. **Not implemented.** No code in Phase 4
applies any bucketing rule; `SubframeBuckets.rule` is recorded as a provenance
string only. This note compares the candidate approaches and recommends one for
review before implementation.

## 1. Problem

When a high-rate stream (e.g. tactile at 1600 Hz) is aligned to a lower-rate
*anchor* stream (e.g. an image stream at 30 fps), each anchor frame spans many
high-rate samples. The Data Engine's premium signal is precisely this sub-frame
burst, and the LeRobot/RLDS export contract wants it as a **fixed-shape**
`[R, U, V]` tensor per frame (Architecture Manual §5).

But the ratio is generally **non-integer**: 1600 / 30 = 53.33, so consecutive
frames legitimately contain 53 or 54 samples. The CTO Review (P2) flagged
exactly this: *"define the resampling/padding rule explicitly… don't ship a
silently variable-length 'fixed-shape' tensor."* This note picks that rule.

Notation: `R` = fixed per-frame slot count; `m_k` = the number of *real*
high-rate samples that fall in frame `k` (here 53 or 54).

## 2. Candidate Approaches

| Approach | What it does | Fidelity | Fabrication risk | Fixed shape? | Downstream simplicity |
|---|---|---|---|---|---|
| **Nearest-neighbour** | One representative sample per frame | Destroys the burst (keeps 1 of ~53) | Low (selection only) | Yes (`R=1`) | High — but defeats the entire premium-signal purpose |
| **Zero-pad** | Fill `R` slots; pad missing with zeros | Burst preserved | **High** — injects fake zeros that corrupt force magnitude/statistics | Yes | High |
| **Repeat-pad (hold)** | Fill `R` slots; pad remainder by repeating the last real sample | Burst preserved; mild flat-tail bias | Low — no fake zeros, no cross-sample interpolation | Yes | High |
| **Interpolation** | Resample each burst to exactly `R` slots | Smooth, uniform | **Medium** — fabricates intermediate values; shifts sharp contact transients in time | Yes | Medium |
| **Ragged** | Store variable-length bursts + per-frame `m_k` | Full, exact | None | **No** | Low — breaks the fixed-shape `[R,U,V]` contract |

## 3. Recommendation

> **Fixed-`R` repeat-pad with an explicit per-frame valid count `m_k`.**
> Choose `R = ceil(grid_rate_hz / anchor_fps)` (here `R = 54`). Place the `m_k`
> real samples in order, repeat the last real sample to fill slots `m_k…R-1`,
> and record `m_k` alongside each frame so consumers can ignore the padding.
> Keep **ragged** as an opt-in high-fidelity fallback for consumers that want it.

Why this one:

- **Honours "mark, don't invent."** Repeat-pad injects no fake energy (unlike
  zero-pad) and never interpolates across real samples (unlike interpolation).
  The `m_k` count makes the padding *non-deceptive* — it is explicitly marked,
  not silently fixed-shaped. This is the exact remedy the CTO Review asked for.
- **Satisfies the fixed-shape `[R,U,V]` export contract**, so LeRobot/RLDS
  emitters need no special-casing.
- **Preserves the premium burst** at full sample count for the dominant case
  (`m_k = R`) and within one sample for the boundary case.
- **Cheap and deterministic** — no resampling kernel, no numerical instability.

Rejected: nearest-neighbour (defeats the product), zero-pad (corrupts force
statistics), interpolation (fabricates transient timing — the most valuable
label). Ragged is correct but breaks the fixed-shape contract, so it is the
fallback, not the default.

## 4. Open Questions for Review

1. **`ceil` vs `floor` for `R`.** `ceil` (R=54) keeps every real sample and pads
   short frames; `floor` (R=53) would *drop* one sample on long frames — lossy.
   Recommendation: `ceil`. Confirm.
2. **Boundary frames** (first/last, partially covered) — pad with `m_k` honest
   and possibly small. Acceptable under repeat-pad; confirm no special rule
   wanted.
3. **Where `m_k` is surfaced** in each export format (a side column in LeRobot,
   a field in RLDS step metadata). Decide at export-design time.
4. **Anchor with no high-rate coverage** (a frame with `m_k = 0`, e.g. a tactile
   dropout) — must be flagged invalid (gap), never repeat-padded from a previous
   frame. This intersects the gap-rejection rule and must be honoured.

**Do not implement** until items 1–4 are reviewed and approved.
