# Desktop App Packaging - Design Doc

**Status:** Design only (no code yet)  
**Date:** 2026-06-16  
**Goal:** Turn the source-only `nullifier` project into a downloadable,
double-clickable desktop app a non-technical user can install and run, while
keeping the desktop bundle light enough to maintain as the scientific method
list grows.

---

## Current Direction

The desktop app should be a **thin local application**:

- native window via `pywebview`
- local FastAPI backend serving the built React SPA
- user settings, API keys, local history, and report rendering
- LLM provider routing
- compute/genomics jobs submitted through a backend abstraction

The heavy scientific runtime should move behind a **compute/genomics engine API**.
That engine can run remotely for normal users, locally as an advanced sidecar,
or fall back to system-installed tools when available.

---

## Confirmed / Updated Decisions

| Area | Decision | Rationale |
|------|----------|-----------|
| **Packaging** | **PyInstaller + pywebview** | One executable launches a native window showing the existing web UI. No browser/terminal needed. Much lighter than Electron. |
| **Targets** | **Windows + Linux** | Built on their own CI runners. macOS remains out of scope for v1. |
| **LLM providers** | **Provider profiles + task routing** | Users may have Anthropic, OpenAI, Gemini, or local LM Studio. Routing should be configurable per task. |
| **LM Studio usage** | **Preset + custom routing** | Users can choose LM Studio for librarian only, all tasks, no tasks, or custom per-agent routing. |
| **Genomics / compute** | **Remote compute service preferred for v1** | Keeps desktop install small, lowers user CPU cost, avoids bundling R/codeml/HyPhy/etc. into the desktop app. |
| **Local genomics** | **Fallback / advanced option** | Use system R/codeml if installed, or later run a local sidecar implementing the same compute API. |
| **API keys** | **In-app Settings page** writing `~/.nullifier/config.toml` | App is usable without environment variables. Keys are redacted in API responses. |

---

## Why This Fits The Existing Architecture

The current codebase is already close to distributable:

- **Single origin.** Vite builds the React SPA into `backend/nullifier/static/`, and
  FastAPI serves it with explicit static routes plus a catch-all for React Router.
- **Graceful degradation.** R/PAML helpers already return health/status objects
  instead of crashing. That makes it practical to switch among remote compute,
  local sidecar, system tools, or disabled compute.
- **Config overlay.** `config/loader.py` already merges `~/.nullifier/config.toml`
  over packaged defaults. We need to add safe write/update support.
- **All persistent state in one place.** User data lives under `~/.nullifier/`
  (`runs.db`, `config.toml`, caches, flags), so the bundle can remain read-only.

```
Nullifier.exe / AppImage
  -> PyInstaller one-dir bundle
      -> Python runtime + nullifier backend
      -> prebuilt React SPA
      -> packaged default_config.toml
      -> static data files

Runtime:
  desktop.py picks a free port
  -> starts uvicorn in a daemon thread
  -> opens pywebview window at http://127.0.0.1:<port>
  -> stores user state in ~/.nullifier/
  -> sends heavy compute jobs to the selected compute engine
```

---

## LLM Provider And Routing Model

The app should move from a fixed `claude` / `local` split to named provider
profiles. A route points to a provider, not a hardcoded implementation.

Example config shape:

```toml
[providers.anthropic]
enabled = true
api_key = ""
model = "claude-sonnet-4-20250514"

[providers.openai]
enabled = false
api_key = ""
model = "gpt-4.1"

[providers.gemini]
enabled = false
api_key = ""
model = "gemini-2.5-pro"

[providers.lmstudio]
enabled = false
endpoint = "http://127.0.0.1:1234/v1"
api_key = "lm-studio"
model = ""
parallel_requests = 1
request_timeout_seconds = 300

[routing]
formalizer_stage1 = "anthropic"
formalizer_stage2 = "anthropic"
query_expander = "anthropic"
librarian_per_paper = "lmstudio"
librarian_synthesizer = "anthropic"
methodologist = "anthropic"
interpreter = "anthropic"
gene_set_classifier = "lmstudio"
robustness_reading = "lmstudio"
robustness_summary = "anthropic"
provenance_enrichment = "lmstudio"
skeptic = "anthropic"
```

### Settings Presets

The Settings page should expose simple presets first:

- **Cloud only:** no local LLM CPU cost; requires API keys.
- **LM Studio for librarian:** local model handles high-volume paper
  classification; reasoning-heavy tasks remain cloud.
- **All LM Studio:** lowest API spend, highest local CPU/GPU load.
- **Custom:** explicit per-task routing.

For CPU-friendly defaults, LM Studio should use `parallel_requests = 1`.

### Provider Client Changes

`tools/llm_client.py` should become provider-based:

- resolve a task route from `cfg["routing"][task_name]`
- look up that provider under `cfg["providers"]`
- instantiate the correct SDK/client
- support Anthropic, OpenAI-compatible providers, Gemini, and LM Studio
- validate missing keys at call time with helpful errors
- track usage/cost per provider where available

OpenAI-compatible providers can share most of the LM Studio client path when
their APIs follow the OpenAI chat/completions contract.

