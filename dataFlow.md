# bioAnalyst — Data Flow Map

```text
                                  ┌─────────────────────────────────┐
                                  │  USER INPUT                     │
                                  │  Hypothesis text (+ optional    │
                                  │  completed analysis/results)    │
                                  └────────────────┬────────────────┘
                                                   │
                                                   ▼
                       ┌──────────────────────────────────────────────────┐
                       │  ENTRY POINTS                                    │
                       │  cli.py (run cmd)    server.py (POST /api/runs)  │
                       └────────────────┬─────────────────────────────────┘
                                        │
                                        ▼
                       ┌──────────────────────────────────────────────────┐
                       │  pipeline.py — event-yielding orchestrator       │
                       │  emits events ──────────────────────────► WebSocket
                       │  persists events ───────────────────────► store/runs.py
                       └────────────────┬─────────────────────────────────┘
                                        │
                ┌───────────────────────┴───────────────────────┐
                │                                               │
                ▼                                               ▼
   ┌────────────────────────┐                    ┌────────────────────────────┐
   │  config/loader.py      │                    │  tools/llm_client.py       │
   │  config.toml routing   │───── routing ─────►│  Claude   │   LM Studio    │
   │  Claude / Local model  │                    │  Sonnet   │   Gemma/local  │
   └────────────────────────┘                    └─────────┬──┴──────┬───────┘
                                                           │         │
            ┌──────────────────────────────────────────────┘         │
            │                                                        │
            ▼                                                        │
┌───────────────────────────────────────────────────────┐            │
│  AGENT: Formalizer (Stage 1 + Stage 2)        Claude  │            │
│  Inputs:  raw input text                              │            │
│  Outputs: core_hypothesis, domain, key_entities,      │            │
│           starter_entities, proposed/methods_used,    │            │
│           completed_analysis, atomic_claims           │            │
└────────────────────────┬──────────────────────────────┘            │
                         │                                           │
                         ▼  events.confirmation_required             │
              ┌─────────────────────────┐                            │
              │  CONFIRMATION GATE      │                            │
              │  WebSocket round-trip   │                            │
              │  user keeps/edits/      │                            │
              │  removes each section   │                            │
              └─────────────────────────┘                            │
                         │                                           │
                         ▼                                           │
┌───────────────────────────────────────────────────────┐            │
│  AGENT: Query Expander                        Claude  │            │
│  Per atomic claim → 5–8 search variants               │            │
└────────────────────────┬──────────────────────────────┘            │
                         │                                           │
                         ▼                                           │
┌───────────────────────────────────────────────────────────────┐    │
│  tools/literature.py — federated retrieval (parallel)         │    │
│  ┌─────────────────┐ ┌─────────────┐ ┌────────────┐ ┌───────┐ │    │
│  │ semantic_       │ │ openalex.py │ │ europe_    │ │ bio-  │ │    │
│  │ scholar.py      │ │             │ │ pmc.py     │ │ rxiv  │ │    │
│  └─────────────────┘ └─────────────┘ └────────────┘ └───────┘ │    │
│  Dedupe by DOI/title → rank → return papers                   │    │
└──────────────────────────────┬────────────────────────────────┘    │
                               │                                     │
                               ▼                                     │
┌───────────────────────────────────────────────────────────────┐    │
│  AGENT: Librarian                                             │    │
│  ┌──────────────────────────────────────────────┐             │    │
│  │  Step A — Per-paper classification           │◄────────────┘    │
│  │  Routed to LM Studio/local, parallel         │  high volume     │
│  │  Inputs:  paper abstract + claim             │                  │
│  │  Outputs: supports/contradicts/tangential/   │                  │
│  │           confounder + quoted sentence       │                  │
│  │  Prior flags injected as few-shot ◄──── flag_store.py           │
│  └──────────────────────────────────────────────┘                  │
│  ┌──────────────────────────────────────────────┐                  │
│  │  Step B — Per-claim synthesizer       Claude │                  │
│  │  Reads all classifications for one claim →   │                  │
│  │  novelty_flag, evidence_strength, gaps       │                  │
│  └──────────────────────────────────────────────┘                  │
└─────────────┬─────────────────────────────────────────────────┐    │
              │                                                 │    │
              │ (literature evidence)                           │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  GENE SET ASSEMBLY                                        │   │    │
│  tools/gene_sets.py — SynGO + BBB + controls + background │   │    │
│  Reads syngo1.3_complete_data/ Excel sources              │   │    │
│  Reads backend/nullifier/data/random_background_300.txt   │   │    │
│  Caches to ~/.nullifier/gene_sets_cache.pkl (7-day TTL)   │   │    │
│  Outputs: starter, expanded sets, controls, background    │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  AGENT: Analyst + genomic fetch                           │   │    │
│  tools/ensembl.py — lookup, orthologs, paralogs,          │   │    │
│  gene tree, regulatory features, motifs                   │   │    │
│  tools/gnomad.py — LOEUF / pLI constraint                 │   │    │
│  tools/phylo.py — phylostratigraphy age                   │   │    │
│  tools/paml.py — optional codeml branch-model omega       │   │    │
│  tools/r_bridge.py — Rscript seqinr::kaks pairwise dN/dS  │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  tools/genomic_data.py                                    │   │    │
│  Builds typed data dict for compute layer                 │   │    │
│  groups, variables, gene_index, provenance, rate_vectors  │   │    │
│  Uses mammal panel from backend/nullifier/data/            │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  AGENT: Methodologist                              Claude │   │    │
│  Reads: hypothesis + expansion + genomic data summary     │   │    │
│  Outputs: structured analysis plan (which tests to run)   │   │    │
│  Test menu: Mann-Whitney, Kruskal-Wallis, Spearman,       │   │    │
│  Pearson, Fisher's exact, BH/Bonferroni, bootstrap,       │   │    │
│  permutation, effect sizes, rate-vector tests             │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  tools/compute.py — DETERMINISTIC (no LLM)                │   │    │
│  Executes plan with local Python, NumPy, SciPy helpers    │   │    │
│  Returns typed test results, corrections, CI/effects      │   │    │
│  Also runs reproducibility check and leave-one-out        │   │    │
│  robustness/perturbation analysis                         │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  AGENT: Interpreter                                Claude │   │    │
│  Reads: deterministic compute results + genomic data      │   │    │
│  Outputs: assessment, interpretation, outlier genes,      │   │    │
│  limitations; marks saturated dN/dS axes untestable       │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  AGENT: Skeptic                                    Claude │   │    │
│  Reads: literature evidence + analyst result +            │   │    │
│         compute/robustness + raw top abstracts            │   │    │
│  Outputs: verdict, decomposed scores, alternatives,       │   │    │
│           decisive experiment, completed-analysis critique│   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ▼                                                 │    │
┌───────────────────────────────────────────────────────────┐   │    │
│  FULL REPORT ASSEMBLED                                    │   │    │
│  formalized + evidence + verdict + analyst_result         │   │    │
└─────────────┬─────────────────────────────────────────────┘   │    │
              │                                                 │    │
              ├──────────────────────────► store/runs.py        │    │
              │                            SQLite persistence   │    │
              │                                                 │    │
              ├──────────────────────────► WebSocket stream     │    │
              │                            events to frontend   │    │
              │                                                 │    │
              ▼                                                 │    │
       ┌───────────────────────────────────────────────────────┐│    │
       │  OUTPUT SURFACES                                      ││    │
       │                                                       ││    │
       │  CLI:  report/renderer.py (Rich terminal output)      ││    │
       │                                                       ││    │
       │  Web:  server.py serves backend/nullifier/static/     ││    │
       │        ├── HomePage      (submit hypothesis)          ││    │
       │        ├── RunPage       (live progress + report)     ││    │
       │        │   ├── EventTimeline                          ││    │
       │        │   ├── EvidencePanel                          ││    │
       │        │   ├── GeneSetPanel                           ││    │
       │        │   ├── ComputeResultsSection                  ││    │
       │        │   ├── RobustnessPanel                        ││    │
       │        │   ├── GenomicPanel / PhylogenyView           ││    │
       │        │   └── VerdictSection / CritiquePanels        ││    │
       │        ├── ReviewPage    (classification review)      ││    │
       │        ├── HistoryPage   (past runs from runs.db)     ││    │
       │        └── FlagsPage     (flag library)               ││    │
       └────────────────┬──────────────────────────────────────┘│    │
                        │                                       │    │
                        ▼                                       │    │
              ┌──────────────────────┐                         │    │
              │  USER FLAGS PAPER    │                         │    │
              │  Writes to           │                         │    │
              │  flag_store.py ──────┼──► used as few-shot examples
              │  (flags.db)          │     in next Librarian run
              └──────────────────────┘
```

