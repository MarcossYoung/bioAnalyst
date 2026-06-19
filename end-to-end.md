# Nullifier end-to-end execution trace

This document follows one hypothesis from submission to verdict using the current pipeline structure. It complements [dataFlow.md](dataFlow.md), [productStructure.md](productStructure.md), and [README.md](README.md).

## Example input

`examples/synapse_bbb.txt` describes a co-evolution hypothesis:

> Synaptic and blood-brain-barrier genes co-evolved under shared selective pressure. Synaptic and BBB genes should show comparable evolutionary constraint and should be more similar to each other than either is to matched controls. Starter entities: DLG4, GRIN1, SHANK3, CLDN5, OCLN, SLC2A1.

The input is free-form prose. It may also contain cited papers, proposed methods, starter data, or completed analyses with reported statistics.

## Execution topology

The conceptual six-stage product flow is implemented as a formalization stage, two concurrent evidence branches, and a final stress test:

```text
User input
    │
    ▼
Formalizer stage 1 ──► optional confirmation/edit gate ──► Formalizer stage 2
    │
    ├───────────────────────────────┬──────────────────────────────────┐
    │                               │                                  │
    ▼                               ▼                                  │
Librarian branch              Analyst branch                           │
  query expansion               gene-set expansion                    │
  bounded evidence hunt         genomic retrieval                     │
  per-paper classification      typed genomic-data build              │
  claim synthesis               methodologist                         │
    │                            deterministic compute                 │
    │                            robustness + reproducibility          │
    │                            interpreter                           │
    │                               │                                  │
    └───────────────────────────────┴─────────────── join ─────────────┘
                                                    │
                                                    ▼
                                                 Skeptic
                                                    │
                                                    ▼
                                  report + events + persisted run
```

`pipeline.py` starts the Librarian in a background worker before running the Analyst branch. Librarian events are collected in that worker and emitted when the branch joins. If no starter entities are present, the Analyst branch emits `analyst_skipped`, and the pipeline waits directly for the Librarian.

## 1. Entry and orchestration

The same pipeline serves both product surfaces:

- CLI: `python -m nullifier.cli run ...`
- Web/API: `POST /api/runs`, with progress replayed and streamed over `GET /ws/runs/{run_id}`

`pipeline.py` is a synchronous event-yielding orchestrator. Events are persisted in `~/.nullifier/runs.db`, rendered by the CLI, and streamed to the React UI. Cancellation is checked between major stages and produces `run_aborted`; unrecoverable failures produce `run_failed`.

## 2. Formalization and confirmation

### Stage 1: extraction

The first Claude call separates the research claim from its scaffolding and normalizes:

- `core_hypothesis`
- `domain`
- `key_entities` and `starter_entities`
- cited literature
- proposed and previously used methods
- starter data
- completed analyses and reported findings

For the example, completed analysis is empty and the six starter genes are retained.

### Confirmation gate

In the web flow, the backend emits `confirmation_required`. The user can keep, edit, or remove detected sections before expensive retrieval begins. The CLI can use the same callback or bypass it with `--no-confirm`.

### Stage 2: atomic claims

The second Claude call decomposes the confirmed hypothesis into independently testable claims. A plausible decomposition is:

- **C1:** Synaptic genes show stronger purifying selection than matched controls. Null: their constraint distributions do not differ.
- **C2:** BBB genes show stronger purifying selection than matched controls. Null: their constraint distributions do not differ.
- **C3:** Synaptic and BBB constraint profiles are more similar to each other than either is to controls. Null: there is no excess cross-set similarity.

The output is emitted as `claims_formalized`. At this point the two evidence branches begin.

## 3. Librarian branch: bounded disconfirming-evidence hunt

The Librarian no longer performs one search-and-classify pass. Each atomic claim runs through a bounded actor/critic loop.

### 3.1 Cited-literature validation

User-cited titles are checked before claim retrieval:

1. Semantic Scholar `GET /paper/search/match` attempts an exact title match.
2. A successful result uses the API `matchScore`.
3. A 404, throttling response, host failure, or unmatched title falls back to the existing federated search and local title/abstract similarity.
4. Results retain the existing `validated` or `unverified` record shape.

The Semantic Scholar call shares the per-run host circuit breaker with its other endpoints.

### 3.2 Seed round

For each claim, the Claude query expander generates approximately 5–8 variants. C1 might produce:

