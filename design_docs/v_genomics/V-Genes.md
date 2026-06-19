# Nullifier v-genomics — Stage 0 (containerize) + Stage 1 (diagnostic layer)

## Context

The recently-shipped "honesty floor" PR made the genomic axis *honest*: ω now flows
from real pal2nal+NG86 codon alignments, and the engine renders **N/A** instead of
fabricating a score when it can't compute (`mirrortree_lite` usability gate +
`skeptic._apply_guardrails` forcing `genomic_evidence_alignment = None`). But the floor
can only answer with a **pairwise summary ω** — it cannot test the actual hypothesis
("BBB genes evolve at rates that track synaptic complexity across mammalian lineages"),
which is a claim about *specific branches* and *covariation between two gene sets*.

The full v-genomics program (spec §1–§10) replaces the primary estimator with
relative-rate covariation (ERC) + rate-phenotype (RERconverge), adds a HyPhy selection
layer, and — the differentiating part — a **diagnostic layer** that detects the four
dominant false-positive sources (alignment error, recombination, dS saturation, gBGC)
plus low power, *before* any result is believed.

**This plan covers only the foundation: Stage 0 + Stage 1.** Stage 0 stands up the
containerized tool substrate. Stage 1 produces a per-gene **diagnostic record** for the
8 starter genes — and nothing more. No risk score, no gating, no ERC/RERconverge.
The single goal of Stage 1, per the spec, is: *produce the 8 records and confirm by
hand that they reflect reality* (the known-saturated BBB set lights up saturation; an
alignment-sensitive gene shows aligner disagreement). Risk scoring + honesty-gate wiring
is Stage 2 and is explicitly out of scope here — diagnostics in this plan are an
**observational side-channel that gates nothing.**

### Locked decisions (spec §8 + user answers this session)
- **Scope:** Stages 0–1 only. Later stages noted under *Deferred*.
- **Container engine:** Docker image (version-pinned → provenance). Not WSL2.
- **Alignment confidence:** **GUIDANCE2 from the start** (not the lightweight
  occupancy+entropy fallback). Image therefore carries Perl + GUIDANCE2 + `pal2nal.pl`.
- HyPhy over PAML for model-based steps (GARD here); NG86 stays demoted to a cross-check
  input; the genomic axis stays *experimental* until the §6 benchmark (far future).

---

## Stage 0 — Docker substrate

A single version-pinned image and a thin Python subprocess/JSON boundary. Nothing
scientific computes in Stage 0; it is the swappable, testable estimator boundary
("send alignment + tree, get back artifacts + JSON").

### 0.1 Image — `docker/genomics/Dockerfile` (new)
Pin and install, one image, each version captured for provenance:
- **MAFFT**, **PRANK** — aligners
- **GUIDANCE2** (Perl) — column/residue confidence + aligner perturbation
- **pal2nal.pl** (Perl) — protein MSA → codon MSA (multi-species; the floor's pairwise
  `codon_align` stays for NG86, but the diagnostic layer needs a true MSA)
- **HyPhy** — bundles **GARD** (recombination)
- **IQ-TREE 2** — branch-length estimation on the fixed species tree (tree length / power,
  and the model-rate side of the NG86 cross-check)
- **RERconverge** (R) — installed now per spec §7 Stage 0 so the image is built once;
  **not invoked until Stage 4.**

Tag e.g. `nullifier-genomics:v1`. Document `docker build` in CLAUDE.md commands block.

### 0.2 Boundary — `backend/nullifier/tools/genomics_container.py` (new)
Follows the existing temp-dir subprocess pattern in `tools/paml.py:149` and
`tools/r_bridge.py:270` (write inputs to a temp workdir → run → parse output files):
- `run_tool(tool, args, files: dict[str,str], *, timeout) -> dict | None` — writes
  `files` into a temp workdir, runs
  `docker run --rm -v "<workdir>:/work" <image> <tool> <args>`, returns parsed
  outputs (JSON for HyPhy/GARD; output files for MAFFT/PRANK/GUIDANCE2/IQ-TREE).
  **Returns `None` on failure / Docker absent** — never throws (graceful-degradation
  convention). Thread-safe like the R bridge.
- `container_versions() -> dict[str,str]` — runs each tool's `--version` once, memoized;
  feeds provenance. Includes the image tag/digest.
- `available() -> bool` — `docker` on PATH and image present; surfaced at server startup
  the way LM-Studio absence already is.
- SQLite results cache `~/.nullifier/genomics_cache.db`, keyed by
  `sha256(tool + args + input-sequences)`, reusing the cache idiom from
  `tools/ensembl.py:115-137` / `tools/paml.py:54-76` (JSON value + `cached_at`, TTL from
  config). Diagnostics are expensive — never recompute the same alignment.