---

## Compute / Genomics Engine Direction

The method list is expected to grow: new experiments, new calculations, and new
scientific runtimes. That argues against bundling every scientific dependency
inside the desktop application.

Instead, introduce a `ComputeEngine` boundary:

```
Desktop backend
  -> ComputeEngine client
      -> remote service
      -> local sidecar
      -> system-local tools
      -> disabled engine
```

The desktop should ask the engine for capabilities and submit jobs, rather than
hardcoding all binary/runtime details into the UI bundle.

Example config:

```toml
[compute_engine]
mode = "remote"  # remote | local_sidecar | system_tools | disabled
base_url = "https://compute.nullifier.app"
local_url = "http://127.0.0.1:8765"
api_key = ""
request_timeout_seconds = 30
poll_interval_seconds = 2
```

### Engine API Sketch

The same API should be implemented by the hosted service and any local sidecar:

```text
GET  /health
GET  /capabilities
GET  /methods
POST /jobs
GET  /jobs/{job_id}
GET  /jobs/{job_id}/results
POST /jobs/{job_id}/cancel
```

Example method metadata:

```json
{
  "id": "paml_branch_model",
  "label": "PAML branch model",
  "inputs": ["codon_alignment", "tree", "foreground"],
  "outputs": ["omega_foreground", "omega_background", "lrt_pvalue"],
  "cost_class": "heavy",
  "estimated_runtime": "30s-5m",
  "available": true,
  "version": "paml-4.10.7"
}
```

### Remote Compute Service

The remote engine is the preferred v1 path for nontechnical users.

Pros:

- small desktop installer
- lower user CPU cost
- no separate R/codeml/HyPhy installation
- reproducible scientific environment
- centralized updates for R packages and scientific tools
- easier method expansion over time
- server-side caching of expensive results

Trade-offs:

- requires internet
- introduces infrastructure cost
- needs auth, rate limits, job queues, and logs
- privacy/data handling must be explicit
- long-running jobs need async polling and cancellation

### Local Sidecar Service

A local sidecar is an optional compute engine running on the user's machine,
separate from the desktop app. It exposes the same API as the remote service.

```
Desktop app
  -> http://127.0.0.1:8765/health
  -> http://127.0.0.1:8765/jobs
```

The sidecar owns:

- R environment
- codeml
- HyPhy
- future scientific CLIs
- job timeouts
- compute cache
- CPU/concurrency limits
- runtime logs

Important: a sidecar does **not** reduce total CPU use because computation still
runs locally. It does reduce desktop bundle size, startup fragility, dependency
conflicts, and update pain.

Possible sidecar implementations:

| Option | Pros | Cons |
|--------|------|------|
| **Micromamba sidecar** | No Docker required; reproducible env; friendlier than manual R install | Still large; OS-specific; local CPU load remains |
| **Docker/Podman sidecar** | Strong isolation and reproducibility; good for labs/power users | Poor default for nontechnical Windows users; Docker Desktop is heavy |
| **Separate native worker** | Can be installed/updated independently; starts only when needed | Still has to package scientific runtimes somewhere |

The strategic move is to make remote and sidecar deployments share the same API.
Then "local" and "remote" are engine URLs, not separate product architectures.

### System-Local Fallback

The current `r_bridge.py` and `paml.py` behavior remains useful:

- detect `Rscript` and `codeml` on PATH
- allow configured paths
- return unavailable/health states instead of crashing

This should remain as an advanced fallback for users who already have local tools.

---

## V1 Product Recommendation

For v1:

1. Ship the **desktop app without bundled R/codeml**.
2. Add provider-based LLM routing and Settings UI.
3. Add a compute engine abstraction.
4. Make **remote compute** the recommended genomics path.
5. Keep system-local R/codeml fallback.
6. Design the local sidecar as a later deployment of the same engine API.

This keeps the v1 desktop installer smaller, lowers CPU burden for normal users,
and leaves room for the method list to grow without turning desktop packaging into
the main engineering bottleneck.

---

## Work Areas

### A. Config write path

- **`config/loader.py`** - add `save_user_config(updates)`.
- Deep-merge updates into `~/.nullifier/config.toml`.
- Write with `tomli-w` (new dependency).
- Reuse existing `CONFIG_DIR`, `CONFIG_PATH`, and merge logic.
- Avoid echoing raw API keys through logs or API responses.

### B. Provider-based LLM client

- **`tools/llm_client.py`** - replace hardcoded `claude` / `local` routing with
  provider profiles.
- Support:
  - Anthropic
  - OpenAI
  - Gemini
  - OpenAI-compatible local endpoints such as LM Studio
- Add per-provider health checks.
- Add per-provider usage/cost tracking where the SDK exposes token usage.
- Preserve batch routing for librarian-style tasks.
- Default local parallelism to `1`.

### C. Backend settings API

- **`server.py`**
  - `GET /api/config` - redacted view of providers, routing, LM Studio status,
    compute engine mode, and compute/genomics health.
  - `POST /api/config` - save provider settings, API keys, routing, LM Studio
    endpoint/model, and compute engine settings.
  - Register these routes before the SPA catch-all.

