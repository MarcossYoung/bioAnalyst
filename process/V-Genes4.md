# V-Genes Stage 4 — RERconverge (flagged secondary) + cortical-neuron-number axis

> Sibling docs: [V-Genes.md](V-Genes.md), [V-Genes2.md](V-Genes2.md), [V-Genes3.md](V-Genes3.md).
> **Depends on:** Stage 3 (per-branch relative rates + ERC, the **primary** test), Stage 0
> (RERconverge/R in the container). Architectural — gets its own detailed plan before execution.

## Context

ERC (Stage 3) tests set-vs-set covariation and is the **primary phenotype-independent
test** of the co-evolution hypothesis — it carries the verdict. Stage 4 adds the
**"track complexity"** half: do each set's relative rates correlate with a **continuous
phenotype axis** across the tree, **phylogeny-corrected**? RERconverge is purpose-built
for exactly this — relative evolutionary rates vs a continuous trait with the phylogenetic
correction baked in, with a large validation literature.

The phenotype is **cortical neuron number** (the **Herculano-Houzel** compiled
isotropic-fractionator dataset), operationalized as a per-species continuous value — **not**
hand-waved "synaptic complexity" (spec §4, §9). This is a deliberate deviation from the
spec's "start with EQ" suggestion: cortical neuron number is a more direct proxy for the
complexity axis, but it is far sparser than EQ tables. After intersecting Herculano-Houzel's
species with the mammal one2one-ortholog panel and the surviving low-risk gene set, the
RERconverge leg realistically falls to **~15–25 species** and is **likely underpowered**.

Stage 4 therefore runs **secondary to ERC**: the RERconverge leg is a **flagged,
corroborative** check that never overrides the ERC verdict, and is reported as exploratory
and underpowered, with the species-overlap and primate-confound constraints written in
(below).

## Scope / locked decisions
- **ERC (Stage 3) is primary; RERconverge is flagged secondary.** The RERconverge result
  never overrides the ERC verdict and is presented as exploratory/underpowered.
- Test = RERconverge: per-set RERs vs the **continuous** trait, phylogeny-corrected.
- Trait = **cortical neuron number** (Herculano-Houzel compiled dataset), a per-species
  continuous value over the mammal panel. (EQ, cortical-neuron-density, and synapse-density
  are deferred alternative proxies.)
- **Species-overlap floor (honesty gate):** if fewer than ~20 panel species (tune in
  review) carry **both** a cortical neuron count **and** a surviving low-risk ortholog, the
  leg is **untestable → N/A** (reuse the Stages 1–2 gate, see §4.3).
- **Primate-confound:** the trait is clade-driven — Herculano-Houzel's neuron-count signal
  is concentrated in primates. At this N, any rate-phenotype correlation is confounded by
  primate shared ancestry; phylogenetic correction is **necessary but not sufficient**.
  Results must carry a primate-confound flag and pass the primate-out sensitivity check (§4.3).
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

### 4.2 Phenotype axis — `data/phenotypes/cortical_neurons_mammals.tsv` (new)
Per-species **cortical neuron counts** across the panel, with a **provenance/citation**
record pointing to the **Herculano-Houzel** compiled dataset. Expect only **~15–25**
panel species to carry a count after the one2one-ortholog intersection. The
methodologist's deferred `phenotype_association` construct (RERconverge slot already
present, obs 1863) **activates here** → routes to the `rerconverge` test.

### 4.3 Controls + honesty
Same matched control sets — control-set rate-phenotype correlation must be weaker than
BBB/synaptic. Two honesty constraints govern this leg:
- **Species-overlap → N/A.** If the trait can't be sourced for enough panel species
  (below the ~20-species floor), or too few genes survive the risk filter → untestable →
  **N/A** (reuse Stages 1–2 gate).
- **Primate-out sensitivity check.** Because the neuron-count signal is concentrated in
  primates, the leg must report whether any rate-phenotype correlation **survives dropping
  (or down-weighting) the primate clade**. A correlation that vanishes without primates is
  reported as **primate-confounded**, not as support.

### 4.4 Framing enforcement
Interpreter/skeptic genomic prompts (`agents/interpreter.py`, `agents/skeptic.py`) must:
- frame any correlation as association-consistent-with-a-shared-driver, **not** causal
  co-evolution (a correctness requirement, not stylistic); and
- surface the **underpowered** and **primate-confound** flags whenever the RERconverge leg
  returns a number, and present that number as **secondary to the ERC result**.

## Files (anticipated)
`tools/rerconverge.py` (new) + R driver in `docker/genomics/` ·
`data/phenotypes/cortical_neurons_mammals.tsv` (new) · `agents/methodologist.py` (activate
`phenotype_association` → `rerconverge`) · `tools/compute.py` (RERconverge result typing) ·
`agents/interpreter.py` + `skeptic.py` (association-not-causation framing + underpowered /
primate-confound flags) · `events.py` · frontend RERconverge panel · tests + fixtures.

## Open decisions (resolve when we plan Stage 4)
1. **Panel intersection** — the data source is resolved (Herculano-Houzel compiled
   dataset); the remaining real task is which panel species have published counts, and
   documenting any imputation/exclusions and the resulting N (which sets whether the leg is
   testable at all). This is a data-sourcing task, not a code task.
2. **Continuous vs binary** trait — spec says continuous; cortical neuron number is
   continuous → continuous.
3. Alternative complexity proxies (EQ, cortical neuron density, synapse density) — defer or
   plan as a follow-on axis.

## Verification
**ERC carries the verdict.** The RERconverge leg yields either a **flagged, underpowered,
primate-sensitivity-checked** rate-phenotype correlation **with controls**, or an honest
**untestable/N/A**; the cortical-neuron-number axis is operationalized **and cited** to
Herculano-Houzel; report framing is association-only and presents the leg as secondary.
(Reproducing a *known* rate-phenotype association overlaps Stage 5.)

## Next: [V-Genes5.md](V-Genes5.md) — validation harness + calibration (the milestone).
