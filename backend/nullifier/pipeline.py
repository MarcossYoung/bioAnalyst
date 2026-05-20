from typing import Callable, Generator

from . import events as ev
from .agents.formalizer import formalize_stage1, formalize_stage2
from .agents.librarian import retrieve_evidence
from .agents.analyst import run_analyst
from .agents.compute import run_compute
from .agents.methodologist import run_methodologist
from .agents.interpreter import run_interpreter
from .agents.skeptic import stress_test
from .tools import gene_sets
from .tools.genomic_data import build_data
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

        # ── v6 Analyst stage: expand → fetch → methodologist → compute → interpret ──
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

                # 2. Fetch all genomic data
                analyst_events: list[ev.Event] = []
                analyst_data = run_analyst(
                    all_targets=gene_sets.all_genes(expansion),
                    expansion=expansion,
                    formalized=formalized,
                    starter_entities=starter_entities,
                    completed_analysis=completed_analysis,
                    on_event=analyst_events.append,
                )
                for e in analyst_events:
                    yield e

                analyst_data["data"]["paml"] = analyst_data.get("paml_data", {})

                # 3. Methodologist
                plan = run_methodologist(
                    formalized, expansion, analyst_data["data_summary"],
                    completed_analysis=completed_analysis,
                )
                yield ev.methodologist_plan_complete(plan)
                yield ev.token_update(TRACKER)

                # 4–5. Compute + Robustness
                compute_events: list[ev.Event] = []
                compute_result = run_compute(
                    plan=plan,
                    data=analyst_data["data"],
                    starter_entities=starter_entities,
                    rebuild_data=lambda excl: build_data(
                        analyst_data["gene_data"], expansion, exclude=excl,
                        gnomad_data=analyst_data["gnomad_data"],
                        phylo_data=analyst_data["phylo_data"],
                    ),
                    on_event=compute_events.append,
                )
                for e in compute_events:
                    yield e

                # 6. Interpreter
                yield ev.interpreter_start()
                interpretation = run_interpreter(
                    formalized, expansion,
                    compute_result["compute_results"],
                    analyst_data["gene_data"],
                    robustness=compute_result["robustness"],
                    reproducibility=analyst_data["reproducibility"],
                )
                assessment = interpretation.get("overall_genomic_assessment", "inconclusive")
                yield ev.interpreter_complete(assessment)
                yield ev.analyst_ready(assessment)

                analyst_result = {
                    "skipped": False,
                    "expansion": expansion,
                    "gene_data": analyst_data["gene_data"],
                    "plan": plan,
                    "compute_results": compute_result["compute_results"],
                    "robustness": compute_result["robustness"],
                    "reproducibility": analyst_data["reproducibility"],
                    "interpretation": interpretation,
                    "set_a": analyst_data["set_a"],
                    "set_b": analyst_data["set_b"],
                    "set_a_stats": analyst_data["set_a_stats"],
                    "set_b_stats": analyst_data["set_b_stats"],
                    "cross_set": analyst_data["cross_set"],
                    "phylo_data": analyst_data["phylo_data"],
                    "data_provenance": analyst_data["data"].get("provenance"),
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