### 0.3 Config — `config/default_config.toml` + `config/loader.py`
New top-level section (mirrors existing `[paml]` / `[r]` shape):
```toml
[genomics_container]
enabled = true
engine = "docker"
image = "nullifier-genomics:v1"
timeout_seconds = 600
cache_path = "~/.nullifier/genomics_cache.db"
cache_ttl_days = 30
```
Add `genomics_container.cache_path` to the `~`-expansion list in `load_config()`
(`config/loader.py`, alongside `ensembl.cache_path` / `flags.db_path`).

### 0.4 Provenance
Add a `genomics_container` block to `data_provenance` (assembled in
`tools/genomic_data.build_data`) carrying `container_versions()` — image digest + each
tool version — same as the floor records `homology_pal2nal_ng86`. "Which HyPhy version"
becomes a real provenance field.

---

## Stage 1 — Diagnostic layer (records only, gates nothing)

Each starter gene gets a `GeneDiagnostics` record (spec schema §2). Inputs come from the
analyst path, which already fetches one2one orthologs, protein alignments
(`source_align_seq`/`target_align_seq`), and resolves CDS via the ENSP→transcript→CDS
two-hop (`ensembl.resolve_cds_for_protein`). The panel is the existing mammal panel
(`genomic_data._one2one_panel_species`).

### 1.1 Per-gene artifact build (in the container)
For each gene: collect panel one2one ortholog CDS → align proteins with **GUIDANCE2
(MAFFT base)** and **PRANK** → back-translate each to a codon MSA with `pal2nal.pl` →
build a tree with **IQ-TREE** (branch lengths on fixed topology) → run **GARD** on the
codon MSA. All cached by §0.2.

### 1.2 Detectors — `backend/nullifier/tools/diagnostics.py` (new)
Each detector returns its schema sub-block; missing inputs → nulls + a named reason,
never a throw.
- **§2.1 alignment** — `mafft_prank_agreement` (column-level agreement between the two
  MSAs), `guidance2_mean_col_score` (GUIDANCE2 column scores), `columns_masked` (below
  GUIDANCE2 threshold). `result_changes_with_aligner` is **null in Stage 1** — it
  requires running the primary test under each aligner, which doesn't exist until Stage 3.
  Note this explicitly in the record.
- **§2.2 recombination** — `gard_breakpoints` from the GARD JSON; `action` = `none`
  (Stage 1 only *detects*; partition/exclude is Stage 3 gating).
- **§2.3 saturation** — `median_branch_dS`, `saturated_branch_fraction`,
  `surviving_branches`. **Reuse the floor's existing saturation math** in `tools/dnds.py`
  (`_jc_distance`, the pS-above-JC-undefined fraction) and
  `analyst._set_statistics` (`dnds_saturation_fraction`) rather than reimplementing.
- **§2.4 gBGC** — `gc3_skew` (GC content at 3rd codon positions vs genome baseline),
  `risk` tier (low/medium/high). Pure Python from CDS; no container needed.
- **§2.5 power** — `taxa_after_gate`, `aligned_codons`, `tree_length` (from IQ-TREE),
  `usable`, `exclusion_reason`. **Reuse the floor's reason taxonomy** from
  `analyst._set_usability` (`no_cds` / `failed_pal2nal` / `saturated` / `too_few_taxa` /
  `recombinant`) so "API failed" vs "alignment failed" vs "too few orthologs" stay
  distinguished.
- **ng86_crosscheck** — `model_vs_ng86_divergence`: compare the floor's NG86 pairwise ω
  against the IQ-TREE model-based tree-wide rate on the *same* alignment. Coarse is fine
  for Stage 1 (large divergence = alignment suspect).

### 1.3 Record type
A plain `@dataclass GeneDiagnostics` (+ nested dataclasses) matching the spec §2 YAML
schema, in `diagnostics.py`. No pydantic — project uses plain dataclasses (cf.
`events.Event`). JSON-serializable for the runs DB and for hand inspection.

### 1.4 Orchestration + surfacing (non-gating)
- `run_diagnostics(gene_data, panel, on_event) -> dict[gene, GeneDiagnostics]` in
  `diagnostics.py`.
- Invoked from `agents/analyst.py` **after** orthologs+CDS are fetched, in parallel with
  the existing set-stats path. Its output is attached to the analyst result as
  `diagnostics` but is **not** read by `_set_statistics`, `mirrortree_lite`, or
  `skeptic._apply_guardrails` — Stage 1 changes no verdict.
