import math

from nullifier import pipeline
from nullifier.agents.compute import run_compute
from nullifier.agents.contracts import validate_interpretation, validate_verdict


CORE_SCORES = {
    "statistical_robustness": 5,
    "literature_consensus": 5,
    "mechanistic_plausibility": 5,
    "counter_explanation_risk": 5,
    "novelty_adjusted_confidence": 5,
    "genomic_evidence_alignment": None,
    "overall_falsifiability_score": 5,
}


def _valid_interpretation() -> dict:
    return {
        "patterns_observed": [],
        "outlier_genes": [],
        "regulatory_overlap": {},
        "reproducibility_check": [],
        "limitations": [],
        "overall_genomic_assessment": "inconclusive",
        "assessment_justification": "No directional evidence was available.",
    }


def _patch_pipeline_basics(monkeypatch, *, starter_entities: list[str]) -> None:
    stage1 = {
        "core_hypothesis": "GENE1 affects outcome",
        "domain": "biology",
        "key_entities": [],
        "starter_entities": starter_entities,
        "cited_literature": [],
        "completed_analysis": [],
    }
    stage2 = {
        "atomic_claims": [{
            "id": "claim_1",
            "statement": "GENE1 affects outcome",
            "null_hypothesis": "GENE1 does not affect outcome",
        }],
        "key_search_terms": ["GENE1 outcome"],
    }
    evidence = {"claim_evidence": {"claim_1": {"retrieved_papers": []}}}

    monkeypatch.setattr(pipeline, "formalize_stage1", lambda raw: stage1)
    monkeypatch.setattr(pipeline, "formalize_stage2", lambda value: stage2)
    monkeypatch.setattr(
        pipeline,
        "retrieve_evidence",
        lambda *args, **kwargs: evidence,
    )


def test_contract_validators_accept_valid_payloads_and_genomic_na():
    assert validate_interpretation(_valid_interpretation()) == []
    assert validate_verdict({"verdict": "MODERATE", "scores": CORE_SCORES}) == []


def test_contract_validators_report_shape_and_score_errors():
    interpretation_errors = validate_interpretation({"limitations": "none"})
    assert "patterns_observed is required" in interpretation_errors
    assert "limitations must be a list" in interpretation_errors
    assert "overall_genomic_assessment must be a non-empty string" in interpretation_errors

    scores = {**CORE_SCORES, "statistical_robustness": True, "literature_consensus": math.nan}
    del scores["mechanistic_plausibility"]
    verdict_errors = validate_verdict({"verdict": "", "scores": scores})
    assert "verdict must be a non-empty string" in verdict_errors
    assert "scores.statistical_robustness must be numeric" in verdict_errors
    assert "scores.literature_consensus must be between 1 and 10" in verdict_errors
    assert "scores.mechanistic_plausibility is required" in verdict_errors


def test_analyst_failure_degrades_to_literature_only_verdict(monkeypatch):
    _patch_pipeline_basics(monkeypatch, starter_entities=["GENE1"])
    monkeypatch.setattr(
        pipeline.gene_sets,
        "expand",
        lambda *args, **kwargs: {
            "starter": ["GENE1"],
            "expanded": {},
            "controls": {},
            "starter_count": 1,
        },
    )
    monkeypatch.setattr(pipeline.gene_sets, "all_genes", lambda expansion: ["GENE1"])
    monkeypatch.setattr(
        pipeline,
        "run_analyst",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Ensembl unavailable")),
    )

    observed_analyst_results = []

    def fake_stress_test(formalized, evidence, analyst_result=None, **kwargs):
        observed_analyst_results.append(analyst_result)
        return {"verdict": "NOVEL-UNTESTED", "scores": dict(CORE_SCORES)}

    monkeypatch.setattr(pipeline, "stress_test", fake_stress_test)
    events = list(pipeline.run_pipeline("raw input"))
    event_types = [event.type for event in events]

    assert event_types.count("analyst_failed") == 1
    assert "stage_completed" in event_types
    assert any(
        event.type == "stage_completed" and event.payload["stage"] == "librarian"
        for event in events
    )
    assert "verdict_ready" in event_types
    assert "run_completed" in event_types
    assert "run_failed" not in event_types
    assert observed_analyst_results == [None]
    assert next(event for event in events if event.type == "run_completed").payload["analyst"] is None


def test_malformed_verdict_warns_but_run_completes(monkeypatch):
    _patch_pipeline_basics(monkeypatch, starter_entities=[])
    monkeypatch.setattr(pipeline, "stress_test", lambda *args, **kwargs: ["malformed"])

    events = list(pipeline.run_pipeline("raw input"))
    violations = [event for event in events if event.type == "contract_violation"]

    assert len(violations) == 1
    assert violations[0].payload["agent"] == "skeptic"
    assert violations[0].payload["violations"] == ["expected object, got list"]
    assert any(event.type == "verdict_ready" for event in events)
    assert any(event.type == "run_completed" for event in events)
    assert not any(event.type == "run_failed" for event in events)


