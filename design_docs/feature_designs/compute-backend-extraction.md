# Compute boundary extraction — Phase A: the `ComputeBackend` seam

## Context

Nullifier already implements 3 of the 4 layers of the semantic-architecture
model (`.claude/skills/semantic-architecture.md`): `agents/semantic.py` defines
`AgentSpec` + `OutputContract` (Layer 1), `TaskObject` (Layer 2), and
`WorkflowStep` / `PIPELINE_WORKFLOW` (Layer 3), and every agent uses them. The
only layer not separated is **Layer 4 — the executable runtime**: deterministic
data-fetch + statistical compute still run in-process, pulling `numpy`, `scipy`,
the R bridge (`Rscript`), and PAML (`codeml`) into the same process as the
FastAPI server and the LLM agents.

The goal is to make that runtime **extractable behind a single typed,
serializable contract** so it can later move out-of-process (keeping the core
light) and so new statistical methods become pluggable behind one interface.
The `compute` `WorkflowStep` already declares the seam — `inputs=("plan","data")`
→ `outputs=("compute_results","robustness")`.

**This pass is the seam only (no service yet).** Define a `ComputeBackend`
protocol with an in-process implementation that wraps today's functions, route
`pipeline.py` through it, and add a serializability guard test that *proves* the
payload is wire-ready. Flipping to an HTTP backend later becomes a localized
change with no caller edits.

Deferred to later passes (out of scope here, recorded so the seam is shaped for
them):
- **Phase B** — FastAPI compute service + `HttpBackend` selected by config; the
  intended end state is "service owns data + compute" (the heavy stack —
  `tools/ensembl.py`, `gnomad.py`, `paml.py`, `phylo.py`, `dnds.py`,
  `branch_rates.py`, `diagnostics.py`, `r_bridge.py`, `rerconverge.py`,
  `genomic_data.py`, `compute.py` — moves behind the API; the core sends gene
  IDs/sets and receives typed summaries).
- **Phase C** — split requirements/containers so the light core image omits
  `scipy`/R/PAML/biopython.

## Constraints the cut must respect (verified against current code)

- **LLM agents bracket compute.** `run_methodologist` (picks tests from
  `data_summary`) runs *before* compute; `run_interpreter` (reads results) runs
  *after*. Both stay in the core. So the backend covers **fetch + compute +
  robustness only**, not the whole analyst path.
- **A run-scoped data handle is required.** Because the methodologist (core, LLM)
  sits between fetch and compute, the backend must hold the heavy fetched
  `gene_data`/`data` and return a small handle. Today's
  `rebuild_data=lambda excl: build_data(...)` closure (`pipeline.py:141-149`) is
  **not serializable** and must never cross the seam — the in-process backend
  reconstructs it from the stored data, keyed by the handle. This is the linchpin
  that makes the boundary wire-ready.
- **Cross-boundary payloads are small.** `_data_summary` (`tools/compute.py:1377`)
  is counts + metadata; `compute_results`/`robustness`/set-stats are summaries.
  The heavy `gene_data` (sequences, alignments) never leaves the backend. The
  agents already use only stdlib `statistics`; `numpy/scipy/R/PAML` live entirely
  in `tools/`.

## Plan

### 1. New `backend/nullifier/compute_backend.py`

Typed, JSON-serializable dataclasses mirroring the future wire contract:

- `FetchRequest`: `all_targets`, `expansion`, `formalized`, `starter_entities`,
  `completed_analysis` (the current `run_analyst` args).
- `FetchResult`: `data_handle: str` + the small downstream fields the core needs —
  `data_summary`, `reproducibility`, `set_a`, `set_b`, `set_a_stats`,
  `set_b_stats`, `cross_set`, `dnds_saturation`, `phylo_data`, `diagnostics`,
  `risk_filter`, `data_provenance`. (Everything the `analyst_result` dict in
  `pipeline.py` reads except the heavy `gene_data`.)
- `ComputeResult`: `compute_results`, `robustness`.

`ComputeBackend` protocol:
- `fetch(req: FetchRequest, on_event=None) -> FetchResult`
- `compute(data_handle: str, plan: dict, on_event=None) -> ComputeResult`
- `release(data_handle: str) -> None`

`InProcessComputeBackend(ComputeBackend)`:
- `fetch`: call existing `run_analyst(...)`; store the full returned dict (incl.
  `gene_data`, `data`, `gnomad_data`, `phylo_data`, `paml_data`, `rdnds_data`) in
  a `dict[str, dict]` keyed by a generated `uuid4` handle; set
  `analyst_data["data"]["paml"] = paml_data` here (moved from the current
  `pipeline.py` line that does this post-`run_analyst`); return the small
  `FetchResult`.
- `compute`: look up stored data by handle, rebuild the exact `rebuild_data`
  closure server-side, call existing
  `run_compute(plan, data, starter_entities, rebuild_data, on_event)`, return
  `ComputeResult`.
- `release`: drop the handle entry (pipeline calls it after the interpreter, in a
  `finally`, so per-run data doesn't accumulate).

`get_compute_backend(cfg)` factory: returns `InProcessComputeBackend` for
`[compute] backend = "inprocess"`; raises `NotImplementedError("http")` as the
explicit placeholder for Phase B.

Keep `agents/analyst.py:run_analyst` and `agents/compute.py:run_compute`
**unchanged** — the backend wraps them. This keeps the existing 86 compute tests
valid and the diff small.

### 2. `backend/nullifier/pipeline.py` — route the analyst block through the backend

In the analyst branch:
- construct `backend = get_compute_backend(cfg)` once;
- replace the `run_analyst(...)` call with
  `res = backend.fetch(FetchRequest(...), on_event=analyst_events.append)`;
- feed `res.data_summary` to `run_methodologist`;
- replace `run_compute(...)` with
  `backend.compute(res.data_handle, plan, on_event=compute_events.append)`;
- build `analyst_result` from `res` + the `ComputeResult` (same shape as today);
- `backend.release(res.data_handle)` in a `finally`.

The `gene_data` the interpreter needs: in Phase A `FetchResult` may carry it
(in-process, free); mark it `# wire-excluded` so Phase B knows to replace it with
a server-side interpreter call or a gene-data summary. (Document, don't solve now.)

### 3. `backend/nullifier/config/default_config.toml`

Add `[compute]` with `backend = "inprocess"`. Read via the existing loader overlay
so no user config changes are needed.

## Verification

- **Serializability guard (the key artifact):** new
  `backend/tests/test_compute_backend.py` round-trips `FetchRequest`,
  `FetchResult`, `ComputeResult` through `json.dumps`/`loads` and asserts the
  payload survives — failing loudly if a closure or `numpy` array is smuggled
  across the seam. This is what locks the boundary as wire-ready.
- **Parity:** run a fixture hypothesis through `InProcessComputeBackend`
  (fetch → compute) and assert `compute_results` / `robustness` are identical to
  calling `run_analyst` + `run_compute` directly. No behavior change.
- **Regression:** existing 86 compute tests pass unchanged.
- **End-to-end CLI:**
  `PYTHONPATH=backend python -m nullifier.cli run --input examples/synapse_bbb.txt --no-confirm --debug`
  — same `analyst`/`compute` events fire, same verdict as `main`; confirm no
  per-run handle leak (store empty after run).
