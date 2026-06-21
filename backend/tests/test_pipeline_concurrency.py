import threading
import time

from nullifier import events as ev
from nullifier import pipeline
from nullifier.agents import librarian


def test_librarian_honors_configured_per_claim_budget(monkeypatch):
    formalized = {
        "core_hypothesis": "A hypothesis",
        "domain": "biology",
        "key_entities": [],
        "starter_entities": [],
        "cited_literature": [],
        "atomic_claims": [
            {"id": "claim_1", "statement": "GENE1 changes outcome", "null_hypothesis": "GENE1 does not change outcome"},
        ],
    }
    paper = {"source": "src", "id": "1", "title": "Title 1", "abstract": "Abstract.", "year": 2024}
    monotonic_values = iter([100.0, 101.0])

    monkeypatch.setattr(
        librarian,
        "load_config",
        lambda: {"literature": {"per_claim_search_budget_seconds": 0.5}},
    )
    monkeypatch.setattr(librarian.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(librarian, "get_relevant_flags", lambda *args, **kwargs: [])
    monkeypatch.setattr(librarian, "format_flags_for_prompt", lambda flags: "")
    monkeypatch.setattr(librarian, "expand_queries", lambda claim, starter_entities: [{"query": "q1"}])
    monkeypatch.setattr(librarian, "federated_search", lambda *args, **kwargs: ([paper], {}))
    monkeypatch.setattr(librarian, "llm_call_json_batch", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        librarian,
        "llm_call_json",
        lambda *args, **kwargs: {
            "claim_id": "claim_1",
            "confounders_identified": [],
            "evidence_strength": "absent",
            "novelty_flag": "unstudied",
            "literature_gap": "unknown",
            "synthesis": "summary",
        },
    )

    evidence = librarian.retrieve_evidence(formalized)

    assert evidence["claim_evidence"]["claim_1"]["retrieved_papers"] == []


def test_librarian_degrades_when_query_expansion_fails(monkeypatch):
    formalized = {
        "core_hypothesis": "A hypothesis",
        "domain": "biology",
        "key_entities": [],
        "starter_entities": [],
        "cited_literature": [],
        "atomic_claims": [
            {"id": "claim_1", "statement": "GENE1 changes outcome", "null_hypothesis": "GENE1 does not change outcome"},
        ],
    }

    monkeypatch.setattr(librarian, "get_relevant_flags", lambda *args, **kwargs: [])
    monkeypatch.setattr(librarian, "format_flags_for_prompt", lambda flags: "")
    monkeypatch.setattr(librarian, "expand_queries", lambda *args, **kwargs: (_ for _ in ()).throw(IndexError("list index out of range")))
    monkeypatch.setattr(
        librarian,
        "llm_call_json",
        lambda *args, **kwargs: {
            "claim_id": "claim_1",
            "confounders_identified": [],
            "evidence_strength": "absent",
            "novelty_flag": "unstudied",
            "literature_gap": "unknown",
            "synthesis": "summary",
        },
    )

    evidence = librarian.retrieve_evidence(formalized)

    claim = evidence["claim_evidence"]["claim_1"]
    assert claim["queries_used"] == []
    assert claim["librarian_errors"] == ["query expansion failed: list index out of range"]


def test_librarian_degrades_when_synthesis_fails(monkeypatch):
    formalized = {
        "core_hypothesis": "A hypothesis",
        "domain": "biology",
        "key_entities": [],
        "starter_entities": [],
        "cited_literature": [],
        "atomic_claims": [
            {"id": "claim_1", "statement": "GENE1 changes outcome", "null_hypothesis": "GENE1 does not change outcome"},
        ],
    }

    monkeypatch.setattr(librarian, "get_relevant_flags", lambda *args, **kwargs: [])
    monkeypatch.setattr(librarian, "format_flags_for_prompt", lambda flags: "")
    monkeypatch.setattr(librarian, "expand_queries", lambda *args, **kwargs: [])
    monkeypatch.setattr(librarian, "llm_call_json", lambda *args, **kwargs: (_ for _ in ()).throw(IndexError("list index out of range")))

    evidence = librarian.retrieve_evidence(formalized)

    claim = evidence["claim_evidence"]["claim_1"]
    assert claim["evidence_strength"] == "absent"
    assert claim["novelty_flag"] == "unstudied"
    assert claim["literature_gap"] == "Librarian synthesis failed: list index out of range"
    assert claim["librarian_errors"] == ["synthesis failed: list index out of range"]


def test_run_pipeline_overlaps_librarian_with_analyst(monkeypatch):
    lib_started = threading.Event()
    stamps = {}
    evidence = {"claim_evidence": {"claim_1": {"synthesis": "evidence"}}}

    stage1 = {
        "core_hypothesis": "GENE1 affects outcome",
        "domain": "biology",
        "key_entities": [],
        "starter_entities": ["GENE1"],
        "cited_literature": [],
        "completed_analysis": [],
    }
    stage2 = {
        "atomic_claims": [
            {"id": "claim_1", "statement": "GENE1 affects outcome", "null_hypothesis": "GENE1 does not affect outcome"},
        ],
        "key_search_terms": ["GENE1 outcome"],
    }

    def fake_retrieve_evidence(formalized, max_papers_per_claim=12, on_event=None):
        stamps["librarian_start"] = time.perf_counter()
        lib_started.set()
        if on_event:
            on_event(ev.queries_expanded("claim_1", 1))
        time.sleep(0.3)
        stamps["librarian_finish"] = time.perf_counter()
        return evidence

    def fake_run_analyst(**kwargs):
        assert lib_started.wait(timeout=1)
        stamps["analyst_start"] = time.perf_counter()
        time.sleep(0.1)
        stamps["analyst_finish"] = time.perf_counter()
        return {
            "data": {"provenance": {}},
            "data_summary": {},
            "gene_data": {},
            "paml_data": {},
            "gnomad_data": {},
            "phylo_data": {},
            "rdnds_data": {},
            "reproducibility": {},
            "set_a": [],
            "set_b": [],
            "set_a_stats": {},
            "set_b_stats": {},
            "cross_set": {},
        }

    monkeypatch.setattr(pipeline, "formalize_stage1", lambda raw_text: stage1)
    monkeypatch.setattr(pipeline, "formalize_stage2", lambda stage1: stage2)
    monkeypatch.setattr(pipeline, "retrieve_evidence", fake_retrieve_evidence)
    monkeypatch.setattr(
        pipeline.gene_sets,
        "expand",
        lambda starter_entities, hypothesis, domain: {
            "starter": starter_entities,
            "expanded": {},
            "controls": {},
            "starter_count": len(starter_entities),
        },
    )
    monkeypatch.setattr(pipeline.gene_sets, "all_genes", lambda expansion: ["GENE1"])
    monkeypatch.setattr(pipeline, "run_analyst", fake_run_analyst)
    monkeypatch.setattr(
        pipeline,
        "run_methodologist",
        lambda *args, **kwargs: {"tests_requested": [], "correction": None, "primary_tests": [], "rationale": ""},
    )
    monkeypatch.setattr(
        pipeline,
        "run_compute",
        lambda *args, **kwargs: {"compute_results": {"tests": []}, "robustness": {}},
    )
    monkeypatch.setattr(
        pipeline,
        "run_interpreter",
        lambda *args, **kwargs: {"overall_genomic_assessment": "inconclusive", "limitations": []},
    )
    monkeypatch.setattr(
        pipeline,
        "stress_test",
        lambda formalized, evidence, analyst_result=None, **kwargs: {"scores": {"overall_falsifiability_score": 5}, "verdict": "ok"},
    )

    events = list(pipeline.run_pipeline("raw input"))

    assert "run_failed" not in [event.type for event in events]
    assert stamps["librarian_start"] < stamps["analyst_finish"] < stamps["librarian_finish"]
    assert any(event.type == "queries_expanded" for event in events)
    assert any(event.type == "verdict_ready" for event in events)
    completed = [event for event in events if event.type == "run_completed"]
    assert completed
    assert completed[0].payload["evidence"] == evidence