def test_skip_librarian_does_not_call_retrieval_and_still_completes(monkeypatch):
    _patch_pipeline_basics(monkeypatch, starter_entities=[])

    def fail_retrieve(*args, **kwargs):
        raise AssertionError("retrieve_evidence should not run")

    observed_evidence = []

    def fake_stress_test(formalized, evidence, analyst_result=None, **kwargs):
        observed_evidence.append(evidence)
        return {"verdict": "NOVEL-UNTESTED", "scores": dict(CORE_SCORES)}

    monkeypatch.setattr(pipeline, "retrieve_evidence", fail_retrieve)
    monkeypatch.setattr(pipeline, "stress_test", fake_stress_test)

    events = list(pipeline.run_pipeline("raw input", skip_librarian=True))

    assert any(event.type == "librarian_skipped" for event in events)
    assert any(
        event.type == "stage_completed" and event.payload["stage"] == "librarian"
        for event in events
    )
    assert any(event.type == "verdict_ready" for event in events)
    assert any(event.type == "run_completed" for event in events)
    assert not any(event.type == "run_failed" for event in events)
    assert observed_evidence
    assert observed_evidence[0]["librarian_skipped"] is True
    claim = observed_evidence[0]["claim_evidence"]["claim_1"]
    assert claim["retrieved_papers"] == []
    assert claim["evidence_strength"] == "not_assessed"


def test_raw_verdict_violations_survive_agent_normalization(monkeypatch):
    _patch_pipeline_basics(monkeypatch, starter_entities=[])

    def fake_stress_test(*args, on_contract_violation=None, **kwargs):
        on_contract_violation(["scores must be an object"])
        return {"verdict": "WEAK", "scores": dict(CORE_SCORES)}

    monkeypatch.setattr(pipeline, "stress_test", fake_stress_test)

    events = list(pipeline.run_pipeline("raw input"))
    warnings = [event for event in events if event.type == "contract_violation"]

    assert len(warnings) == 1
    assert warnings[0].payload == {
        "agent": "skeptic",
        "violations": ["scores must be an object"],
    }
    assert any(event.type == "run_completed" for event in events)


def test_malformed_interpretation_emits_contract_warning(monkeypatch):
    monkeypatch.setattr(
        pipeline.gene_sets,
        "expand",
        lambda *args, **kwargs: {
            "starter": ["GENE1"],
            "expanded": {},
            "controls": {},
            "starter_count": 1,
        },
    )
    monkeypatch.setattr(pipeline.gene_sets, "all_genes", lambda expansion: ["GENE1"])
    monkeypatch.setattr(
        pipeline,
        "run_analyst",
        lambda **kwargs: {
            "data": {"provenance": {}},
            "data_summary": {},
            "gene_data": {},
            "paml_data": {},
            "paml_site_data": {},
            "paml_branch_site_data": {},
            "gnomad_data": {},
            "phylo_data": {},
            "rdnds_data": {},
            "reproducibility": {},
            "set_a": [],
            "set_b": [],
            "set_a_stats": {},
            "set_b_stats": {},
            "cross_set": {},
        },
    )
    monkeypatch.setattr(
        pipeline,
        "run_methodologist",
        lambda *args, **kwargs: {"tests_requested": [], "primary_tests": [], "claim_constructs": []},
    )
    monkeypatch.setattr(
        pipeline,
        "run_compute",
        lambda *args, **kwargs: {"compute_results": {"tests": []}, "robustness": {}},
    )
    monkeypatch.setattr(
        pipeline,
        "run_interpreter",
        lambda *args, **kwargs: {
            "overall_genomic_assessment": "inconclusive",
            "limitations": [],
        },
    )

    events = list(pipeline._run_analyst_stage(
        {"core_hypothesis": "GENE1 affects outcome"},
        "biology",
        ["GENE1"],
        [],
    ))
    warning = next(event for event in events if event.type == "contract_violation")

    assert warning.payload["agent"] == "interpreter"
    assert "patterns_observed is required" in warning.payload["violations"]
    assert any(event.type == "analyst_ready" for event in events)


def test_empty_plan_is_explicitly_untested():
    events = []
    result = run_compute(
        plan={
            "tests_requested": [],
            "primary_tests": [],
            "claim_constructs": ["set_difference"],
        },
        data={},
        starter_entities=[],
        rebuild_data=lambda excluded: {},
        on_event=events.append,
    )

    assert result["compute_results"]["untested"] is True
    assert result["compute_results"]["claim_constructs"] == ["set_difference"]
    assert [event.type for event in events].count("no_applicable_tests") == 1
    assert not any(event.type == "compute_test_complete" for event in events)
