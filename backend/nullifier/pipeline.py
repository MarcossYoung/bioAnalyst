import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Generator

from . import events as ev
from .agents.formalizer import formalize_stage1, formalize_stage2
from .agents.librarian import retrieve_evidence
from .agents.analyst import run_analyst
from .agents.compute import run_compute
from .agents.methodologist import run_methodologist
from .agents.interpreter import run_interpreter
from .agents.skeptic import stress_test
from .agents.contracts import validate_interpretation, validate_verdict
from .tools import gene_sets
from .tools.genomic_data import build_data
from .tools.llm_client import TRACKER


def _run_analyst_stage(
    formalized: dict,
    domain: str,
    starter_entities: list,
    completed_analysis: list,
) -> Generator[ev.Event, None, dict | None]:
    """Run the genomic panel as an isolated, streaming pipeline stage."""
    yield ev.stage_started("analyst", "Expanding gene sets")
    expansion = gene_sets.expand(
        starter_entities,
        formalized.get("core_hypothesis", ""),
        domain,
    )
    if expansion.get("skipped"):
        yield ev.analyst_skipped(expansion.get("reason", "expansion skipped"))
        return None

    yield ev.gene_sets_expanded(expansion)
    analyst_events: queue.Queue[ev.Event | None] = queue.Queue()
    analyst_box: dict = {}

    def _run_analyst_worker() -> None:
        try:
            analyst_box["data"] = run_analyst(
                all_targets=gene_sets.all_genes(expansion),
                expansion=expansion,
                formalized=formalized,
                starter_entities=starter_entities,
                completed_analysis=completed_analysis,
                on_event=analyst_events.put,
            )
        except BaseException as exc:
            analyst_box["error"] = exc
        finally:
            analyst_events.put(None)

    analyst_thread = threading.Thread(
        target=_run_analyst_worker,
        daemon=True,
        name="analyst",
    )
    analyst_thread.start()
    while True:
        analyst_event = analyst_events.get()
        if analyst_event is None:
            break
        yield analyst_event
    analyst_thread.join()
    if analyst_box.get("error"):
        raise analyst_box["error"]
    analyst_data = analyst_box["data"]

    analyst_data["data"]["paml"] = analyst_data.get("paml_data", {})
    analyst_data["data"]["paml_site"] = analyst_data.get("paml_site_data", {})
    analyst_data["data"]["paml_branch_site"] = analyst_data.get("paml_branch_site_data", {})

    plan = run_methodologist(
        formalized,
        expansion,
        analyst_data["data_summary"],
        completed_analysis=completed_analysis,
    )
    yield ev.methodologist_plan_complete(plan)
    yield ev.token_update(TRACKER)

    compute_events: list[ev.Event] = []
    compute_result = run_compute(
        plan=plan,
        data=analyst_data["data"],
        starter_entities=starter_entities,
        rebuild_data=lambda excl: {
            **build_data(
                analyst_data["gene_data"],
                expansion,
                exclude=excl,
                gnomad_data=analyst_data["gnomad_data"],
                phylo_data=analyst_data["phylo_data"],
                paml_data=analyst_data.get("paml_data"),
                rdnds_data=analyst_data.get("rdnds_data"),
                diagnostics=analyst_data.get("diagnostics"),
                min_low_risk_genes=(
                    (analyst_data.get("risk_filter") or {}).get("min_low_risk_genes") or 2
                ),
            ),
            "paml_site": {
                k: v
                for k, v in (analyst_data.get("paml_site_data") or {}).items()
                if k not in excl
            },
            "paml_branch_site": {
                k: v
                for k, v in (analyst_data.get("paml_branch_site_data") or {}).items()
                if k not in excl
            },
        },
        on_event=compute_events.append,
    )
    yield from compute_events

    yield ev.interpreter_start()
    raw_interpretation_violations: list[str] = []
    interpretation = run_interpreter(
        formalized,
        expansion,
        compute_result["compute_results"],
        analyst_data["gene_data"],
        robustness=compute_result["robustness"],
        reproducibility=analyst_data["reproducibility"],
        on_contract_violation=raw_interpretation_violations.extend,
    )
    dnds_saturation = analyst_data.get("dnds_saturation") or {}
    if dnds_saturation.get("flag"):
        reason = dnds_saturation.get("reason")
        if any(
            bool((s or {}).get("risk_degraded"))
            for s in ((dnds_saturation.get("sets") or {}).values())
        ):
            reason = "risk filter left too few scorable genes"
        interpretation = {
            **interpretation,
            "overall_genomic_assessment": "untestable",
            "assessment_justification": reason,
            "limitations": list(interpretation.get("limitations") or []) + [reason],
            "dnds_saturation": dnds_saturation,
        }

    violations = list(dict.fromkeys([
        *raw_interpretation_violations,
        *validate_interpretation(interpretation),
    ]))
    if violations:
        yield ev.contract_violation("interpreter", violations)
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
        "dnds_saturation": analyst_data.get("dnds_saturation"),
        "cross_set": analyst_data["cross_set"],
        "phylo_data": analyst_data["phylo_data"],
        "diagnostics": analyst_data.get("diagnostics"),
        "risk_filter": analyst_data.get("risk_filter"),
        "data_provenance": analyst_data["data"].get("provenance"),
    }
    yield ev.token_update(TRACKER)
    yield ev.stage_completed("analyst")
    return analyst_result


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

    lib_executor: ThreadPoolExecutor | None = None
    lib_future: Future[dict] | None = None
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
        lib_events: list[ev.Event] = []
        lib_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="librarian")
        lib_future = lib_executor.submit(
            retrieve_evidence,
            formalized,
            max_papers_per_claim=max_papers,
            on_event=lib_events.append,
        )
        yield ev.stage_started("librarian", f"Retrieving evidence ({n_claims} claim(s))")

        # ── v6 Analyst stage: expand → fetch → methodologist → compute → interpret ──
        if _cancelled():
            yield ev.run_aborted()
            return
        analyst_result: dict | None = None
        starter_entities = formalized.get("starter_entities", []) or []

        if not starter_entities:
            yield ev.analyst_skipped("No starter entities provided")
        else:
            try:
                analyst_result = yield from _run_analyst_stage(
                    formalized,
                    domain,
                    starter_entities,
                    completed_analysis,
                )
            except Exception as exc:
                yield ev.analyst_failed(str(exc))
                analyst_result = None

        evidence = lib_future.result()
        for e in lib_events:
            yield e
        yield ev.token_update(TRACKER)
        yield ev.stage_completed("librarian")

        # ── Skeptic ──────────────────────────────────────────────────────────
        if _cancelled():
            yield ev.run_aborted()
            return
        yield ev.stage_started("skeptic", "Stress-testing hypothesis")
        if completed_analysis:
            yield ev.skeptic_critique_mode_active(len(completed_analysis))

        raw_verdict_violations: list[str] = []
        verdict = stress_test(
            formalized,
            evidence,
            analyst_result=analyst_result,
            on_contract_violation=raw_verdict_violations.extend,
        )
        violations = list(dict.fromkeys([
            *raw_verdict_violations,
            *validate_verdict(verdict),
        ]))
        if violations:
            yield ev.contract_violation("skeptic", violations)
        verdict_payload = verdict if isinstance(verdict, dict) else {}
        yield ev.verdict_ready(
            verdict_payload.get("scores", {}),
            verdict_payload.get("verdict", ""),
        )
        yield ev.token_update(TRACKER)
        yield ev.stage_completed("skeptic")

        yield ev.run_completed(formalized, evidence, verdict, analyst_result)

    except Exception as e:
        yield ev.run_failed(str(e))
    finally:
        if lib_executor is not None:
            lib_executor.shutdown(wait=True)
