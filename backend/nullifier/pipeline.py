from typing import Callable, Generator

from . import events as ev
from .agents.formalizer import formalize_stage1, formalize_stage2
from .agents.librarian import retrieve_evidence
from .agents.analyst import (
    _fetch_all_gene_data,
    _split_into_sets,
    _set_statistics,
    _cross_set_analysis,
)
from .agents.methodologist import run_methodologist
from .agents.interpreter import run_interpreter
from .agents.skeptic import stress_test
from .tools import gene_sets
from .tools.compute import (
    run_analysis_plan,
    leave_one_out,
    verify_reported_stats,
    _data_summary,
)
from .tools.genomic_data import build_data, retrievable_summary
from .tools.llm_client import TRACKER


def run_pipeline(
    raw_text: str,
    confirm_callback: Callable[[dict], dict | None] | None = None,
    max_papers: int = 12,
    cancel_check: Callable[[], bool] | None = None,
) -> Generator[ev.Event, None, None]:
    """Generator that yields Event objects as the pipeline progresses.

    confirm_callback: called with stage1 dict after hypothesis extraction.
        Return the (possibly edited) stage1 dict to proceed, or None to abort.
        If None, the confirmation gate is skipped entirely.

    cancel_check: zero-arg callable that returns True when the run has been cancelled.
        Checked before each stage; yields run_aborted and exits early when True.
    """
    def _cancelled() -> bool:
        return cancel_check is not None and cancel_check()

    try:
        yield ev.run_started()

        # ── Formalizer ──────────────────────────────────────────────────────
        if _cancelled():
            yield ev.run_aborted()
            return
        yield ev.stage_started("formalize", "Extracting hypothesis structure")

        stage1 = formalize_stage1(raw_text)
        domain = stage1.get("domain", "unknown").lower()
        yield ev.hypothesis_extracted(stage1)
        yield ev.token_update(TRACKER)

        completed_analysis = stage1.get("completed_analysis") or []
        if completed_analysis:
            yield ev.formalizer_detected_completed_analysis(completed_analysis)

        if confirm_callback is not None:
            yield ev.confirmation_required(stage1)
            section_keys = [key for _, _, key, _, _ in ev.SECTION_SPECS]
            before = {k: stage1.get(k) for k in section_keys}
            updated = confirm_callback(stage1)
            if updated is None:
                yield ev.run_aborted()
                return
            stage1 = updated
            changed = [k for k in section_keys if stage1.get(k) != before.get(k)]
            yield ev.confirmation_received("edited" if changed else "proceed", changed)
            completed_analysis = stage1.get("completed_analysis") or []

        stage2 = formalize_stage2(stage1)
        formalized = {**stage1, **stage2}
        yield ev.claims_formalized(stage2)
        yield ev.token_update(TRACKER)
        yield ev.stage_completed("formalize")

        # ── Librarian ────────────────────────────────────────────────────────
        if _cancelled():
            yield ev.run_aborted()
            return
        n_claims = len(formalized.get("atomic_claims", []))
        yield ev.stage_started("librarian", f"Retrieving evidence ({n_claims} claim(s))")

        lib_events: list[ev.Event] = []
        evidence = retrieve_evidence(
            formalized,
            max_papers_per_claim=max_papers,
            on_event=lib_events.append,
        )
        for e in lib_events:
            yield e
        yield ev.token_update(TRACKER)
        yield ev.stage_completed("librarian")

        # ── v6 Analyst stage: expand → fetch → methodologist → compute → robustness → interpret ──
        if _cancelled():
            yield ev.run_aborted()
            return
        analyst_result: dict | None = None
        starter_entities = formalized.get("starter_entities", []) or []

        if not starter_entities:
            yield ev.analyst_skipped("No starter entities provided")
        else:
            yield ev.stage_started("analyst", "Expanding gene sets")

            # 1. Gene-set expansion
            expansion = gene_sets.expand(
                starter_entities,
                formalized.get("core_hypothesis", ""),
                domain,
            )
            if expansion.get("skipped"):
                yield ev.analyst_skipped(expansion.get("reason", "expansion skipped"))
            else:
                yield ev.gene_sets_expanded(expansion)

                # 2. Fetch Ensembl data for every gene we'll touch (starter + expanded + controls)
                all_targets = gene_sets.all_genes(expansion)
                gene_count = len(all_targets)
                yield ev.analyst_started(gene_count)

                analyst_events: list[ev.Event] = []
                gene_data = _fetch_all_gene_data(
                    all_targets,
                    use_cache=True,
                    on_gene=lambda g, s: analyst_events.append(ev.analyst_gene_fetched(g, s)),
                    starter_genes=set(expansion.get("starter", [])),
                    on_event=analyst_events.append,
                )
                for e in analyst_events:
                    yield e

                # 3. Build typed `data` dict and ask Methodologist for a plan
                data = build_data(gene_data, expansion)
                plan = run_methodologist(
                    formalized,
                    expansion,
                    _data_summary(data),
                    completed_analysis=completed_analysis,
                )
                yield ev.methodologist_plan_complete(plan)
                yield ev.token_update(TRACKER)

                # 4. Run the Compute layer deterministically
                requested = plan.get("tests_requested") or []
                yield ev.compute_start(len(requested))
                compute_results = run_analysis_plan(plan, data)
                for t in compute_results.get("tests") or []:
                    name = t.get("test") or t.get("requested", "?")
                    sig = t.get("significant_adjusted")
                    if sig is None:
                        sig = t.get("significant")
                    yield ev.compute_test_complete(name, t.get("p_value"), sig)
                yield ev.compute_all_complete(
                    len(compute_results.get("tests") or []),
                    compute_results.get("corrections_applied") or [],
                )

                # 5. Robustness — leave-one-out on the starter set
                primary = plan.get("primary_tests") or []
                yield ev.compute_robustness_start(len(starter_entities))
                robustness = leave_one_out(
                    starter_entities,
                    primary,
                    rebuild_data=lambda excl: build_data(gene_data, expansion, exclude=excl),
                )
                yield ev.compute_robustness_complete(
                    robustness.get("stability", "unknown"),
                    robustness.get("agreement_fraction", 0.0),
                    robustness.get("most_influential_genes") or [],
                )

                # 6. Reproducibility check (deterministic) — only if author reported analyses
                reproducibility = None
                if completed_analysis:
                    yield ev.analyst_reproducibility_check_start(len(completed_analysis))
                    reproducibility = verify_reported_stats(
                        completed_analysis,
                        retrievable_summary(gene_data),
                    )
                    yield ev.analyst_reproducibility_check_complete(
                        reproducibility.get("verifiable_count", 0),
                        reproducibility.get("total", len(completed_analysis)),
                    )

                # 7. Interpreter — Claude reads typed Compute output
                yield ev.interpreter_start()
                interpretation = run_interpreter(
                    formalized, expansion, compute_results, gene_data,
                    robustness=robustness, reproducibility=reproducibility,
                )
                assessment = interpretation.get("overall_genomic_assessment", "inconclusive")
                yield ev.interpreter_complete(assessment)
                yield ev.analyst_ready(assessment)

                # 8. Compatibility shims so the existing Skeptic prompt still sees its inputs
                set_a, set_b = _split_into_sets(formalized, starter_entities)
                set_a_stats = _set_statistics(set_a, gene_data) if set_a else None
                set_b_stats = _set_statistics(set_b, gene_data) if set_b else None
                cross_set = (_cross_set_analysis(set_a, set_b, gene_data)
                             if (set_a and set_b) else None)

                analyst_result = {
                    "skipped": False,
                    "expansion": expansion,
                    "gene_data": gene_data,
                    "plan": plan,
                    "compute_results": compute_results,
                    "robustness": robustness,
                    "reproducibility": reproducibility,
                    "interpretation": interpretation,
                    # legacy shims used by Skeptic prompt formatting
                    "set_a": set_a,
                    "set_b": set_b,
                    "set_a_stats": set_a_stats,
                    "set_b_stats": set_b_stats,
                    "cross_set": cross_set,
                }

                yield ev.token_update(TRACKER)
                yield ev.stage_completed("analyst")

        # ── Skeptic ──────────────────────────────────────────────────────────
        if _cancelled():
            yield ev.run_aborted()
            return
        yield ev.stage_started("skeptic", "Stress-testing hypothesis")
        if completed_analysis:
            yield ev.skeptic_critique_mode_active(len(completed_analysis))

        verdict = stress_test(formalized, evidence, analyst_result=analyst_result)
        yield ev.verdict_ready(verdict.get("scores", {}), verdict.get("verdict", ""))
        yield ev.token_update(TRACKER)
        yield ev.stage_completed("skeptic")

        yield ev.run_completed(formalized, evidence, verdict, analyst_result)

    except Exception as e:
        yield ev.run_failed(str(e))
