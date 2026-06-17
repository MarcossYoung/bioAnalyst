# Nullifier

A multi-agent scientific hypothesis stress-tester. Takes a research proposal, extracts falsifiable claims, retrieves evidence from four federated literature databases, runs deterministic statistical tests against gene-set data, and delivers a structured verdict with scores, critique panels, and experiment recommendations.

Available as a **web UI** (FastAPI + React) or a **CLI** (Rich terminal output).

## Quick Start

**Requires Python 3.11+, Node.js 18+, and an Anthropic API key.**

```powershell
# From the repo root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

cd frontend
npm install
npm run build
cd ..

$env:PYTHONPATH="backend"
python -m nullifier.cli serve
```

Then open **http://127.0.0.1:8000**.

For a CLI smoke test:

```powershell
$env:PYTHONPATH="backend"
python -m nullifier.cli run --input examples/synapse_bbb.txt --max-papers 3
```

## How It Works

Six stages run in sequence:

1. **Formalizer** — Extracts the falsifiable core from scaffolding (methods, cited literature, starter data). Decomposes it into atomic claims with null hypotheses and normalizes common structured-output variants from the model. Detects whether the input includes completed analyses (reported statistics, sample sizes) and flags them for reproducibility checking. Presents the result for confirmation before the expensive retrieval step runs.

2. **Librarian** — For each atomic claim, generates 5–8 query variants, searches four literature sources in parallel, deduplicates, and classifies each paper as `supports / contradicts / tangential / confounder`. Every classification requires a verbatim abstract quote. Claim-level query or synthesis failures degrade to `absent / unstudied` evidence for that claim instead of ending the whole run.

3. **Analyst** _(when starter entities are provided)_ — Expands the hypothesis gene list against canonical SynGO and BBB gene sets (scored for relevance by a local Gemma classifier, with a heuristic fallback for set relevance). Expansion is split into primary sets used for compute and exploratory sets retained for context, so broad ontology hits do not automatically turn into thousands of expensive gene tests. Fetches Ensembl genomic data for the primary target genes (orthologs, dN/dS, regulatory features, motif overlap).

4. **Methodologist** — Reads the expanded gene-set data and selects an appropriate statistical test plan: which tests to run, which correction method to apply, which genes form each group.

