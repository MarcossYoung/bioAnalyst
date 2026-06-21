# PAML positive-selection models: site M7/M8 and branch-site Model A

**Status:** Implementation ready

**Date:** 2026-06-19

**Basis:** Jeffares et al. 2015, *A Beginner's Guide to Estimating the
Non-synonymous to Synonymous Rate Ratio of all Protein-Coding Genes in a Genome*

## Summary

Nullifier currently runs only the PAML branch model (`model=0` versus
`model=2`, `NSsites=0`). That test detects a lineage-wide shift in ω, but it is
not a direct test for positive selection at a subset of codons. A branch-wide
average can remain below one even when a small number of sites are adaptive.

Add two standard CODEML tests:

- Site model M7 versus M8 for pervasive positive selection across the tree.
- Branch-site Model A for positive selection at sites on specified foreground
  branches.

Also apply Benjamini-Hochberg correction across genes to all three PAML model
families. The current branch-model consumer selects the minimum raw per-gene
p-value, which is not valid for a genome-scale family of tests.

The implementation must preserve the existing graceful-degradation contract:
PAML functions return typed status dictionaries and never terminate a run when
CODEML or usable sequence data is unavailable.

## Scope

### Included

- M7/M8 site-model runner, parser, cache, and compute consumer.
- Branch-site Model A runner, parser, cache, and compute consumer.
- Gene-level BH correction for branch, site, and branch-site model families.
- Formalizer constructs and deterministic Methodologist routing.
- Analyst data collection, pipeline wiring, events, and frontend event labels.
- Interpreter and Skeptic guidance for BEB-supported positive-selection claims.
- Configuration, unit tests, contract tests, and regression coverage.

### Deferred

- A low-power floor based on sequence identity or synonymous tree distance.
- Per-column alignment-confidence masking such as GUIDANCE.
- Changing how foreground branches are selected or labeled.
- Parallel execution of CODEML jobs.

The existing MAFFT-versus-PRANK diagnostic remains the available alignment
sensitivity check. Until column masking is implemented, alignment error must be
reported as an explicit positive-selection caveat.

## Scientific model definitions

### Existing branch model

Purpose: detect a lineage-wide ω shift, including relaxed constraint or
acceleration. It must not be described as a site-level positive-selection test.

- Null: `model=0`, `NSsites=0`.
- Alternative: `model=2`, `NSsites=0`.
- LRT degrees of freedom: 1.
- Significance: per-gene BH-adjusted p-value below 0.05.

### Site model M7 versus M8

Purpose: detect pervasive positive selection at a subset of sites across the
whole tree. It does not use a foreground branch.

- Null M7: `model=0`, `NSsites=7`, `ncatG=10`.
- Alternative M8: `model=0`, `NSsites=8`, `ncatG=10`.
- LRT degrees of freedom: 2.
- LRT p-value: `chi2.sf(lrt, 2)`.
- Model output: positive-class ω, positive-class proportion, and BEB sites from
  M8.

### Branch-site Model A

Purpose: detect positive selection at a subset of sites on configured
foreground branches.

- Null: `model=2`, `NSsites=2`, `fix_omega=1`, `omega=1`.
- Alternative: `model=2`, `NSsites=2`, `fix_omega=0`, `omega=1.5`.
- LRT degrees of freedom: 1 with a ½ point-mass-at-zero and ½ χ²₁ null.
- LRT p-value: `1` when `lrt == 0`; otherwise `0.5 * chi2.sf(lrt, 1)`.
- Model output: foreground positive-class ω, affected-site proportion, and BEB
  sites from the alternative.

The first implementation reuses `_label_newick()` and the existing foreground
groups. Multiple labeled tips therefore represent a shared foreground category;
changing this biological definition is outside this feature.

### Positive-selection decision rule

A site or branch-site result supports positive selection only when both are
true:

1. The selected gene has a gene-family BH-adjusted LRT p-value below 0.05.
2. At least one parsed BEB site has posterior probability at or above 0.95.

An LRT without a qualifying BEB site is model-level evidence without localized
site support. A BEB site without a BH-significant LRT is not sufficient.

## Claim constructs and test routing

The feature must be reachable from normal pipeline input. Add these claim
constructs to the formalizer and semantic normalization contracts:

| Construct | Meaning | Compute test |
| --- | --- | --- |
| `pervasive_positive_selection` | Selected sites across the tree | `paml_site_model` |
| `lineage_specific_positive_selection` | Selected sites on a foreground lineage | `paml_branch_site_model` |
| `lineage_specific_rate_shift` | Branch-wide acceleration or relaxed constraint | `paml_branch_model` |

