# V-Genes Stage 4 — RERconverge + operationalized complexity axis

> Sibling docs: [V-Genes.md](V-Genes.md), [V-Genes2.md](V-Genes2.md), [V-Genes3.md](V-Genes3.md).
> **Depends on:** Stage 3 (per-branch relative rates), Stage 0 (RERconverge/R in the
> container). Architectural — gets its own detailed plan before execution.

## Context

ERC (Stage 3) tests set-vs-set covariation. Stage 4 adds the **"track complexity"** half
of the hypothesis: do each set's relative rates correlate with a **continuous phenotype
axis** (a synaptic-complexity proxy) across the tree, **phylogeny-corrected**?
RERconverge is purpose-built for exactly this — relative evolutionary rates vs a
binary/continuous trait with the phylogenetic correction baked in, with a large
validation literature.

The phenotype must be **operationalized, not hand-waved** (spec §4, §9). Start with **EQ
(encephalization quotient)** — the easiest complexity proxy to source across mammals.

## Scope / locked decisions
- Test = RERconverge: per-set RERs vs the **continuous** trait, phylogeny-corrected.
- Trait operationalized as a per-species continuous value over the mammal panel; **EQ
  first** (cortical-neuron-density / synapse-density deferred as alternatives).
- Runs in the Stage 0 container (R; no native-Windows R — that pain is why we
  containerized).
- **Over-claim guard (spec §9):** the report presents a rate-phenotype correlation as a
  *shared-driver-consistent association*, never as directional/causal co-evolution.
- Disclaimer: heuristic-not-calibrated until Stage 5.

## Design

### 4.1 RERconverge driver — `tools/rerconverge.py` (new)
Thin Python boundary → `genomics_container.run_tool` invoking an R driver script bundled
in `docker/genomics/` (write trees + RERs + trait → run RERconverge → JSON back).
Follows the temp-dir pattern of the old `tools/r_bridge.py`, but **containerized**.
Input RERs come from Stage 3 `branch_rates`.

### 4.2 Phenotype axis — `data/phenotypes/eq_mammals.tsv` (new)
Per-species EQ across the panel, with a **provenance/citation** record. The
methodologist's deferred `phenotype_association` construct (RERconverge slot already
present, obs 1863) **activates here** → routes to the `rerconverge` test.

### 4.3 Controls + honesty
Same matched control sets — control-set rate-phenotype correlation must be weaker than
BBB/synaptic. If the trait can't be sourced for enough panel species, or too few genes
survive the risk filter → untestable → **N/A** (reuse Stages 1–2 gate).

### 4.4 Framing enforcement
Interpreter/skeptic genomic prompts (`agents/interpreter.py`, `agents/skeptic.py`) must
frame any correlation as association-consistent-with-a-shared-driver, **not** causal
co-evolution. This is a correctness requirement, not a stylistic one.

## Files (anticipated)
`tools/rerconverge.py` (new) + R driver in `docker/genomics/` · `data/phenotypes/eq_mammals.tsv`
(new) · `agents/methodologist.py` (activate `phenotype_association` → `rerconverge`) ·
`tools/compute.py` (RERconverge result typing) · `agents/interpreter.py` + `skeptic.py`
(association-not-causation framing) · `events.py` · frontend RERconverge panel · tests +
fixtures.

## Open decisions (resolve when we plan Stage 4)
1. **EQ data source** — which published mammalian EQ table; must cover the panel species
   (may force a panel adjustment or documented imputation). This is a real data-sourcing
   task, not a code task.
2. **Continuous vs binary** trait — spec says continuous; EQ is continuous → continuous.
3. Alternative complexity proxies (cortical neuron density, synapse density) — defer or
   plan as a follow-on axis.

## Verification
A phylogeny-corrected rate-phenotype correlation **with controls**, or an honest
untestable; the EQ axis is operationalized **and cited**; report framing is
association-only. (Reproducing a *known* rate-phenotype association overlaps Stage 5.)

## Next: [V-Genes5.md](V-Genes5.md) — validation harness + calibration (the milestone).
