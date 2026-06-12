# V-Genes Stage 5 — Validation harness + weight calibration (the milestone)

> Sibling docs: [V-Genes.md](V-Genes.md), [V-Genes2.md](V-Genes2.md), [V-Genes3.md](V-Genes3.md), [V-Genes4.md](V-Genes4.md).
> **Depends on:** Stages 1–4 fully working. This is the gate that promotes the genomic
> axis from *experimental* to *verdict-bearing* (spec §6, §8.6).

## Context

"More reliable than a postgrad" is an empirical claim and requires evidence. Until this
stage passes, **no real-user verdict rests on the genomic score.** The evidence exists in
the literature: many published positive-selection calls were later shown to be alignment
or gBGC artifacts. That labeled set is the benchmark.

The milestone (spec §6) — not "HyPhy runs," not "ERC computes" — is: **on a labeled
benchmark, the risk layer separates known artifacts from known-robust results, AND the
primary test reproduces a published comparative finding.**

## Scope / locked decisions
- **Negative set:** documented false-positive selection calls — alignment-driven (e.g.
  the Drosophila alignment-sensitivity cases) and gBGC-driven. The harness must **flag
  these high-risk.** If it scores them low-risk, the §3 risk model is empty and the
  postgrad claim is unearned.
- **Positive set:** a published comparative result that can be reproduced — a known
  gene/lineage with robust, replicated positive selection or a known rate-phenotype
  association. The pipeline must **recover it.**
- **Calibration:** tune the §3 `fp_risk` weights against the negative set to maximize
  separation; only then do the weights stop being a bare heuristic.
- **Promotion:** only after the benchmark passes may the axis carry a verdict.

## Design

### 5.1 Harness — `validation/` (new)
A runner + labeled datasets + a sources doc that executes the full Stage 1–4 pipeline over
the labeled positive/negative sets and reports: negative → high risk (flagged), positive →
recovered. Stored, reproducible, version-pinned datasets.

### 5.2 Calibration
`fp_risk` weights move from module constants to a **calibrated, config-driven table**
tuned against the negative set (maximize artifact/robust separation). Flip the
`calibration_state` provenance field from `"heuristic"` to
`"calibrated against N cases (date, dataset version)"` and flip the report disclaimer.
**Set the metric + pass threshold *before* running** to avoid post-hoc tuning.

### 5.3 Axis promotion flag
A config flag that, once the benchmark passes, allows the genomic axis to carry a verdict
(removes the experimental label). `skeptic._apply_guardrails` reads it: **before
promotion the genomic score is advisory only**; after, it is verdict-bearing. This makes
spec §8.6 a mechanical gate, not a judgement call.

### 5.4 Metrics + provenance
Separation statistic (e.g. AUC of risk between artifact vs robust) + recovery of the
positive result. Store calibration provenance (dataset versions, weights, date, threshold).

## Files (anticipated)
`validation/` runner + labeled datasets + sources doc · `tools/diagnostics.py` (weights
become calibrated/config-driven) · config (calibration table + axis-promotion flag) ·
`agents/skeptic.py` (read promotion flag) · provenance (calibration state + date +
dataset versions) · report (flip disclaimer) · tests.

## Open decisions (resolve when we plan Stage 5 — these are the real work)
1. **Negative-set curation** — which specific artifact papers/cases (start from the
   alignment-sensitivity + gBGC false-positive literature the spec gestures at). A
   literature-sourcing task.
2. **Positive-set choice** — one tractable, well-documented published result to reproduce.
   Favor an **ERC/selection** result over a rate-phenotype association: the Stage 4
   RERconverge leg is underpowered (~15–25 species, primate-confounded), so a rate-phenotype
   target would make the benchmark rest on the weakest part of the pipeline.
3. **Separation metric + pass threshold** — define explicitly up front (recommend
   AUC ≥ a fixed threshold **and** positive recovered).

## Verification (this ends the project, not "the pipeline runs")
On the labeled benchmark: risk **separates** known artifacts from known-robust; the
pipeline **reproduces** the chosen published finding; weights are calibrated with stored
provenance; only then is the axis **promoted**. Spec §9 "The benchmark is met" is the
criterion that flips the axis to verdict-bearing.

## Next: [V-Genes6.md](V-Genes6.md) — HyPhy selection layer + scale.
