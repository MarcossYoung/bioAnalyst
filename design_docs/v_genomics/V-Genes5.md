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

## Benchmark v1 cases (locked)

Stage 5 uses a small, source-pinned benchmark rather than an invented toy set. The
negative set calibrates the risk layer only; the positive case tests whether the primary
comparative axis can recover a published finding.

### Negative artifact set — risk-layer calibration

Each negative case is stored as a labeled diagnostic case with the published source,
artifact mode, affected gene/site/region identifiers where available, and the expected
risk reason(s). The harness may use cached diagnostics for CI, but the source extraction
rules below are version-pinned and auditable.

1. **NEG-ALIGN-DROSOPHILA-2011**
   - Source: Markova-Raina & Petrov, "High sensitivity to aligner and high rate of
     false positives in the estimates of positive selection in the 12 Drosophila
     genomes", Genome Research, 2011, DOI: `10.1101/gr.115949.110`.
   - Case definition: Drosophila positive-selection calls reported as aligner-sensitive
     in the paper/supplement. Each extracted gene/site call becomes one artifact case.
   - Expected signal: `alignment.result_changes_with_aligner = true` and/or low
     alignment-confidence diagnostics; final `fp_risk.tier` must be `flagged` or
     `excluded`.

2. **NEG-ALIGN-SITEWISE-2011**
   - Source: Jordan & Goldman, "The Effects of Alignment Error and Alignment Filtering
     on the Sitewise Detection of Positive Selection", Molecular Biology and Evolution,
     2011/2012 issue, DOI: `10.1093/molbev/msr272`.
   - Case definition: alignment-error / filtering-sensitive sitewise positive-selection
     detections from the paper's benchmark examples. These are method-artifact cases, not
     biological positives.
   - Expected signal: low alignment confidence, material MAFFT/PRANK or filtered/unfiltered
     disagreement where available; final `fp_risk.tier` must be `flagged` or `excluded`.

3. **NEG-GBGC-HAR-2010**
   - Source: Katzman et al., "GC-Biased Evolution Near Human Accelerated Regions",
     PLoS Genetics, 2010, DOI: `10.1371/journal.pgen.1000960`.
   - Case definition: HAR-near loci/regions where accelerated evolution is explained or
     materially confounded by GC-biased evolution. These are gBGC artifact controls for
     the risk model; noncoding cases are represented as region-linked diagnostic records
     and do not enter the ERC positive-recovery test.
   - Expected signal: `gbgc.risk = high`; final `fp_risk.tier` must be `flagged` or
     `excluded`.

4. **NEG-GBGC-GENOME-2010**
   - Source: Ratnakumar et al., "Detecting positive selection within genomes: the
     problem of biased gene conversion", Philosophical Transactions of the Royal Society
     B, 2010, DOI: `10.1098/rstb.2010.0007`.
   - Case definition: gBGC-prone selection-scan calls/classes described by the paper,
     preferentially protein-coding examples where identifiers are extractable from the
     paper or supplement.
   - Expected signal: high gBGC risk, plus recombination/hotspot annotation where
     available; final `fp_risk.tier` must be `flagged` or `excluded`.

Optional expansion after v1, not required for promotion: Bolívar et al., "Biased
Inference of Selection Due to GC-Biased Gene Conversion and the Rate of Protein Evolution
in Flycatchers When Accounting for It", Molecular Biology and Evolution, 2018, 35(10):2475,
DOI: `10.1093/molbev/msy149`.

### Positive reproducible case — primary-axis recovery

**POS-ERC-SLC30A9-2021**
- Source: "Evolutionary rate covariation identifies SLC30A9 (ZnT9) as a mitochondrial
  zinc transporter", Biochemical Journal, 2021, DOI: `10.1042/bcj20210342`.
- Why this case: it is an ERC-style comparative finding, so it exercises the Stage 3
  primary axis directly instead of resting promotion on the underpowered Stage 4
  rate-phenotype leg.
- Test input:
  - Query gene: `SLC30A9`.
  - Positive comparator set: curated mitochondrial zinc / mitochondrial transporter /
    OXPHOS-context genes extracted from the paper and its data, versioned in the benchmark.
  - Negative comparator set: matched random/control genes already used by the Stage 3
    control-set machinery.
- Recovery criterion: `SLC30A9` must rank above the predeclared percentile threshold
  against controls by ERC/mirrortree-lite rate covariation, with the same direction as the
  published finding, and no high-risk diagnostic reason may be required to make it pass.
- This positive case is **not used to tune weights**. It is a held-out recovery check
  evaluated after negative-set calibration.

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
1. **Case extraction details** — the source families and positive case are locked above;
   implementation still needs to extract exact identifiers from papers/supplements into
   `validation/benchmarks/vgenes5_v1/` with hashes and source notes.
2. **Separation metric + pass threshold** — define explicitly up front (recommend
   AUC ≥ a fixed threshold **and** positive recovered).

## Verification (this ends the project, not "the pipeline runs")
On the labeled benchmark: risk **separates** known artifacts from known-robust; the
pipeline **reproduces** the chosen published finding; weights are calibrated with stored
provenance; only then is the axis **promoted**. Spec §9 "The benchmark is met" is the
criterion that flips the axis to verdict-bearing.

## Next: [V-Genes6.md](V-Genes6.md) — HyPhy selection layer + scale.
