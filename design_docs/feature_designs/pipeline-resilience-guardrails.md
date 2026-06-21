# Pipeline resilience and output-contract guardrails

**Status:** Implementation ready

**Date:** 2026-06-19

**Basis:** Control-flow audit of `pipeline.py`, `agents/compute.py`,
`agents/methodologist.py`, and `tools/compute.py`. Four defects found: one
resilience break that contradicts the stated degradation contract, two cases of
silent output degradation, and one missing "untested vs. tested-null"
distinction.

## Summary

The codebase already defends the LLM-to-deterministic boundary well: the
Methodologist whitelists `tests_requested` against `TEST_LIBRARY`
(`methodologist.py:86-93`), and `_run_one` wraps every test in `try/except`
returning a typed error result (`compute.py:1327-1359`) validated by
`validate_test_result` (`compute.py:208`). PAML/codeml absence degrades to
`available=False`. Those guardrails should not change.

This document covers four gaps that remain:

1. **Analyst failure aborts the entire run**, discarding already-retrieved
   literature evidence — contradicts the graceful-degradation principle in
   `CLAUDE.md`.
2. **Only the Methodologist validates LLM output shape**; the Interpreter,
   Skeptic, and Formalizer outputs are trusted, so a malformed verdict
   silently drops scores instead of being flagged.
3. **An empty post-filter plan is indistinguishable from a null result** —
   "no applicable test ran" and "tests ran and found nothing" both surface as
   `inconclusive`.
4. **Scientific guardrails for the PAML family** (BH across genes, low-power
   floor) — already specified in
   [`paml-positive-selection-models.md`](paml-positive-selection-models.md);
   restated here only as cross-references so this document is the single
   guardrail index.

All changes preserve the existing contract: deterministic code never raises
into the pipeline; degradation is always surfaced as a typed event, never a
silent default.

## Scope

### Included

- Isolate the Analyst stage so its failure costs the genomic panel only, not
  the literature verdict.
- Runtime contract validators for the Interpreter and Skeptic outputs that emit
  a warning event on violation instead of silently defaulting.
- A distinct event when the filtered plan is empty.
- New event factories in `events.py` for each of the above.

### Deferred / cross-referenced

- BH correction across genes and the dN/dS low-power floor — owned by
  [`paml-positive-selection-models.md`](paml-positive-selection-models.md).
- Formalizer Stage 1/2 schema validation — lower risk (its output is consumed
  through the section-confirmation gate, which already lets a human correct it);
  out of scope for this pass.

---

## Guardrail 1 — Analyst failure must not abort the literature verdict

### Problem

`pipeline.py:118-147`: the Analyst runs in a worker thread; on any exception the
worker stores it in `analyst_box["error"]`, and the pipeline re-raises it at
line 146:

```python
if analyst_box.get("error"):
    raise analyst_box["error"]
```

That raise propagates to the outer `except Exception` (line 252) and yields
`run_failed`. The Librarian is running concurrently in `lib_future`, but
`lib_future.result()` (line 231) is never reached, so **literature evidence that
was already retrieved is thrown away and the whole run fails because a gene-tree
fetch or codeml call hiccuped.** This directly contradicts the CLAUDE.md
guarantee that "one failure doesn't abort the run."

The Skeptic already tolerates a missing genomic panel: `analyst_result` is
initialized to `None` (line 95), the no-starter-genes branch leaves it `None`,
and `stress_test(..., analyst_result=None)` is the documented default. So the
downstream path already supports the degraded case — the only thing missing is
not crashing.

### Change

Wrap the Analyst block (the `else` body under `if not starter_entities`,
roughly `pipeline.py:100-229`) in its own `try/except`. On failure:

- emit a new `analyst_failed(reason)` event (see events change below),
- set `analyst_result = None`,
- continue to the Librarian join and the Skeptic.

```python
try:
    # gene-set expansion → analyst worker → methodologist → compute → interpreter
    ...
    analyst_result = { ... }
    yield ev.stage_completed("analyst")
except Exception as exc:
    yield ev.analyst_failed(str(exc))
    analyst_result = None
```

Keep the existing `analyst_box["error"]` re-raise **inside** this block so the
worker exception is caught locally rather than at the pipeline boundary.

### Notes

- The worker catches `BaseException` (line 128). Keep that — it correctly
  surfaces `KeyboardInterrupt`/`SystemExit` — but the new local `except
  Exception` means only ordinary failures degrade gracefully; a genuine
  interrupt still aborts, which is correct.
- `analyst_box["data"]` (line 147) can `KeyError` only if the worker neither set
  `data` nor `error`, which the `finally: analyst_events.put(None)` plus the
  `except BaseException` make unreachable. No change needed, but the new outer
  `try` covers it regardless.
- The `finally` that shuts down `lib_executor` (line 254) is unchanged and still
  runs.

---

## Guardrail 2 — Validate Interpreter and Skeptic output shape

### Problem