5. **Compute + Robustness** — Executes the plan deterministically (Mann-Whitney U, Fisher's exact, bootstrap CI, etc.). Runs a leave-one-out sensitivity analysis to assess how much the result depends on any single gene.

6. **Interpreter → Skeptic** — The Interpreter reads typed compute results and issues a plain-language assessment. The Skeptic independently re-checks all evidence, scores across seven dimensions, identifies alternative explanations, and issues a verdict: `STRONG | MODERATE | WEAK | FALSIFIED | NOVEL-UNTESTED`. When the input included completed analyses, the Skeptic additionally produces a `RESULTS-PROBLEMATIC` critique covering methods, statistical rigor, reproducibility, and interpretation.

Per-paper classification and gene-set relevance scoring use a local LLM (LM Studio) for cost efficiency; all synthesis and reasoning steps use Claude.

## Literature Sources

| Source            | Coverage                                      |
| ----------------- | --------------------------------------------- |
| Semantic Scholar  | General scientific literature, citation graph |
| OpenAlex          | Open access, broad coverage                   |
| Europe PMC        | Life sciences, full-text indexed              |
| bioRxiv / medRxiv | Preprints (via Europe PMC `SRC:PPR`)          |

Sources are queried in parallel. If any source is unavailable, the run continues with the remaining ones (circuit breaker per host).

## Installation

**Requires Python 3.11+ and Node.js 18+**

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install and build the frontend
cd frontend
npm install
npm run build   # outputs to backend/nullifier/static/
cd ..
```

### R/seqinr for Pairwise dN/dS, Optional PAML for Branch-Model Omega

Current builds compute pairwise dN/dS with R `seqinr::kaks` from Ensembl Compara aligned CDS. This is the primary evolutionary-rate source used for set-level `dnds_mean` and Spearman/other statistical tests. It does not require `codeml`.

The app can also compute lineage-specific branch-model omega via PAML `codeml` when installed. That path is secondary and degrades gracefully when `codeml` is unavailable.

Install R 4.0+:

```bash
# macOS
brew install r

# Ubuntu
sudo apt install r-base
```

On Windows, install R from https://www.r-project.org/.

Install required R packages once:

```r
install.packages(c("ape", "phangorn", "seqinr", "caper"))
```

The Python bridge shells out to `Rscript`; no Python R binding such as `rpy2` is needed. Make sure `Rscript` is on `PATH`, or set `[r].r_home` in `~/.nullifier/config.toml` if R is installed in a non-standard location.

#### Optional: Installing codeml (PAML)

Install PAML so `codeml` is on `PATH`:

```bash
# macOS
brew install paml

# Ubuntu
sudo apt install paml
```

The startup health endpoint reports missing R packages and whether `codeml` is on `PATH`. Missing `codeml` disables only the secondary PAML branch-model omega calculation; pairwise dN/dS still runs through R/seqinr.

### API Keys

```dotenv
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Optional — improves Semantic Scholar rate limits
SEMANTIC_SCHOLAR_API_KEY=your-key
```

You can put these values in `.env`; the backend loads it automatically.

### Local LLM

Install [LM Studio](https://lmstudio.ai) and load a Gemma model (the default config uses `google/gemma-4-e4b`). The default routing uses the local model for high-volume per-paper classification, gene-set scoring, and robustness reading. If LM Studio is unavailable, tasks still routed to `local` can fail; gene-set scoring has a heuristic fallback, and Librarian subcall failures are recorded as degraded claim-level evidence rather than crashing the full run.

Check the loaded model ID via LM Studio's `/api/health` endpoint and set `backends.local.model` in your config to match exactly.

To run without LM Studio, edit `~/.nullifier/config.toml` and route the local tasks you need to `"claude"`.

## Configuration

On first run, a config file is created at `~/.nullifier/config.toml`. Edit it to change model routing, LM Studio endpoint, or pipeline behaviour.

```toml
[backends.claude]
provider = "anthropic"
model = "claude-sonnet-4-20250514"

[backends.local]
provider = "openai_compatible"
endpoint = "http://127.0.0.1:1234/v1"
model = "google/gemma-4-e4b"    # must match the model id loaded in LM Studio
api_key = "lm-studio"
parallel_requests = 2
request_timeout_seconds = 300

[routing]
librarian_per_paper   = "local"   # high-volume — local is ~10× cheaper
gene_set_classifier   = "local"   # v6: Gemma scores gene-set relevance
robustness_reading    = "local"   # v6: per-perturbation verdict reading
provenance_enrichment = "local"   # v6: provenance metadata enrichment
formalizer_stage1     = "claude"
formalizer_stage2     = "claude"
methodologist         = "claude"  # v6: picks statistical tests
interpreter           = "claude"  # v6: reads compute results
skeptic               = "claude"
# ... (see backend/nullifier/config/default_config.toml for full table)

[gene_sets]
cache_ttl_days = 7
min_score = 2
process_min_score = 3
max_primary_process_sets = 10
max_primary_set_size = 250

[compute]
alpha               = 0.05
default_correction  = "benjamini_hochberg"
bootstrap_iters     = 5000
permutation_iters   = 10000
```

## Web UI

```powershell
# From the repo root
$env:PYTHONPATH="backend"   # PowerShell; Linux/Mac: export PYTHONPATH=backend
python -m nullifier.cli serve
```

Then open **http://127.0.0.1:8000** in your browser.

- Paste your hypothesis text and submit
- Watch the live event timeline as the pipeline runs
- Confirm, edit, or abort the extracted hypothesis in the modal
- View evidence per claim, gene-set expansion, statistical test results, robustness panel, and the final verdict scorecard
- Two-verdict layout when input includes completed analyses: hypothesis verdict (left) + critique verdict (right)
- Browse past runs in the History page; review and correct paper classifications in the Flags page

For development with hot-reload:

```powershell
# Terminal 1 — backend
$env:PYTHONPATH="backend"   # PowerShell; Linux/Mac: export PYTHONPATH=backend
python -m nullifier.cli serve --reload

# Terminal 2 — frontend (Vite HMR at localhost:5173)
cd frontend && npm run dev
```

> **Security:** The server binds to `127.0.0.1` by default (localhost only). Do not expose it to a network without adding authentication.

## CLI

```powershell
# From the repo root — set PYTHONPATH so the package resolves
$env:PYTHONPATH="backend"   # Linux/Mac: export PYTHONPATH=backend

# Run the pipeline
python -m nullifier.cli run --input examples/synapse_bbb.txt

# With all options
python -m nullifier.cli run `
  --input hypothesis.txt `
  --output-json out.json `
  --max-papers 12 `
  --no-confirm

# Review and correct paper classifications
python -m nullifier.cli review out.json

# List accumulated corrections
python -m nullifier.cli flags list
```

| Flag            | Default  | Description                                                              |
| --------------- | -------- | ------------------------------------------------------------------------ |
| `--input`       | required | Path to hypothesis text file                                             |
| `--output-json` | none     | Save full report JSON for later review                                   |
| `--max-papers`  | 6        | Max papers retrieved per atomic claim in CLI runs; web/API default is 12 |
| `--no-confirm`  | off      | Skip the confirmation gate                                               |
| `--debug`       | off      | Print raw event stream alongside output                                  |

### Exporting the Last Run

Runs are stored in SQLite at `~/.nullifier/runs.db`. To export the latest run
as a self-contained JSON file, run this from the repo root:

```powershell
python scripts/export_last_run.py
```

The script writes a file next to the database, for example:

```text
C:\Users\Your Name\.nullifier\run_20260608T124534_8c296d26_export.json
```

To export a specific run or write to another directory:

```powershell
python scripts/export_last_run.py --run-id 8c296d26
python scripts/export_last_run.py --output-dir exports
python scripts/export_last_run.py --db "C:\Users\Your Name\.nullifier\runs.db"
```

| Flag            | Default  | Description                                                              |
| --------------- | -------- | ------------------------------------------------------------------------ |
| `--input`       | required | Path to hypothesis text file                                             |
| `--output-json` | none     | Save full report JSON for later review                                   |
| `--max-papers`  | 6        | Max papers retrieved per atomic claim in CLI runs; web/API default is 12 |
| `--no-confirm`  | off      | Skip the confirmation gate                                               |
| `--debug`       | off      | Print raw event stream alongside output                                  |

## Writing a Good Input File

The tool expects a free-form research memo — not a structured form. Include:

- The hypothesis you want tested (the tool will extract it)
- Any prior literature you're aware of (titles or descriptions)
- Proposed methods (excluded from falsification)
- Starter gene sets or entity names (used to anchor Ensembl queries and gene-set expansion)

To trigger the completed-analysis critique, include reported statistics — p-values, effect sizes, sample sizes, test names. The Analyst will attempt to cross-check them against Ensembl-retrievable values and flag what cannot be verified from Ensembl alone.

See `examples/synapse_bbb.txt` for a pure-hypothesis example (BBB / synaptic gene co-evolution).

## Project Structure

```
bioAnalyst/
├── backend/
│   └── nullifier/
│       ├── cli.py              CLI entrypoint (run, serve, review, flags)
│       ├── server.py           FastAPI app — REST + WebSocket + static serving
│       ├── pipeline.py         Event-yielding orchestrator
│       ├── events.py           Event dataclass + factory functions
│       ├── agents/
│       │   ├── formalizer.py
│       │   ├── librarian.py
│       │   ├── analyst.py      Legacy v5 Ensembl interpretation
│       │   ├── methodologist.py  v6: statistical test planning
│       │   ├── interpreter.py    v6: reads compute results
│       │   └── skeptic.py
│       ├── tools/
│       │   ├── llm_client.py       Unified Claude / LM Studio router
│       │   ├── ensembl.py          Ensembl REST client + SQLite cache
│       │   ├── literature.py       Federated search orchestration
│       │   ├── gene_sets.py        v6: SynGO + BBB expansion + Gemma scoring
│       │   ├── compute.py          v6: deterministic statistical tests
│       │   ├── genomic_data.py     v6: typed data builder for compute layer
│       │   ├── provenance.py       v6: provenance record construction
│       │   ├── flag_store.py       SQLite flag DB + few-shot injection
│       │   └── sources/            semantic_scholar, openalex, europe_pmc, biorxiv
│       ├── store/runs.py       SQLite runs + events DB (~/.nullifier/runs.db)
│       ├── config/             TOML config loader + defaults
│       ├── review/             Interactive CLI flag-review TUI
│       ├── report/             Rich terminal renderer (CLI only)
│       └── static/             Built frontend assets (gitignored, from npm run build)
├── frontend/                   Vite + React + TypeScript + Recharts
│   └── src/
│       ├── pages/              RunPage, HistoryPage, ReviewPage, FlagsPage
│       └── components/         VerdictSection, ComputeResultsSection, GeneSetPanel,
│                               RobustnessPanel, EvidencePanel, GenomicPanel, …
└── examples/
    └── synapse_bbb.txt
```

The FastAPI server serves the built SPA from `backend/nullifier/static/`: `/assets/*` from a precise mount, and a catch-all that returns `index.html` for all other paths so React Router deep links (`/history`, `/flags`, …) resolve instead of 404ing.

## Verdicts

| Verdict               | Meaning                                                                                                                          |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `STRONG`              | Multiple independent supporting lines; no credible contradictions                                                                |
| `MODERATE`            | Net support but meaningful uncertainty or gaps                                                                                   |
| `WEAK`                | Contradicted, methodologically limited, or very sparse evidence                                                                  |
| `FALSIFIED`           | Direct contradicting evidence; hypothesis as stated is untenable                                                                 |
| `NOVEL-UNTESTED`      | No prior literature found; hypothesis is uninvestigated, not disproven                                                           |
| `RESULTS-PROBLEMATIC` | Input included completed analyses; Skeptic found HIGH-severity issues in methods, statistics, reproducibility, or interpretation |

When `RESULTS-PROBLEMATIC` is issued, the UI renders two verdict cards side-by-side: the left card shows the underlying hypothesis verdict (derived from the falsifiability score); the right card shows the critique breakdown.

## Cost

Typical run on a 2–3 claim hypothesis with LM Studio running:

| Component                                                                  | Approximate cost              |
| -------------------------------------------------------------------------- | ----------------------------- |
| Claude calls (formalize, synthesize, methodologist, interpreter, skeptic)  | $0.08–0.18                    |
| Local LLM (per-paper classification, gene-set scoring, robustness reading) | ~$0                           |
| Ensembl lookups                                                            | free (cached after first run) |
| **Total**                                                                  | **~$0.08–0.20**               |

If you reroute all local tasks to Claude, expect roughly ~$0.25–0.60 per run.

## Design Decisions

- **No LangChain, no vector database.** Plain Python, `requests`, SQLite, thread pool.
- **Hybrid routing.** High-volume classification and scoring use a local model; reasoning-heavy tasks use Claude. Routing is configurable per task in `~/.nullifier/config.toml`.
- **Event-driven pipeline.** `pipeline.py` is a synchronous generator yielding typed events. The CLI drains them to a Rich console; the server fans them out over WebSocket to all connected clients.
- **Deterministic compute layer.** Statistical tests in `tools/compute.py` are pure functions — same data always produces the same result. The Methodologist chooses which tests to run; compute just executes them.
- **Focused gene-set compute.** Gene-set expansion keeps broad matching catalogs as exploratory context, while only primary, size-capped sets enter expensive genomic data fetching and statistical tests.
- **Graceful degradation.** Each literature source is independently wrapped with a circuit breaker. LM Studio being unavailable is surfaced at startup; Librarian query and synthesis failures degrade individual claims where possible, and unrecoverable task errors emit `run_failed` events instead of crashing the server.
- **Verbatim quote requirement.** Every paper classification must include a verbatim abstract sentence, enforced in Librarian prompts and checked by the Skeptic.
- **Flag learning is prompting, not fine-tuning.** User corrections are stored in the configured `flags.db_path` (default `~/.nullifier/flags.db`) and injected as few-shot examples on future runs with matching domain/entities.
- **`NOVEL-UNTESTED` is not `WEAK`.** A hypothesis with no prior literature is uninvestigated, not disproven.
