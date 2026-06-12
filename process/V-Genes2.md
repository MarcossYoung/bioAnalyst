# V-Genes Stage 2 — Pre-flight risk score + honesty-gate wiring

> Sibling docs: [V-Genes.md](V-Genes.md) (Stage 0+1), V-Genes3–6.md (later stages).
> **Depends on:** Stage 1 (`GeneDiagnostics` records exist but gate nothing).

## Context

Stage 1 produces a per-gene `GeneDiagnostics` record (`tools/diagnostics.py`) that is
computed and attached to the analyst result but is **read by nothing** —
`_set_statistics`, `mirrortree_lite`, and `skeptic._apply_guardrails` all ignore it.

Stage 2 turns that record into a **decision**. It implements the spec §3 pre-flight risk
score and wires the three risk tiers into the engine's **existing** honesty machinery so
that — in the spec's words (§7 Stage 2) — *the engine refuses the right genes for named
reasons*. No ERC/RERconverge yet (Stage 3+); the primary test is still the floor's
NG86-based `mirrortree_lite`. The §6 calibration has not happened, so the weights ship as
a **heuristic, not calibrated**, and every genomic output must say so.

## Scope / locked decisions
- Implement `fp_risk` exactly as spec §3 (weights are the starting heuristic).
- Gate three tiers: `<0.25` contributes normally · `0.25–0.50` flagged + robustness-tested
  · `≥0.50` excluded → contributes to N/A, never a number.
- **`result_changes_with_aligner` stays `null` until Stage 3** (it needs the primary test
  run under each aligner, which doesn't exist yet). Its 0.40 weight is therefore the one
  heavy term that cannot fire in Stage 2. **Recommended handling:** treat `null` as
  *"weight not applicable"* (skip the term), NOT as `False` — a `null` must never be read
  as "the call is alignment-stable." Document this in the record and in `fp_risk`.
  *(Open decision below.)*

## Design

### 2.1 `fp_risk(d) -> tuple[float, list[str]]` — `tools/diagnostics.py`
Pure function of input properties, per spec §3. Module-level named constants for every
weight and `NG86_DIVERGENCE_FLOOR`. Returns `(risk in [0,1], reasons)`. A `tier(risk)`
helper returns `contributes | flagged | excluded`. Unit-tested to reproduce the spec's
arithmetic on hand-built records.

### 2.2 Per-gene exclusion → rate vectors
Genes with `risk ≥ 0.50` are dropped from the rate vectors **before** the test, with the
reason retained. Implement as a filter in `tools/genomic_data.per_gene_rate_vectors`
(or a pre-step feeding it), reusing the existing `sets`/`rates` shape.

### 2.3 Set-level risk-survival → existing usability gate
Extend the floor's per-set usability (`analyst._set_usability`, surfaced as
`rate_vectors.set_usability`) with a **`risk_degraded`** flag alongside the existing
`dnds_degraded`: if a set has fewer than `min_low_risk_genes` survivors (config), the set
is degraded → `cross_set_allowed = False`. `mirrortree_lite` (`tools/compute.py:733`)
already honours `set_usability` and skips with a reason — add the skip reason
*"set X degraded: too few genes survive FP-risk filter."*

### 2.4 Honesty gate — `skeptic._apply_guardrails`
The gate already forces `scores["genomic_evidence_alignment"] = None` (skeptic.py ~199)
on `compute.untestable` / `analyst_interp` untestable / `dnds_saturation`. Add a **named
reason** for the risk path so the nulled axis says *"risk filter left too few scorable
genes"* — distinct from saturation and from "no test ran." Distinguishable named reasons
are a Stage 2 success criterion (spec §9).

### 2.5 Robustness — medium-risk drop
`leave_one_out` (`tools/compute.py:1144`) already perturbs by dropping genes. Add the
`0.25–0.50` (flagged) genes as an extra perturbation set: re-run and confirm the verdict
survives dropping medium-risk genes (spec §3 tier behaviour).

### 2.6 Disclaimer + provenance
- Every genomic output carries *"FP-risk weights are a heuristic, not yet calibrated
  (see Stage 5)."* Thread through interpreter/skeptic genomic text and the frontend
  `GenomicPanel`.
- Record the `fp_risk` weight vector + a `calibration_state: "heuristic"` field in
  `data_provenance` (via `make_provenance`) so a result is reproducible and its
  calibration status is explicit.

### 2.7 Events
`diagnostics_risk_scored(gene, risk, tier, reasons)` and a per-set risk-survival summary
event (mirror the `rdnds_*` / `diagnostics_*` factories in `events.py`).

## Files
**Modified:** `tools/diagnostics.py` (fp_risk + tier + constants) · `tools/genomic_data.py`
(drop risk≥0.50; risk-survival in set_usability) · `agents/analyst.py` (`_set_usability`
gains `risk_degraded`; thread tags) · `tools/compute.py` (`mirrortree_lite` skip reason;
`leave_one_out` medium-risk perturbation) · `agents/skeptic.py` (honesty-gate named
reason) · `agents/interpreter.py` (surface exclusions + disclaimer) · `events.py` ·
`provenance`/`genomic_data.py` (weights + calibration_state) · `frontend` GenomicPanel
(flagged/excluded genes + disclaimer).
**New tests:** `backend/tests/test_fp_risk.py` (+ extend diagnostics/skeptic tests).

## Open decisions (resolve when we plan Stage 2 in detail)
1. **`result_changes_with_aligner = null` handling** — recommended: skip the 0.40 term
   (weight not applicable) until Stage 3; alternative: ship a cheap proxy now.
2. **`min_low_risk_genes` per-set threshold** — what survivor count makes a set scorable.
3. Whether flagged (medium-risk) genes appear in the report as "flagged, not scored" by
   default or only on drill-down.

## Verification
- Unit: `fp_risk` reproduces spec §3 arithmetic on hand-built records; a `risk≥0.50`
  record is excluded with reasons; a set with too few survivors → axis N/A with the new
  named reason; existing **86-test suite still green**.
- By hand (spec §9 "Risk gating is honest"): run on the 8 starter genes — confirm
  high-risk genes are excluded with **distinguishable** named reasons, and the axis
  renders **N/A** when too few survive or a set is degraded — never a fabricated score.

## Next: [V-Genes3.md](V-Genes3.md) — branch rates + ERC.