Update construct inference with specific positive-selection terms before the
general set-difference fallback. The Methodologist must route these constructs
deterministically. LLM-selected plans remain restricted to tests registered for
the claim construct.

The existing ambiguous `lineage_specific_selection` construct is replaced by
the explicit constructs above. Compatibility normalization may map it to
`lineage_specific_rate_shift`, because the existing implementation only
provides the branch model.

## Runtime and data flow

For every starter gene, the Analyst fetches the Ensembl Compara alignment once
and may run all three model families:

```text
Compara alignment
  ├─ branch model
  ├─ site M7/M8
  └─ branch-site Model A
```

Site and branch-site models are enabled by default and can be disabled
independently. On a cold cache, one gene requires six CODEML processes: two per
model family. `timeout_seconds` is a per-process limit, not a total per-gene
deadline.

The Analyst returns separate maps:

```text
paml_data
paml_site_data
paml_branch_site_data
```

The prepared compute data exposes them as:

```text
data["paml"]
data["paml_site"]
data["paml_branch_site"]
```

Pipeline setup and leave-one-out rebuilds must preserve all three maps. Site and
branch-site output is not added to generic scalar group metrics in this pass;
dedicated compute consumers read the result maps directly.

## Result contracts

All runner functions return a dictionary containing `status` and `gene`.
Expected statuses are:

- `computed`
- `codeml_unavailable`
- `no_foreground_seqs` for foreground-dependent models
- `insufficient_sequences`
- `timeout`
- `error`

Upstream alignment retrieval may additionally produce `no_compara_alignment`.

### Site-model computed result

```json
{
  "status": "computed",
  "gene": "GENE",
  "model": "site",
  "lrt_statistic": 0.0,
  "lrt_pvalue": 1.0,
  "omega_positive_class": 1.0,
  "prop_positive": 0.0,
  "beb_sites": [],
  "species_count": 0,
  "alignment_length": 0,
  "provenance": {}
}
```

### Branch-site computed result

```json
{
  "status": "computed",
  "gene": "GENE",
  "model": "branch_site",
  "foreground_group": "primates",
  "lrt_statistic": 0.0,
  "lrt_pvalue": 1.0,
  "omega_foreground_positive": 1.0,
  "prop_sites": 0.0,
  "beb_sites": [],
  "species_count": 0,
  "alignment_length": 0,
  "provenance": {}
}
```

Each BEB entry contains:

```json
{
  "position": 42,
  "amino_acid": "K",
  "posterior": 0.987,
  "significance_marker": "*"
}
```

Parsers must tolerate absent amino-acid or significance-marker fields while
requiring a valid position and posterior probability.

## Multiple-testing ownership

Each PAML compute consumer owns correction across genes within its model family:

1. Collect finite `lrt_pvalue` values from `status="computed"` results.
2. Run the existing `benjamini_hochberg()` helper.
3. Add `p_value_adjusted` and `significant` to each computed per-gene result.
4. Select the best gene by adjusted p-value, then raw p-value as a stable
   tie-breaker.
5. Expose both raw and adjusted values in the typed test result.

PAML test results must be marked as internally corrected. The generic
`run_analysis_plan()` correction must not overwrite their gene-family adjusted
p-values. Any across-test-family correction should use separate fields and is
not part of this feature.

## Implementation changes

### `backend/nullifier/tools/paml.py`

- Generalize `_write_control()` to accept `nssites`, `ncatg`, `fix_omega`,
  `omega`, and a unique run label.
- Add shared sequence/tree preparation and minimum-sequence validation.
- Read `[paml].timeout_seconds` and pass it to every `_run_codeml()` call.
- Add `_parse_site_classes()` and `_parse_beb_sites()`.
- Add `run_site_model()` and `run_branch_site_model()`.
- Refactor `run_branch_model()` onto shared timeout and cache helpers without
  changing its scientific definition.
- Namespace cache keys with `branch`, `site`, or `branch_site`; include the
  foreground for foreground-dependent models and the alignment hash for all
  models.

### `backend/nullifier/agents/analyst.py` and pipeline wiring

- Refactor `_fetch_paml_data()` to fetch each alignment once and return all
  configured model maps.
- Emit model-specific progress and failure events.
- Add the new maps to Analyst output and prepared compute data.
- Preserve them in `pipeline.py` and robustness rebuilds.

### `backend/nullifier/tools/compute.py`

- Add a shared PAML gene-family BH helper.
- Apply it to `_paml_branch_model()`.
- Add `_paml_site_model()` and `_paml_branch_site_model()`.
- Register both tests in `TEST_LIBRARY` with their explicit constructs.
- Update `TEST_LIBRARY_DOC` and `_closest_alternative()`.
- Keep unavailable results valid under `validate_test_result()` and include
  status counts in `details`.

