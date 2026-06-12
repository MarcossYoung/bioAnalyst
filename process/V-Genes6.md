# V-Genes Stage 6 — HyPhy selection layer + scale

> Sibling docs: [V-Genes.md](V-Genes.md), [V-Genes2.md](V-Genes2.md), [V-Genes3.md](V-Genes3.md), [V-Genes4.md](V-Genes4.md), [V-Genes5.md](V-Genes5.md).
> **Depends on:** Stage 5 (axis promoted / benchmark passed). Comes **last**.

## Context

Selection detection is **interpretation only — never the headline** (spec §1, §8.1).
After the primary test (ERC/RERconverge) and the benchmark (Stage 5), Stage 6 adds HyPhy
selection tests to answer *"is the rate shift ERC/RERconverge found driven by selection,
or by drift/relaxation?"* — context, not verdict. Only **then** does gene-set size scale
beyond the curated set.

## Scope / locked decisions
- HyPhy selection tests run **only on the genes driving an ERC/RERconverge signal**, not
  all genes.
- Strictly **interpretation**: never produces the primary score.
- **HyPhy, not PAML** (CLI + JSON + maintained). codeml stays **opt-in single-gene
  drill-down** (M7/M8 site models) only — never automated.
- Scaling beyond the curated ~50/50 set happens **after** Stage 5 and after the selection
  layer works.

## Design

### 6.1 Selection tests — `tools/hyphy_selection.py` (new)
Calls `genomics_container.run_tool` (Stage 0); each test emits JSON:
- **aBSREL** — which branches show episodic selection (no a-priori foreground).
- **RELAX** — relaxed vs intensified selection (often *more* relevant here than positive
  selection, since the hypothesis is partly about changing constraint).
- **BUSTED** — gene-wide episodic selection (optional).

### 6.2 Interpretation-only enforcement
`agents/interpreter.py` + `agents/skeptic.py`: selection output **annotates** the
ERC/RERconverge finding (drift vs selection vs relaxation context) and **never** overrides
the primary score. This is a correctness invariant, mechanically enforced.

### 6.3 gBGC interaction (spec §2.4)
A high-`gbgc.risk` gene (from the Stage 1 record) **cannot contribute a positive-selection
interpretation without the caveat surfaced.** Wire `gbgc.risk` into the selection-layer
interpretation gate.

### 6.4 Scaling
Only after the above: scale gene-set size from the curated set toward the full expanded
set (the floor's ~8,721). Throughput/caching of the container boundary now matters —
batch + lean on the Stage 0 results cache. Measure per-gene cost before scaling; scale in
stages, keeping the honesty gate intact (N/A where warranted).

### 6.5 Optional codeml drill-down
An opt-in surface for site-level M7/M8 on a single gene a user explicitly drills into.
Never in the automated path.

## Files (anticipated)
`tools/hyphy_selection.py` (new) · `tools/compute.py` (selection result typing, marked
interpretation-only) · `agents/interpreter.py` + `skeptic.py` (context-not-verdict; gBGC
caveat gate) · `agents/methodologist.py` (selection triggered by ERC/RERconverge signal,
not as primary) · scaling/batching in `agents/analyst.py` + `tools/genomic_data.py` +
container cache · optional codeml drill-down surface · `events.py` · frontend selection
panel · tests + fixtures.

## Open decisions (resolve when we plan Stage 6)
1. **Default selection tests** — recommend **aBSREL + RELAX** first (RELAX matches the
   constraint-change framing); BUSTED optional.
2. **Scaling target + throughput budget** — full ~8,721 is expensive; staged scale-up
   with the container cache, measure per-gene cost first.
3. **codeml drill-down scope** — opt-in only; how surfaced in the UI.

## Verification
Selection tests run on signal-driving genes and emit JSON context; results **never**
override the ERC/RERconverge primary score; the gBGC caveat surfaces on high-risk genes;
at small scale-up the pipeline stays honest (N/A where warranted) and performance is
acceptable.

---

*End of the V-Genes staged program. See [V-Genes.md](V-Genes.md) for the Stage 0+1
foundation and spec §10 for explicitly rejected/deferred alternatives (codeml branch-site
as primary estimator — rejected; Coevol; regulatory co-adaptation track).*