### D. Frontend Settings page

- **`frontend/src/pages/SettingsPage.tsx`**
  - provider cards for Anthropic, OpenAI, Gemini, and LM Studio
  - masked API key inputs
  - model fields
  - LM Studio endpoint/model/parallelism controls
  - routing presets: cloud only, LM Studio librarian only, all LM Studio, custom
  - compute engine selector: remote, local sidecar, system tools, disabled
  - read-only health/capability panel
- **`frontend/src/App.tsx`**
  - add `/settings` route and nav access.
- First-run guard:
  - guide the user to Settings if no usable provider is configured.

### E. Compute engine client

- New **`backend/nullifier/compute_engine/`** package:
  - `client.py`
  - `types.py`
  - `remote.py`
  - `system_tools.py`
  - `disabled.py`
- Common operations:
  - `health()`
  - `capabilities()`
  - `methods()`
  - `submit_job(method_id, payload)`
  - `get_job(job_id)`
  - `get_results(job_id)`
  - `cancel_job(job_id)`
- The first implementation may wrap existing local functions while the remote
  service is being built.

### F. Desktop launcher

- New **`backend/nullifier/desktop.py`**
  - pick a free localhost port
  - start uvicorn in a daemon thread
  - poll `/api/health` until ready
  - open pywebview window
  - fallback to `webbrowser` if native renderer is unavailable
- Add:
  - `python -m nullifier.desktop`
  - CLI `desktop` subcommand
  - `nullifier-desktop = "nullifier.desktop:main"` script entry

### G. Frozen-path resolution

- New **`backend/nullifier/resources.py`**
  - `resource_path(rel)` uses `Path(sys._MEIPASS)` when frozen.
- Route bundled-data lookups through it:
  - static frontend
  - packaged `default_config.toml`
  - SynGO data
  - panels/static data
- This remains important even without bundled R/codeml.

### H. Packaging

- **`packaging/nullifier.spec`**
  - include static UI, default config, SynGO data, favicon/icons, static data
  - hidden imports for FastAPI/uvicorn/provider SDKs/dynamic agent modules
  - one-dir build for faster startup and easier debugging
- **Installers**
  - Windows: Inno Setup
  - Linux: tarball or AppImage

### I. Build orchestration + CI

- **`packaging/build.{ps1,sh}`**
  - frontend install/build
  - PyInstaller build
  - installer step
- **`.github/workflows/desktop-build.yml`**
  - matrix: `windows-latest`, `ubuntu-latest`
  - setup Node and Python
  - build per OS
  - upload artifacts

### J. Dependency cleanup

- Add runtime:
  - `pywebview`
  - `tomli-w`
  - provider SDKs as needed
- Add build-only:
  - `pyinstaller`
- Fix stale dependency:
  - remove `rpy2>=3.5.0` from `backend/pyproject.toml`; dN/dS currently uses
    `Rscript` via subprocess, not `rpy2`.

---

## What Is Not Bundled In The Desktop

- LM Studio
- R
- codeml
- HyPhy
- future heavy scientific runtimes

These are provided by one of:

- remote compute engine
- local sidecar
- system-local tools
- disabled/unavailable capability state

---

## CPU-Cheap Defaults

Recommended default profile:

```toml
[providers.lmstudio]
parallel_requests = 1

[literature]
max_papers_per_claim = 4
parallel_search = false

[selection]
enabled = false

[compute]
bootstrap_iters = 1000
permutation_iters = 2000

[paml]
auto_run = false

[r]
pairwise_dnds_fallback = false
```

Expose stronger settings as "balanced" and "thorough" profiles rather than
making the first-run experience expensive by default.

---

## Verification When Implemented

1. **Desktop launch:** `python -m nullifier.desktop` opens a native window.
2. **First-run Settings:** no env vars set; app guides user to configure at
   least one provider.
3. **Provider routing:** Anthropic/OpenAI/Gemini/LM Studio routes can each run a
   small JSON task.
4. **LM Studio presets:** librarian-only, all-local, none-local, and custom
   routing save correctly.
5. **Compute engine health:** Settings shows remote/local/system/disabled status.
6. **Remote compute:** a compute job submits, polls, completes, and returns
   results through the same pipeline shape expected by the report.
7. **System fallback:** with local R/codeml installed, system-tools mode works.
8. **No local genomics:** without R/codeml and without remote compute, report
   degrades gracefully and explains unavailable methods.
9. **Frozen build:** Windows and Linux builds launch without source paths.
10. **Regression:** existing backend tests still pass.

---

## Known Trade-offs / Risks

- **Remote compute introduces infrastructure.** It needs auth, quotas, job
  queues, logs, caching, and clear privacy posture.
- **Local sidecar still uses local CPU.** It mainly improves packaging and
  dependency isolation, not total compute cost.
- **Provider routing increases config complexity.** The UI must provide simple
  presets before exposing per-task routing.
- **Plaintext API keys** in `~/.nullifier/config.toml` are acceptable for a
  single-user desktop v1, but Settings should disclose this clearly.
- **No code signing** in v1 may trigger Windows SmartScreen and Linux trust
  prompts.