The site and branch-site consumers expose the selected gene's positive-class
parameters and qualifying BEB sites. The branch consumer describes its effect
as a rate shift; it must not label `omega_foreground > 1` alone as a validated
site-level positive-selection result.

### Formalizer and Methodologist

- Extend allowed constructs, normalization, inference, and output descriptions.
- Add deterministic construct-to-test mappings.
- Set Methodologist correction to `none` for plans containing only internally
  corrected PAML tests.
- Document the distinction between rate shifts, pervasive selection, and
  lineage-specific site selection in the Methodologist context.

### Interpreter and Skeptic

The Interpreter currently renders only top-level typed fields. Add a bounded
PAML summary containing the selected gene, model parameters, adjusted p-value,
and qualifying BEB sites. Do not dump every per-gene record into the prompt.

Interpreter rules:

- Require both the BH-significant LRT and qualifying BEB support before stating
  that positive selection is supported.
- State that site-level ω above one does not imply gene-wide ω above one.
- Identify the branch-site mixture-null correction.
- Report unsuccessful or insufficient alignments as coverage limitations.

Skeptic rules:

- Allow valid site and branch-site evidence to bear on the positive-selection
  alternative.
- Flag alignment error as the dominant false-positive concern when BEB support
  is sparse, low-posterior, or alignment-sensitive.
- Do not let the existing branch model stand in for a branch-site test.

### Events and frontend

Use model-aware PAML events for started, completed, timeout, and failed states.
Every event includes `gene` and `model`; foreground-dependent events also include
`foreground`. Update `EventTimeline.tsx` to render `branch`, `site`, and
`branch-site` labels and ensure failures are mapped to the Analyst stage.

### Configuration

Add defaults:

```toml
[paml]
codeml_path = ""
run_site_model = true
run_branch_site_model = true
timeout_seconds = 300
```

The existing deep-merge loader makes these keys available to users with older
configuration files.

## Delivery sequence

1. Add claim constructs and deterministic routing tests.
2. Refactor shared PAML control, timeout, cache, and parsing infrastructure.
3. Implement and unit-test site and branch-site runners.
4. Refactor Analyst collection and pipeline data wiring.
5. Add compute consumers and gene-family BH correction.
6. Add interpreter summaries, Skeptic rules, events, and frontend labels.
7. Run targeted contract tests, the complete backend suite, and the frontend
   production build.

This order makes each layer testable before the next layer depends on it.

## Verification and acceptance criteria

### Unit tests

- Control files contain the correct `model`, `NSsites`, `ncatG`, `fix_omega`,
  and `omega` values for every null and alternative.
- Captured CODEML `.mlc` fixtures parse lnL, positive classes, and BEB sites.
- M7/M8 uses χ² with 2 degrees of freedom.
- Branch-site uses the mixture p-value and returns `p=1` for `lrt=0`.
- Cache keys cannot collide across model families or foregrounds.
- Config flags disable only their corresponding model.
- Configured timeout reaches every CODEML subprocess.
- Missing CODEML, missing alignment, insufficient sequences, timeout, execution
  failure, and parse failure return typed dictionaries without raising.

### Compute and routing tests

- BH-adjusted values match `benjamini_hochberg()` for a known vector.
- Branch-model significance no longer uses the minimum unadjusted p-value.
- Both new consumers pass `validate_test_result()` when computed or unavailable.
- Positive-selection significance requires adjusted LRT and BEB support.
- Plan-level correction does not overwrite PAML gene-family correction.
- A pervasive-selection claim selects `paml_site_model`.
- A primate-specific positive-selection claim selects
  `paml_branch_site_model`.
- A lineage acceleration or relaxed-constraint claim selects
  `paml_branch_model`.

### Integration and regression tests

- The Analyst fetches one alignment per gene while running enabled models.
- All model maps reach Compute and robustness rebuilds.
- Model-aware PAML events render in the frontend timeline.
- Without CODEML, all PAML tests return `available=false` and the run completes.
- With fixture-backed successful runs, site and branch-site output includes BEB
  sites and adjusted p-values.
- Existing backend tests pass after branch-model expectations are updated.
- The frontend production build succeeds.

## Completion criteria

The feature is complete when a normal hypothesis run can route an explicit
positive-selection claim to the appropriate PAML model, execute or gracefully
degrade all configured model families, correct per-gene tests, expose bounded
BEB evidence to interpretation, and complete without uncaught CODEML errors.
