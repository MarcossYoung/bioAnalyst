# V-Genes Stage 3 — Branch rates + ERC on a curated set

> Sibling docs: [V-Genes.md](V-Genes.md), [V-Genes2.md](V-Genes2.md).
> **Depends on:** Stage 0 (container substrate), Stage 1 (codon-MSA artifacts +
> diagnostics), Stage 2 (risk gating). This doc is **architectural** — domain risk lives
> here, so it will get its own detailed plan + exploration before execution.

## Context

Stages 1–2 made the genomic axis honest about *when not to trust a result*. Stage 3
introduces the **primary test the floor structurally cannot do**: per-branch **relative
rates** + **ERC** (evolutionary rate covariation) between the BBB and synaptic sets,
compared against matched controls. This is the "co-evolution between the two gene sets"
half of the hypothesis. The estimator changes here — from NG86 pairwise ω (a single
summary number) to **model-based per-branch relative rates on a fixed species tree**.

Not the full expanded set — a **curated ~50/50** BBB/synaptic set with matched controls
(spec §7 Stage 3). Goal: ERC produces a number-with-robustness, or honestly returns
untestable.

## Scope / locked decisions
- Estimator = **per-branch relative rates** (branch lengths free, **topology fixed** to
  the species tree) — far more stable than free-ratio ω. **Not** per-branch dN/dS.
- ERC compared against the already-specced matched controls (brain-expressed
  non-synaptic/non-BBB, endothelial non-BBB, expression-/length-/GC-matched).
- Runs in the Stage 0 container; tool versions → provenance.
- Honesty gate from Stages 1–2 governs output: too few survivors / degraded set →
  untestable → N/A.

## Design

### 3.1 Branch-rate estimator — `tools/branch_rates.py` (new)
Calls `genomics_container.run_tool` (Stage 0) to estimate branch lengths on the **fixed
species tree** per gene, on the Stage 1 codon MSA. Returns a per-gene, per-branch
**relative**-rate vector (gene-wide rate removed). Cached via the Stage 0 results cache.
Boundary stays "send alignment + tree → get rates + JSON," testable with fixtures.

### 3.2 Closes the `result_changes_with_aligner` loop
Run the estimator on **both** the MAFFT and PRANK codon MSAs (Stage 1 produced both). If
the branch-rate vector / ERC contribution changes materially between aligners, set
`result_changes_with_aligner = true` in the `GeneDiagnostics` record — which **activates
the 0.40 fp_risk weight** left dormant in Stage 2. Stage 3 therefore completes the
heaviest risk term.

### 3.3 ERC test — `tools/compute.py` (new `erc` test)
For surviving low-risk genes: build per-branch relative-rate vectors → mean set vectors →
cross-set covariation (BBB-set vs synaptic-set branch rates), residualizing tree-wide
rate consistent with `mirrortree_lite`'s existing residualization. Significance by
**permutation against the matched control sets**. Returns typed result (effect size +
CI) or `available=False` (untestable) through the existing compute contract.

### 3.4 Methodologist routing
`cross_lineage_rate_correlation` currently maps to `mirrortree_lite`; route the
set-covariation construct to the new `erc` test (keep `mirrortree_lite` as the NG86
cross-check). The deferred `phenotype_association`/RERconverge slot stays for Stage 4.

### 3.5 Curated ~50/50 set + controls
A curated BBB/synaptic gene list + matched-control fixture (new data file), **not** full
expansion. Source from the existing SynGO synaptic set + a curated BBB list + the specced
controls in `tools/gene_sets.py`.

### 3.6 Robustness
Reuse Stage 2 robustness wiring: leave-one-out + drop medium-risk genes + drop each
high-leverage gene; the ERC verdict must survive.

## Files (anticipated)
`tools/branch_rates.py` (new) · `tools/compute.py` (`erc`) · `tools/genomic_data.py`
(assemble per-branch rate vectors) · `agents/methodologist.py` (route `erc`) ·
`agents/analyst.py` (invoke branch-rate estimation) · curated-set data file ·
`tools/diagnostics.py` (populate `result_changes_with_aligner`) · `events.py` ·
frontend ERC panel · tests + fixtures (synthetic rate vectors with known answers).

## Open decisions (resolve when we plan Stage 3)
1. **IQ-TREE vs HyPhy** for branch rates — recommend **IQ-TREE** (stable branch-length
   estimation, fast, fixed topology); HyPhy reserved for the Stage 6 selection layer.
2. **Curated set composition + source** — exact ~50/50 list and the matched-control
   construction.
3. **ERC method** — classic pairwise branch-rate correlation (Clark/Sackton-style) vs
   partial-correlation on residualized relative rates; recommend the latter for
   consistency with `mirrortree_lite`.
4. The fixed **species tree** source for the mammal panel (topology + which calibration).

## Verification
ERC on the curated set yields a number-with-robustness **or** an honest untestable;
`result_changes_with_aligner` now populated; controls demonstrably separate signal from
noise; unit tests on synthetic rate vectors with a known covariation answer.

## Next: [V-Genes4.md](V-Genes4.md) — RERconverge + complexity axis.