The Methodologist is the only agent whose LLM output is validated against a
whitelist. The Interpreter and Skeptic results are consumed through `.get()`
with defaults, e.g. `pipeline.py:246`:

```python
yield ev.verdict_ready(verdict.get("scores", {}), verdict.get("verdict", ""))
```

A malformed verdict (missing `scores`, non-numeric score, missing `verdict`)
renders as "no scores / empty verdict" rather than being flagged. The failure is
invisible — the run reports success with a hollow result.

### Change

Add two lightweight validators in `tools/compute.py` (or a new
`agents/contracts.py` if preferred — keep them deterministic and LLM-free),
mirroring `validate_test_result`'s style (return-on-clean, collect problems):

- `validate_interpretation(interp: dict) -> list[str]` — returns a list of
  human-readable contract violations (missing `overall_genomic_assessment`,
  non-list `limitations`, etc.); empty list means clean.
- `validate_verdict(verdict: dict) -> list[str]` — asserts `verdict` is a
  non-empty string and `scores` contains the expected 7 dimensions, each
  numeric and in range. Returns violations.

These **do not raise** and **do not mutate** the payload — the run still
completes with whatever the LLM produced (preserving the no-`raise`-after-result
ethos). Instead, in `pipeline.py`, after each agent returns, emit a warning
event when the violation list is non-empty:

```python
violations = validate_verdict(verdict)
if violations:
    yield ev.contract_violation("skeptic", violations)
```

Same pattern for the Interpreter after `run_interpreter` (line 181). This
converts silent degradation into a visible, logged signal that the SQLite event
store and the web UI can surface, without changing the verdict the user sees.

### Why warn, not block

The seven-dimension scores feed a human-read verdict, not a control-flow
decision. Blocking on a malformed verdict would trade a silent-but-complete run
for a hard failure — strictly worse for a stress-tester whose job is to report
what it found. A flagged warning is the right severity.

---

## Guardrail 3 — Distinguish "no applicable test" from "tested, null"

### Problem

After the Methodologist whitelist filter (`methodologist.py:86-93`),
`tests_requested` can be `[]` — every test the LLM proposed was unknown, or no
construct mapped to a library test. The run then proceeds to an `inconclusive`
genomic assessment that is indistinguishable from "tests ran and found no
signal." For a falsification tool this conflation is a credibility risk: absence
of a test is being reported as absence of evidence.

### Change

In `agents/compute.py:run_compute` (or at the methodologist call site in
`pipeline.py:152-156`), when the filtered `tests_requested` is empty, emit a
distinct `ev.no_applicable_tests(constructs)` event carrying the claim
constructs that found no library match. The Interpreter and Skeptic prompts
should treat this state as **untested**, not as a weak-null, in their framing.

This is purely additive — no test is skipped that would otherwise run; it only
labels the empty-plan case so downstream language is honest.

---

## Guardrail 4 — PAML scientific guardrails (cross-reference)

Owned by [`paml-positive-selection-models.md`](paml-positive-selection-models.md);
listed here so this document is the complete guardrail index:

- **BH across genes** for the branch, site, and branch-site model families,
  replacing the current min-raw-p selection in `_paml_branch_model`
  (`compute.py`). Significance requires adjusted p < 0.05.
- **Low-power / too-closely-related floor** (Jeffares Note 1: >95% identity or
  synonymous tree distance < 0.5) — deferred in that doc, flagged analogously to
  the existing `dnds_saturation` upper-bound gate.

No duplication of implementation here; if those land via the PAML doc, this
section is satisfied.

---

## events.py additions

- `analyst_failed(reason: str)` — Analyst stage degraded; genomic panel absent,
  run continues.
- `contract_violation(agent: str, violations: list[str])` — an agent's output
  failed its runtime contract; result retained, violation logged.
- `no_applicable_tests(constructs: list[str])` — filtered plan is empty; the
  genomic question is untested, not null.

Each follows the existing `Event` dataclass + factory pattern and is persisted
to the `run_events` table like every other event, so reconnecting WebSocket
clients replay them.

## Verification

- **Unit (`backend/tests/test_pipeline_resilience.py`):**
  - Analyst worker raising → run still yields `librarian` completion,
    `skeptic` verdict, and `run_completed`; exactly one `analyst_failed` event;
    no `run_failed`.
  - `validate_verdict` / `validate_interpretation` return the right violation
    lists on hand-built malformed payloads and `[]` on valid ones.
  - Empty filtered plan → `no_applicable_tests` emitted; no `compute_test_complete`.
- **Contract:** a malformed verdict still produces a complete `run_completed`
  event (no `raise`), plus a `contract_violation` event.
- **Regression:** existing 86 compute tests pass unchanged (these changes are at
  the pipeline/agent layer, not the test library).
- **End-to-end CLI:** force an Analyst exception (e.g. monkeypatch
  `run_analyst` to raise) on a hypothesis with starter genes; confirm the CLI
  still renders a literature-only verdict and exits 0.
