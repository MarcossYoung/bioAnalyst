# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Nullifier v6** is a multi-agent scientific hypothesis stress-tester with a web UI and CLI. It extracts falsifiable claims, retrieves evidence from four federated databases, expands starter genes into canonical gene sets, runs deterministic statistical tests over Ensembl-derived data, and delivers a structured verdict. Hybrid LLM routing: high-volume classification/scoring can use LM Studio (local), while reasoning-heavy synthesis uses Claude.

## Commands

All Python commands run from the repo root with `PYTHONPATH=backend`.

**Install:**
```powershell
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
```

**Run pipeline (CLI):**
```powershell
$env:PYTHONPATH="backend"
python -m nullifier.cli run --input examples/synapse_bbb.txt
python -m nullifier.cli run --input hypothesis.txt --output-json out.json --max-papers 12 --no-confirm --debug
```

**Start web server:**
```powershell
$env:PYTHONPATH="backend"
python -m nullifier.cli serve              # http://127.0.0.1:8000
python -m nullifier.cli serve --reload     # with auto-reload (dev)
```

**Frontend dev (HMR at localhost:5173, proxies API to :8000):**
```powershell
cd frontend && npm run dev
```

**Rebuild frontend after changes:**
```powershell
cd frontend && npm run build   # outputs to backend/nullifier/static/
```

**Review and correct past classifications:**
```powershell
python -m nullifier.cli review out.json   # k=keep, f=flag, q=quit
python -m nullifier.cli flags list
```

**Required env vars:** `ANTHROPIC_API_KEY`. Optional: `SEMANTIC_SCHOLAR_API_KEY`.

## Architecture

```
hypothesis.txt
    → pipeline.py (event generator)
        → Formalizer (stage1 + stage2)
        → Librarian  (per-claim: expand queries → search → classify → synthesize)
        → Analyst    (gene-set expansion + Ensembl data fetch)
        → Methodologist (statistical test plan)
        → Compute + Robustness (deterministic tests + leave-one-out)
        → Interpreter (reads typed compute output)
        → Skeptic    (stress-test + 7-dimension scores + verdict)
    → CLI: Rich console renderer
    → Server: WebSocket fan-out to browser
```

**`pipeline.py`** — synchronous generator yielding `Event` objects. Checked by `cancel_check` before each stage. Errors are caught and emitted as `run_failed` events (no re-raise).

**`server.py`** — FastAPI app. Pipeline runs in a daemon `threading.Thread`, puts events into a `queue.Queue`. `_execute_run` async task drains via `asyncio.to_thread`, persists to SQLite, and fans out to per-client `asyncio.Queue`s. Confirmation gate uses `concurrent.futures.Future` — pipeline blocks on `cf.result(timeout=600)`; WebSocket handler calls `cf.set_result()`.

**`events.py`** — `Event` dataclass + factory functions. All inter-component communication goes through typed events. Also owns the **section-based confirmation gate** helpers: `SECTION_SPECS`, `build_confirm_sections(stage1)`, `apply_section_edits(stage1, edits)` — the `confirmation_required` payload is `{sections, domain}` and the client replies with `{type:"confirm_sections", edits:{<section_id>:{action:"keep"|"edit"|"remove", value?}}}`.

**Completed-analysis critique**: Formalizer Stage 1 also extracts optional `methods_used` and `completed_analysis` (`[{finding, statistic, test, sample_size, interpretation}]`). When `completed_analysis` is non-empty, the pipeline emits `formalizer_detected_completed_analysis`, `tools.compute.verify_reported_stats` checks what can be reconstructed from Ensembl-derived values, the Interpreter receives the reproducibility result, and the Skeptic appends critique fields: `methods_critique` / `statistical_critique` / `reproducibility_check` / `interpretation_critique` (`{severity, issues[], notes}`), four `*_critique_score`/`reproducibility_score` rows, and the verdict `RESULTS-PROBLEMATIC` when warranted. Flags are exposed over HTTP (`GET/POST /api/flags`, `GET /api/flags/export`) and in the web UI (inline flag in `EvidencePanel`, `/runs/:id/review`, the `/flags` library).