## Data store summary

| Store | Location | Lifetime | Purpose |
|---|---|---|---|
| `config.toml` | `~/.nullifier/` | Persistent | User routing/cache/R/PAML overrides |
| `runs.db` | `~/.nullifier/` | Persistent | Run history + event replay |
| `flags.db` | `~/.nullifier/` | Persistent | User classification corrections |
| `ensembl_cache.db` | `~/.nullifier/` | 30-day TTL | Ensembl lookup, homology, Compara, regulatory responses |
| `gene_sets_cache.pkl` | `~/.nullifier/` | 7-day TTL by default | Parsed SynGO + BBB + control set data |
| `gnomad_cache.db` | `~/.nullifier/` | 30-day TTL | gnomAD constraint responses |
| `paml_cache.db` | `~/.nullifier/` | 90-day TTL | PAML/codeml branch-model omega results |
| `rdnds_cache.db` | `~/.nullifier/` | 90-day TTL | R/seqinr pairwise dN/dS results |
| `syngo1.3_complete_data/` | Repo | Static | Source SynGO Excel files |
| `backend/nullifier/data/` | Repo | Static | Phylo age table, mammal panel, random background panel |

## Backend routing summary

| Component | Backend | Volume |
|---|---|---|
| Formalizer | Claude | 2 calls / run |
| Query Expander | Claude | ~3–5 calls / run |
| Librarian per-paper | Local LM Studio | 30–50 calls / run |
| Librarian synthesizer | Claude | 3–5 calls / run |
| Gene-set classifier | Local LM Studio | candidate set scoring |
| Analyst splitter | Claude | 1 call / run when starter entities exist |
| Methodologist | Claude | 1 call / run |
| Compute layer | Python (no LLM) | n/a |
| Robustness reading | Local LM Studio | configured per perturbation |
| Robustness summary | Claude | 1 call / run when used |
| Provenance enrichment | Local LM Studio | configured enrichment calls |
| Interpreter | Claude | 1–2 calls / run |
| Skeptic | Claude | 1 call / run |

