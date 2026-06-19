# PAML positive-selection models — site (M7/M8) + branch-site (Model A)

**Status:** Design only (no code yet)
**Date:** 2026-06-19
**Prompted by:** Jeffares et al. 2015, *A Beginner's Guide to Estimating the
Non-synonymous to Synonymous Rate Ratio of all Protein-Coding Genes in a Genome*
(Methods Mol Biol 1201, ch. 4).

## Context

The genomic compute layer estimates dN/dS only with **PAML branch model 2**
(`tools/paml.py:run_branch_model` → null `model=0` vs alt `model=2`, `NSsites=0`).
Jeffares 2015 — the canonical genome-scale dN/dS guide — ratifies most of our
stack (CODEML ML, PAL2NAL codon alignment, saturation gating, BH correction,
Mann–Whitney group tests) but exposes one substantive gap: **branch model 2
detects a lineage-wide ω *shift*, not site-level positive selection.**
Gene-wide / branch-wide average ω rarely exceeds 1 even under genuine positive
selection because most codons stay under purifying selection. The field standard
for *detecting adaptation* is therefore **site models (M7 vs M8)** and
**branch-site Model A** (ω varies by site *and* branch, with Bayes Empirical
Bayes posteriors flagging the selected codons). Any "gene X is under positive
selection" claim is currently answered with a weaker test than the literature
standard.

A second, smaller defect the paper exposes: `_paml_branch_model`
(`tools/compute.py:562`) declares significance from the single **min** per-gene
p-value with **no multiple-testing correction** across genes — a cherry-pick the
paper explicitly warns against at genome scale.

Goal: add site + branch-site CODEML models behind the existing
`paml.py` / `TEST_LIBRARY` / methodologist seam, and correct the gene-family
p-values with Benjamini–Hochberg. Preserve the graceful-degradation contract —
every path returns a typed status dict; absence of the `codeml` binary ⇒
`available=False`, never a crash.

## Decisions

- **Runtime scope:** compute branch + site + branch-site **eagerly for all
  starter genes** (matches the current pre-plan fetch design, where PAML runs in
  the analyst stage before the methodologist picks tests), gated by `[paml]`
  config flags, both new models **on by default**. The 90-day codeml cache and
  per-gene timeout absorb the added cost.
- **FDR:** apply **Benjamini–Hochberg across per-gene p-values** for branch,
  site, and branch-site consumers; significance requires adjusted p<0.05. This
  also repairs the existing branch-model cherry-pick (existing branch-model
  tests will be updated to expect adjusted p).

## Plan

### 1. `backend/nullifier/tools/paml.py` — new control writers, parsers, runners

- **Generalize `_write_control`** (currently hardcodes `NSsites=0`) to accept
  `nssites`, `ncatg`, `fix_omega`, `omega`. Reuse `_write_phylip`,
  `_label_newick`, `_run_codeml`, `_parse_lnl`, `_find_codeml`, the SQLite cache,
  and the `_FOREGROUND_GROUPS` / `_MAMMAL_SPECIES` sets unchanged.
- **Site models (no foreground):** `run_site_model(ensembl_id, gene_symbol,
  aligned, use_cache)` — M7 (`model=0, NSsites=7, ncatG=10`) vs M8
  (`model=0, NSsites=8, ncatG=10`). LRT **df=2**, `p = chi2.sf(lrt, 2)`.
- **Branch-site Model A (needs foreground):** `run_branch_site_model(...,
  foreground)` — alt `model=2, NSsites=2, fix_omega=0, omega=1.5`; null
  `model=2, NSsites=2, fix_omega=1, omega=1`. LRT df=1 but the asymptotic null is
  a **½:½ mixture of point-mass-0 and χ²₁** (Yang & dos Reis 2011; paper Note 5)
  → `p = 0.5 * chi2.sf(lrt, 1)` (lrt=0 ⇒ p=1). Reuse `_label_newick` for the
  `#1` foreground marking, exactly as the branch model does.