- New events in `events.py` (mirror the `rdnds_*` factories at lines ~215): 
  `diagnostics_started(gene_count)`, `diagnostics_gene_complete(gene, usable, flags)`,
  `diagnostics_complete(n_usable, total)`.
- New CLI subcommand in `cli.py`: `diagnostics --input <hyp.txt> --output-json
  diag.json` — runs gene-set expansion + analyst ortholog/CDS fetch + `run_diagnostics`,
  dumps the 8 records to JSON for **by-hand inspection** (the Stage 1 acceptance gate).

### 1.5 Graceful degradation
If `genomics_container.available()` is false (no Docker / image), `run_diagnostics`
returns records populated only with the pure-Python fields (gBGC, NG86 cross-check
inputs, power counts) and nulls for container-derived fields, each with a named reason —
matching the floor's "return None on failure, never throw" convention. Server startup
surfaces container absence like it does LM Studio.

---

## Files

**New**
- `docker/genomics/Dockerfile`
- `backend/nullifier/tools/genomics_container.py`
- `backend/nullifier/tools/diagnostics.py`
- `backend/tests/test_genomics_container.py`
- `backend/tests/test_diagnostics.py`

**Modified**
- `backend/nullifier/config/default_config.toml` — `[genomics_container]` section
- `backend/nullifier/config/loader.py` — expand `genomics_container.cache_path`
- `backend/nullifier/agents/analyst.py` — invoke `run_diagnostics` (non-gating), attach
  to result
- `backend/nullifier/tools/genomic_data.py` — add `genomics_container` versions to
  `data_provenance`
- `backend/nullifier/events.py` — three `diagnostics_*` event factories
- `backend/nullifier/cli.py` — `diagnostics` subcommand
- `CLAUDE.md` — `docker build` + `diagnostics` commands

**Reused (no change)** — `tools/dnds.py` (`codon_align`, `ng86`, `_jc_distance`,
saturation), `tools/ensembl.py` (ortholog/CDS/tree fetch + cache idiom),
`agents/analyst.py` (`_set_statistics`, `_set_usability` reason taxonomy),
`tools/gene_sets.py` (starter set), `tools/paml.py` + `tools/r_bridge.py` (subprocess +
temp-dir + cache patterns to copy).

---

## Verification

1. **Unit (no Docker needed)** — `pytest backend/tests`. Mock the container boundary with
   the `FakeProc` / `monkeypatch.setattr(..., subprocess.run, ...)` pattern from
   `test_paml.py` / `test_r_bridge_dnds.py`:
   - gBGC `gc3_skew` + risk tiers on crafted CDS (known GC3-skewed vs neutral).
   - power thresholds → correct `usable` / `exclusion_reason` for each named reason.
   - saturation block matches the floor's existing `_set_statistics` output on shared
     fixtures (proves reuse, not divergence).
   - `genomics_container.run_tool` returns `None` (not throws) when Docker is absent;
     cache round-trips.
   - the existing **86-test suite still passes** (Stage 1 must not perturb the floor).
2. **Container smoke (skipped if Docker absent)** — build the image; run MAFFT, GUIDANCE2,
   pal2nal, GARD, IQ-TREE on a tiny 4-taxon fixture alignment; assert each returns parsed
   output and a version string.
3. **The Stage 1 acceptance gate (by hand)** — 
   `python -m nullifier.cli diagnostics --input examples/synapse_bbb.txt --output-json
   diag.json`, then read the 8 records and confirm they reflect reality:
   - the known-saturated BBB set shows high `saturated_branch_fraction` / `usable:false`
     with `exclusion_reason: saturated`;
   - at least one gene shows low `mafft_prank_agreement` / low `guidance2_mean_col_score`;
   - `gard_breakpoints`, `gc3_skew`, and `model_vs_ng86_divergence` are populated and
     plausible. This hand-check — not "it ran" — is what closes Stage 1.

---

## Deferred (later stages, not in this plan)
- **Stage 2** — `fp_risk` pre-flight score (spec §3) + wiring risk ≥ 0.50 → excluded →
  N/A through `skeptic._apply_guardrails`; `result_changes_with_aligner` becomes
  meaningful once tests exist.
- **Stage 3** — IQ-TREE/HyPhy per-branch relative rates + ERC on a curated ~50/50 set
  with matched controls + robustness.
- **Stage 4** — RERconverge (flagged secondary; ERC stays primary) over a cortical-neuron-number axis (Herculano-Houzel), with species-overlap + primate-confound constraints.
- **Stage 5** — validation harness (§6) + weight calibration → promotes the axis from
  experimental to verdict-bearing.
- **Stage 6** — HyPhy selection layer (aBSREL/BUSTED/RELAX), then scale gene-set size.
- Rejected (not deferred): codeml branch-site as primary estimator.