- `synaptic gene evolutionary conservation dN/dS`
- `postsynaptic density purifying selection`
- `SynGO evolutionary constraint primate`

Each query fans out to four sources through `tools/literature.py`:

| Source | Role |
|---|---|
| Semantic Scholar | Relevance-ranked graph search with biological-field filters and enriched metadata |
| OpenAlex | Broad scholarly coverage |
| Europe PMC | Life-science literature |
| bioRxiv/medRxiv | Preprints exposed through Europe PMC |

Results are deduplicated by DOI or normalized title and ranked before classification. Semantic Scholar records can include TLDR, open-access PDF, publication type/date, and fields of study.

### 3.3 Optional passage retrieval

When `[literature] use_snippet_search = true`, the top query variants also call Semantic Scholar `GET /snippet/search`. This path is disabled by default because it is more aggressively rate-limited without `SEMANTIC_SCHOLAR_API_KEY`.

The best passage is attached to a matching paper, or admitted as snippet-only evidence when capacity remains. Provenance stays explicit:

- `justification_quote`: exact text verified against the abstract
- `snippet_quote`: exact text verified against the Semantic Scholar passage
- `quote_source`: `abstract`, `snippet`, or `none`

A full-text snippet is never stored as an abstract quotation. TLDR text may help the classifier understand a paper but is not accepted as quoted evidence.

### 3.4 Per-paper classification

The high-volume classifier is routed to the configured `librarian_per_paper` backend, local LM Studio by default. It classifies each paper as:

- `supports`
- `contradicts`
- `tangential`
- `confounder`

Relevant user corrections from `flags.db` are injected into the prompt. Batch failures remain aligned with their papers and are recorded in `failed_classifications`; a high failure fraction marks the claim `classifier_degraded` rather than silently treating missing classifications as an evidence void.

### 3.5 Deterministic critic and hunter rounds

After each round, a deterministic critic counts credible disconfirming classifications (`contradicts` or `confounder`) that contain a verified abstract or snippet quote. It also counts distinct retrieval sources.

If the configured target has not been met, the Claude `librarian_hunter` proposes new queries aimed specifically at:

- negative findings
- contradictory results
- confounders
- alternative mechanisms
- angles not covered by previous queries

The next round searches and classifies only newly discovered papers. State accumulates across rounds.

The loop stops with one of these reasons:

| Stop reason | Meaning |
|---|---|
| `goal_met` | Required disconfirming evidence and source diversity were found |
| `saturated` | Repeated rounds produced no new disconfirming evidence |
| `budget_papers` | The per-claim paper cap was reached |
| `budget_time` | The per-claim search deadline was reached |
| `budget_rounds` | The configured round limit was exhausted |
| `no_queries` | Seed expansion produced no usable query |
| `exhausted` | The hunter produced no new query |

Each claim records `queries_used`, `hunt_stop_reason`, `hunt_rounds`, and `hunt_trace`. The timeline receives `queries_expanded`, `papers_retrieved`, `paper_classified`, `hunt_round`, `classifier_degraded`, and `synthesis_ready` events.

### 3.6 Claim synthesis

The Claude synthesizer reads all accumulated classifications and returns:

- evidence strength
- novelty state
- identified confounders
- remaining literature gap
- short synthesis

For the example, C1 may be well studied, C2 may have mixed or thinner evidence, and the specific C3 co-evolution claim may be sparsely studied. Sparse evidence is not treated as contradiction.

## 4. Analyst branch: gene sets to typed genomic evidence

This branch runs while the Librarian is searching.

### 4.1 Gene-set expansion

`tools/gene_sets.py` maps starter entities into canonical SynGO, BBB, control, and background sets. Candidate sets are scored for relevance through the configured `gene_set_classifier`, with heuristic fallback where supported.

Expansion is split into:

- primary, size-capped sets used for genomic retrieval and compute
- exploratory sets retained as context
- matched controls and background genes

The control set is essential: low dN/dS or high constraint is not informative without an appropriate comparison population.

### 4.2 Genomic retrieval and diagnostics

`agents/analyst.py` coordinates the evidence sources needed by the primary sets:

- Ensembl/HGNC/Compara lookup, homology, alignments, regulatory features, and motifs
- gnomAD LOEUF/pLI population constraint
- phylostratigraphic gene age
- optional PAML/codeml branch-model omega
- R/seqinr pairwise dN/dS
- diagnostic and false-positive risk checks

Source-specific caches under `~/.nullifier/` avoid repeating expensive requests and calculations.