- **New parsers:** `_parse_site_classes(mlc)` (proportion + ω of the positive
  class from M8) and `_parse_beb_sites(mlc)` (Bayes Empirical Bayes "Positively
  selected sites" → list of `{position, posterior}`). Branch-site reuses the BEB
  parser for the foreground ω>1 class.
- **Result dicts** mirror `run_branch_model`'s contract plus model-specific
  fields: site → `omega_positive_class`, `prop_positive`, `lrt_pvalue`,
  `beb_sites`; branch-site → `omega_foreground_positive`, `prop_sites`,
  `lrt_pvalue` (mixture-corrected), `beb_sites`. Same `status` vocabulary
  (`computed` / `codeml_unavailable` / `no_foreground_seqs` / `timeout` /
  `error`).
- **Cache keys namespaced per model** (`…:site:…`, `…:branchsite:…`) so they do
  not collide with the branch-model cache. Make the codeml `timeout` configurable
  via `[paml] timeout_seconds` (site/branch-site runs are slower).

### 2. `backend/nullifier/agents/analyst.py` — drive the new models

- In `_fetch_paml_data` (analyst.py:58), after the branch model, also call
  `run_site_model` / `run_branch_site_model` per starter gene **when the
  `[paml]` flags enable them**, reusing the already-fetched
  `ensembl.fetch_gene_tree_aligned` alignment (one fetch, three model runs).
- Return them in separate maps; surface as `data["paml_site"]` and
  `data["paml_branch_site"]` (alongside today's `data["paml"]`), at the point
  where `pipeline.py:125` already wires `analyst_data["data"]["paml"]`. Add
  site/branch-site variants of the `paml.gene_*` events in `events.py`.

### 3. `backend/nullifier/tools/compute.py` — two new TEST_LIBRARY tests + BH fix

- `_paml_site_model(inputs, data)` reads `data["paml_site"]`; constructs
  `{"pervasive_positive_selection"}`. `_paml_branch_site_model(inputs, data)`
  reads `data["paml_branch_site"]`; constructs
  `{"lineage_specific_positive_selection"}`. Both mirror `_paml_branch_model`'s
  `_test_result` shape (so `validate_test_result` passes), expose `per_gene` and
  the best gene's `beb_sites`, and degrade to `available=False` with status
  counts when nothing computed.
- **BH across genes:** add a helper that runs the existing `benjamini_hochberg`
  over the `per_gene` `lrt_pvalue`s and reports `p_value_adjusted` +
  `significant = adjusted < 0.05`. Apply it in all three consumers
  (`_paml_branch_model` included). Register the two `kind:"paml"` entries — the
  `_run_one` dispatch (`compute.py:1351`) already routes `kind=="paml"` as
  `fn(inputs, data)`, unchanged. Update `TEST_LIBRARY_DOC` and
  `_closest_alternative` (`compute.py:1370`) so "site" / "branch-site" /
  "positive selection" route to the new tests.

### 4. Agents — methodologist / interpreter / skeptic prompts

- **Methodologist** (`methodologist.py:215`): teach the Jeffares Table-4 mapping —
  lineage-specific positive selection ⇒ `paml_branch_site_model`; pervasive
  (whole-tree) positive selection ⇒ `paml_site_model`; lineage *rate shift /
  relaxed constraint* ⇒ existing `paml_branch_model`. Keep BH as the correction.
- **Interpreter** (`interpreter.py:29`): read BEB sites, positive-class ω and
  proportion; enforce the honesty framing — a positive-selection conclusion
  requires BEB-supported sites **and** a BH-significant LRT; state the ½:½
  mixture caveat; state that site-level ω>1 ≠ gene-wide ω>1.
- **Skeptic** (`skeptic.py`): let the new evidence bear on the positive-selection
  alternative without overclaiming; reuse existing guardrails and add the paper's
  dominant-FP caveat (alignment error) when BEB sites are few or low-posterior.

### 5. `backend/nullifier/config/default_config.toml`

Add `[paml] run_site_model = true`, `run_branch_site_model = true`,
`timeout_seconds = 300`. Read via the existing loader overlay (no user config
change required).

## Deferred (documented, not built this pass)

- **Low-power / too-closely-related floor** (paper Note 1: >95% identity, or
  synonymous tree distance <0.5 ⇒ underpowered). Future: parse dS from codeml and
  flag `low_power`, analogous to the existing `dnds_saturation` upper-bound gate.
- **Per-column alignment-confidence masking (GUIDANCE-style).** The existing
  MAFFT-vs-PRANK aligner cross-check partially covers the paper's dominant-FP
  concern; full column masking is out of scope. Add a provenance caveat only.

## Verification

- **Unit (`backend/tests/test_paml_models.py`):** control-file generation asserts
  the right `model` / `NSsites` / `ncatG` / `fix_omega` lines per model; parser
  tests on captured codeml `.mlc` fixtures (site classes, BEB sites, lnL); LRT
  math including the branch-site ½:½ mixture; graceful degradation returns the
  right `status` when `_find_codeml()` is None (monkeypatched).
- **Contract:** `validate_test_result` passes for both new tests; the BH helper
  returns adjusted p-values matching `benjamini_hochberg` on a known vector.
- **Methodologist:** a "positive selection in primates" hypothesis selects
  `paml_branch_site_model`; a pervasive-selection hypothesis selects
  `paml_site_model` (mock plan).
- **Regression:** existing 86 compute tests pass; update branch-model tests that
  asserted significance on the uncorrected min p-value to expect BH-adjusted p.
- **End-to-end CLI** on a primate-accelerated starter set: with codeml installed,
  branch-site reports BEB sites + a BH-adjusted LRT; without codeml, all three
  PAML tests report `available=False` and the run completes. Confirm cache keys
  are namespaced per model (no collision with the branch cache).