## Event flow

`pipeline.py` yields events via `events.py` → consumed by:

- `server.py` WebSocket → frontend `EventTimeline` and `RunPage`
- `store/runs.py` → persisted to `runs.db` for replay
- `report/renderer.py` → CLI Rich output after `run_completed`

Common event groups:

- Formalizer: `hypothesis_extracted`, `confirmation_required`, `confirmation_received`, `claims_formalized`
- Librarian: `queries_expanded`, `papers_retrieved`, `paper_classified`, `classifier_degraded`, `synthesis_ready`
- Analyst: `gene_sets_expanded`, `analyst_started`, `ensembl.batch_progress`, `analyst_gene_fetched`, `analyst_symbol_resolved`
- Genomic enrichments: `analyst_gnomad_fetched`, `analyst_phylo_loaded`, `paml.*`, `analyst_paml_complete`, `rdnds.*`, `analyst_rdnds_complete`
- Compute: `methodologist_plan_complete`, `compute_start`, `compute_test_complete`, `compute_all_complete`, `compute_robustness_start`, `compute_robustness_complete`
- Interpretation/verdict: `interpreter_start`, `interpreter_complete`, `analyst_ready`, `skeptic_critique_mode_active`, `verdict_ready`
- Run lifecycle: `run_started`, `stage_started`, `stage_completed`, `token_update`, `run_completed`, `run_failed`, `run_aborted`

## API flow

| Route | Purpose |
|---|---|
| `POST /api/runs` | Create a run and start the pipeline task |
| `GET /api/runs` | List historical runs |
| `GET /api/runs/{run_id}` | Return run metadata plus persisted events |
| `DELETE /api/runs/{run_id}` | Cancel a pending/running run |
| `GET /api/health` | Report LM Studio and R/PAML health |
| `GET /api/flags` | List/filter classification corrections |
| `POST /api/flags` | Store a user correction |
| `GET /api/flags/export` | Export corrections as JSON |
| `GET /ws/runs/{run_id}` | Replay and stream run events |