### 4.3 Typed data build

`tools/genomic_data.py` converts fetched records into the compute contract:

- groups and variables
- gene index
- provenance records
- evolutionary-rate vectors
- risk-filtered and saturation-aware data summaries

The Methodologist and Compute layer consume this typed structure rather than raw API responses.

## 5. Methodologist, deterministic compute, and interpretation

### Methodologist

Claude reads the hypothesis, expansion, completed-analysis context, and genomic summary. It selects tests from the supported menu but performs no calculations. For the example it may choose rank-based set comparisons, a cross-set association test, multiple-testing correction, effect sizes, and bootstrap intervals.

### Compute and robustness

`agents/compute.py` invokes deterministic helpers in `tools/compute.py`. The same inputs therefore produce the same statistics. Outputs include typed test results, corrected p-values, effect sizes, confidence intervals, and provenance.

The same stage also runs:

- leave-one-out or configured perturbation robustness
- reproducibility checks for completed analyses supplied by the user

### Interpreter

Claude translates typed results into a bounded scientific assessment, including limitations and influential genes. If dN/dS is saturated or risk filtering leaves too few scorable genes, the genomic assessment is explicitly marked `untestable` rather than forcing a conclusion.

## 6. Join and Skeptic verdict

The Skeptic runs only after the literature and genomic branches have completed. It reads:

- formalized claims and null hypotheses
- Librarian classifications, synthesis, degradation state, and top raw abstracts
- gene-set expansion and genomic evidence
- deterministic test results
- robustness and reproducibility results
- Interpreter assessment

It then scores the evidence, identifies alternatives, names a decisive experiment, and issues the final verdict.

### Illustrative outcomes

These are outcome patterns, not hard-coded thresholds or expected results for the example dataset.

#### `STRONG`

The synaptic and BBB sets both differ materially from matched controls, effect sizes are meaningful, corrected results survive robustness checks, and literature evidence agrees without credible contradictions.

#### `FALSIFIED`

A load-bearing claim fails—for example, BBB genes do not differ from controls—and credible literature directly contradicts the proposed shared selective-pressure mechanism. Support for C1 alone does not rescue C3.

#### `NOVEL-UNTESTED`

The specific co-evolution claim has little prior literature and the genomic branch cannot test it decisively. Absence of evidence is reported as uninvestigated, not disproven.

#### `RESULTS-PROBLEMATIC`

If the input includes a completed analysis such as `t-test, p = 0.03, n = 18`, critique mode evaluates the reported methods separately. It can flag inappropriate distributional assumptions, missing multiple-testing correction, low power, sensitivity to individual genes, or values that cannot be reproduced from available sources. The UI presents the underlying hypothesis verdict alongside the completed-analysis critique.

## 7. Final report and feedback loop

`run_completed` carries four top-level products:

- `formalized`
- `evidence`
- `analyst`
- `verdict`

The web UI renders live progress, evidence by claim, gene-set expansion, deterministic compute, robustness, genomic panels, provenance, and verdict cards. The CLI renders the same report through Rich.

Users can review paper classifications after the run. Corrections are stored in `flags.db` and become relevant few-shot guidance in future Librarian classifications; this is prompt-based feedback, not model fine-tuning.

## 8. Routing and failure behavior

Default routing keeps high-volume tasks local and reasoning-heavy tasks on Claude:

| Task | Default backend |
|---|---|
| Formalizer stages | Claude |
| Query expander | Claude |
| Librarian per-paper classifier | Local LM Studio |
| Librarian hunter | Claude |
| Librarian synthesizer | Claude |
| Gene-set classifier | Local LM Studio |
| Methodologist | Claude |
| Compute | Python, no LLM |
| Interpreter | Claude |
| Skeptic | Claude |

The pipeline is designed to preserve partial results:

- A failed literature source trips its host breaker; remaining sources continue.
- A failed hunter call stops follow-up retrieval for that claim and records the error.
- Per-paper failures are retained with drop reasons and degradation state.
- Query expansion or synthesis failures degrade the affected claim where possible.
- Missing starter entities skip the genomic branch without blocking literature review.
- Missing optional R/PAML capabilities degrade only the corresponding genomic axis.
- An unrecoverable stage exception emits `run_failed`.

The shipped defaults, including hunt limits and Semantic Scholar controls, are defined in `backend/nullifier/config/default_config.toml` and merged with user overrides from `~/.nullifier/config.toml`.
