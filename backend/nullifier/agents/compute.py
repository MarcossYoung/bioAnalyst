"""Orchestrator for the deterministic Compute stage (Layer 3 → Layer 4 bridge).

Calls tools/compute.py functions in order and emits events via on_event callback.
No LLM calls — purely orchestration of deterministic runtime.
"""
from typing import Callable

from ..tools.compute import run_analysis_plan, leave_one_out
from .. import events as ev


def run_compute(
    plan: dict,
    data: dict,
    starter_entities: list,
    rebuild_data: Callable,
    on_event=None,
) -> dict:
    """Run all compute steps and return {compute_results, robustness}.

    rebuild_data: callable that accepts an exclude set and returns a data dict
        (used by leave_one_out for each leave-one-out iteration).
    on_event: called with each Event as it is emitted.
    """
    def _emit(e):
        if on_event:
            on_event(e)

    requested = plan.get("tests_requested") or []
    _emit(ev.compute_start(len(requested)))
    compute_results = run_analysis_plan(plan, data)
    for t in compute_results.get("tests") or []:
        name = t.get("test") or t.get("requested", "?")
        sig = t.get("significant_adjusted")
        if sig is None:
            sig = t.get("significant")
        _emit(ev.compute_test_complete(name, t.get("p_value"), sig))
    _emit(ev.compute_all_complete(
        len(compute_results.get("tests") or []),
        compute_results.get("corrections_applied") or [],
    ))

    primary = plan.get("primary_tests") or []
    _emit(ev.compute_robustness_start(len(starter_entities)))
    risk_filter = ((data or {}).get("rate_vectors") or {}).get("risk_filter") or {}
    robustness = leave_one_out(
        starter_entities,
        primary,
        rebuild_data=rebuild_data,
        flagged_genes=risk_filter.get("flagged_genes") or [],
    )
    _emit(ev.compute_robustness_complete(
        robustness.get("stability", "unknown"),
        robustness.get("agreement_fraction", 0.0),
        robustness.get("most_influential_genes") or [],
    ))

    return {"compute_results": compute_results, "robustness": robustness}