**`store/runs.py`** — SQLite at `~/.nullifier/runs.db`. Tables: `runs` (status, results JSON) + `run_events` (full event log by seq). WebSocket clients replay from DB on reconnect.

**`tools/llm_client.py`** — `llm_call_json(task_name, system, user)` routes to Claude or LM Studio per `cfg["routing"][task_name]`. `llm_call_json_batch` parallelises with `ThreadPoolExecutor`. Module-level `TRACKER` accumulates token counts.

**`tools/ensembl.py`** — Ensembl REST client. SQLite cache at `~/.nullifier/ensembl_cache.db` by default, configurable via `ensembl.cache_path`. The default rate limit is 14 req/s. Returns `None` on failure (never throws).

**Analyst path** — `pipeline.py` runs this path whenever `starter_entities` is non-empty. `tools/gene_sets.py` expands starter entities against SynGO, BBB, and control sets; `agents/analyst.py` supplies fetch/stat compatibility helpers; `tools/genomic_data.py` builds typed data; `agents/methodologist.py` chooses tests from `tools/compute.py`; `agents/interpreter.py` reads compute/robustness/reproducibility output.

**`frontend/`** — Vite + React + TypeScript + Recharts. Tailwind v4 is imported (`@import "tailwindcss"` in `index.css`) but only a few utilities are used (e.g. `animate-spin`); page/component styling is **inline styles + CSS custom properties** defined in `index.css` (`--oxford`, `--bg`, `--surface`, `--border`, `--text-*`, `--verdict-*`) — an "academic dashboard" theme with dark `#0f172a` headers. `RunPage` is a single scrolling document (no tabs): Hypothesis → Evidence → Genomic analysis → Gene-set expansion → Statistical analysis → Robustness → Verdict. `vite.config.ts` proxies `/api` and `/ws` to `:8000` in dev. Production build goes to `backend/nullifier/static/`.

**Static serving / SPA routing (`server.py`)** — `/assets` is a precise `StaticFiles` mount; `/favicon.svg` and `/icons.svg` have explicit `FileResponse` routes; everything else is a `GET /{full_path:path}` catch-all returning `index.html` (registered last, after all `/api` and `/ws` routes) so React Router deep links like `/history` and `/flags` resolve instead of 404ing.

## Key Conventions

- **LLM calls**: Always use `tools/llm_client.py` — never call the Anthropic SDK or OpenAI client directly from agents. Handles JSON fence stripping, one retry, and token tracking.
- **Verbatim quotes**: Every paper classification must include a `quote` field with a verbatim abstract sentence. Enforced in Librarian prompts and verified by Skeptic.
- **Graceful degradation**: Literature sources are independently wrapped — one failure doesn't abort the run. `tools/literature.py` has a per-run `SourceHealth` circuit breaker keyed by **host** (`europe_pmc` + `biorxiv` share `www.ebi.ac.uk`): after 2 soft failures — or 1 connection/read-timeout — that host is skipped for the rest of the run. `retrieve_evidence` creates one `SourceHealth` and threads it through every `federated_search`. Per-claim query-variant searches run concurrently with a ~45 s wall-clock backstop; per-request timeouts are `(4, 8)` s and `federated_search`'s fan-out is capped at ~10 s. Ensembl calls return `None` on failure. LM Studio absence is surfaced at server startup; tasks still routed to `local` can fail unless config routes them elsewhere. Gene-set relevance has a heuristic fallback.
- **Event emission from blocking calls**: Librarian and Analyst collect events in a list via `on_event` callback, yield them after the blocking call returns (not true streaming, but avoids threading complexity).
- **No `raise` after `run_failed`**: `pipeline.py` catches all exceptions, yields `run_failed`, and terminates cleanly. The server has its own catch for safety but double-`run_failed` should never happen.
- **WebSocket subscriber ordering**: Subscribers are registered before DB replay so live events can't be missed (single-threaded event loop prevents the race).
- **Windows console encoding**: `cli.py` calls `sys.stdout/stderr.reconfigure(encoding="utf-8")` at import — Windows consoles default to cp1252, which can't encode characters Rich emits (box drawing, `₀` subscripts in dN/dS values, `▶`/`✓` glyphs). Don't strip this; without it `nullifier run` crashes mid-render on Windows.
