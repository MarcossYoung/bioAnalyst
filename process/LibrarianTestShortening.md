 Plan: Background Librarian + Shorter Timeout

 Two changes are wanted:
 1. Shorten the Librarian's per-claim search timeout from 45 s to 30 s,
 and make it configurable (no more code edits to tune it).
 2. Run the Librarian in a background thread concurrent with the Analyst
 path, joining just before the Skeptic (decision: wait for the Librarian
 at the join — it is already bounded by its per-claim budget, so evidence is
 always complete; no partial-result handling needed).

 Outcome: same results, lower wall-clock, and a tunable Librarian timeout.

 Change 1 — Configurable, shorter Librarian timeout

 backend/nullifier/config/default_config.toml — add to the existing
 [literature] section:
 [literature]
 max_papers_per_claim = 12
 max_per_source = 3
 parallel_search = true
 per_claim_search_budget_seconds = 30   # NEW: wall-clock cap per atomic claim
 (The user's ~/.nullifier/config.toml auto-merges new keys via
 loader._deep_merge, so no user action is needed.)

 backend/nullifier/agents/librarian.py
 - Import the loader: from ..config.loader import load_config (other agents
 already do this, e.g. analyst.py:10).
 - At the top of retrieve_evidence() (currently librarian.py:80), read the
 budget once:
 budget = float(
     load_config().get("literature", {}).get(
         "per_claim_search_budget_seconds", _PER_CLAIM_SEARCH_BUDGET
     )
 )
 - Replace the hardcoded constant usage at librarian.py:143
 (if time.monotonic() - t0 > _PER_CLAIM_SEARCH_BUDGET:) with ... > budget:.
 - Update the module fallback constant _PER_CLAIM_SEARCH_BUDGET = 45.0
 (librarian.py:19) to 30.0 so the in-code default matches the new config
 default.

 (Scope note: _FAN_OUT_TIMEOUT in literature.py:33 and the per-source
 TIMEOUT = (4, 8) HTTP timeouts stay hardcoded — the per-claim budget is the
 headline "Librarian timeout" the user asked about. They can be exposed the same
 way later if desired.)

 Change 2 — Run the Librarian in the background

 Edit backend/nullifier/pipeline.py only. The current Librarian block
 (pipeline.py:71-87) and Analyst block (~89-200) are reordered into
 submit → run-analyst → join.

 Pattern:
 1. Add from concurrent.futures import ThreadPoolExecutor at the top.
 2. After the Formalizer block produces formalized (and after the
 _cancelled() check), submit the Librarian instead of calling it inline:
 lib_events: list[ev.Event] = []      # background thread only appends; main
                                      # thread reads it only AFTER join → safe
 lib_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="librarian")
 lib_future = lib_executor.submit(
     retrieve_evidence,
     formalized,
     max_papers_per_claim=max_papers,
     on_event=lib_events.append,
 )
 yield ev.stage_started("librarian", f"Retrieving evidence ({n_claims} claim(s))")
 2. Do not yield the collected lib_events here.
 3. Run the entire existing Analyst block unchanged (it yields its own events
 live, in order, exactly as today).
 4. Before the Skeptic block, join and flush:
 evidence = lib_future.result()       # waits for Librarian to finish
 for e in lib_events:
     yield e
 yield ev.token_update(TRACKER)
 yield ev.stage_completed("librarian")
 5. Wrap cleanup in finally: (so a run_failed/abort still tears the thread
 down): if lib_executor is not None: lib_executor.shutdown(wait=True).
 Initialize lib_executor = None / lib_future = None before the try.

 Cancellation: retrieve_evidence has no cancel hook (it didn't before
 either), so an abort mid-run still lets the Librarian thread finish before
 shutdown(wait=True) returns. This matches today's blocking behavior — no
 regression. The existing _cancelled() checks around the Analyst/Skeptic
 stages are preserved.

 Change 3 — Token tracker lock (small hardening)

 backend/nullifier/tools/llm_client.py — TokenTracker.add_claude /
 add_local (:70, :75) use non-atomic +=. This is already called
 concurrently today by llm_call_json_batch's worker pool; the background
 Librarian just makes it more frequent. Add a threading.Lock shared by both
 methods so token totals can't lose updates:
 _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
 # in add_claude / add_local: `with self._lock: ...`
 (Low-risk; token counts are display-only, but this removes a real race.)

 Behavior / event-ordering notes

 - Stream order changes: events now arrive as Formalizer → Analyst path →
 (burst of Librarian events) → Skeptic, instead of Formalizer → Librarian →
 Analyst → Skeptic. Functionally identical; only the arrival order of the
 Librarian's events shifts to the join point.
 - Frontend is unaffected: EventTimeline.tsx groups events by
 STAGE_KEY and renders in fixed STAGE_ORDER (librarian before analyst),
 so the UI still shows the Librarian section in its normal place regardless of
 arrival order. RunPage reconciles final state from the run_completed DB
 snapshot.
 - CLI: the Rich renderer (cli.py) prints in arrival order, so Librarian
 paper/synthesis lines now appear after the Analyst progress. Cosmetic only;
 render_report still uses fully-collected state at run_completed.
 - Analyst-skipped path (no starter_entities): the Librarian future is
 submitted then joined almost immediately — equivalent to today, no regression.
 - Shared state: formalized is read-only in both paths (neither mutates
 it); config is read-only; TRACKER is guarded by Change 3. No other shared
 mutable state. LM Studio tasks from both paths (librarian_per_paper,
 gene_set_classifier, robustness_reading all route to local) may now
 contend on the local endpoint — correctness is unaffected, throughput is
 bounded by LM Studio's own queueing.

 Files to modify

 - backend/nullifier/config/default_config.toml — new [literature] key
 - backend/nullifier/agents/librarian.py — config-driven budget
 - backend/nullifier/pipeline.py — background submit + join (main change)
 - backend/nullifier/tools/llm_client.py — TokenTracker lock

 Verification

 1. Unit/regression tests (run from repo root, PYTHONPATH=backend):
 $env:PYTHONPATH="backend"; python -m pytest backend/tests -q
      - backend/nullifier/tools/llm_client.py — TokenTracker lock                                                  
                                                                                                                   
      Verification                                                                                                 
                                                                                                                   
      1. Unit/regression tests (run from repo root, PYTHONPATH=backend):                                           
      $env:PYTHONPATH="backend"; python -m pytest backend/tests -q                                                 
      1. Expect the existing 86 tests to stay green.                                                               
      2. New test in backend/tests (e.g. test_pipeline_concurrency.py):                                            
        - Monkeypatch retrieve_evidence to sleep ~0.3 s and record a start                                         
      timestamp, and monkeypatch a representative Analyst-path call similarly;                                     
      assert via timestamps that the Librarian started before the Analyst                                          
      path finished (proves overlap), and that run_pipeline still yields the                                       
      Librarian's events and a verdict_ready / run_completed with non-empty                                        
      evidence.                                                                                                    
        - A small unit test asserting retrieve_evidence honors                                                     
      per_claim_search_budget_seconds from a patched load_config.                                                  
      3. End-to-end CLI run (network-dependent):                                                                   
      $env:PYTHONPATH="backend"; python -m nullifier.cli run --input examples/synapse_bbb.txt --no-confirm --debug 
      3. Confirm: a verdict renders, Librarian evidence is present, and wall-clock is                              
      noticeably lower than a sequential run (Librarian overlaps the Analyst path).                                
      4. Server smoke (optional): python -m nullifier.cli serve, run the same                                      
      hypothesis from the web UI, confirm the Librarian/Evidence section populates                                 
      and the verdict renders (frontend stage-grouping intact).   