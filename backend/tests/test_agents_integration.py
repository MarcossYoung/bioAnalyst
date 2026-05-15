"""Smoke tests for agent/data handoff guards."""

from nullifier.agents import formalizer, librarian


def test_formalizer_stage1_normalizes_field_shapes(monkeypatch):
    payload = {
        "core_hypothesis": "  Hypothesis text  ",
        "cited_literature": "Paper A",
        "proposed_methods": "sequencing",
        "methods_used": ("analysis",),
        "completed_analysis": {"finding": "Effect observed"},
        "starter_data": None,
        "starter_entities": "GENE1",
        "domain": None,
        "key_entities": ["A", 2],
    }
    monkeypatch.setattr(formalizer, "llm_call_json", lambda *args, **kwargs: payload)

    out = formalizer.formalize_stage1("raw input")

    assert out["core_hypothesis"] == "Hypothesis text"
    assert out["cited_literature"] == ["Paper A"]
    assert out["proposed_methods"] == ["sequencing"]
    assert out["methods_used"] == ["analysis"]
    assert out["completed_analysis"] == [{"finding": "Effect observed"}]
    assert out["starter_entities"] == ["GENE1"]
    assert out["key_entities"] == ["A", "2"]
    assert out["starter_data"] == ""
    assert out["domain"] == "unknown"


def test_formalizer_stage2_normalizes_claims(monkeypatch):
    payload = {
        "atomic_claims": [
            {"statement": "X affects Y", "null_hypothesis": "X does not affect Y"},
        ],
        "key_search_terms": "X Y",
    }
    monkeypatch.setattr(formalizer, "llm_call_json", lambda *args, **kwargs: payload)

    out = formalizer.formalize_stage2({"core_hypothesis": "X affects Y"})

    assert out["atomic_claims"][0]["id"] == "claim_1"
    assert out["atomic_claims"][0]["statement"] == "X affects Y"
    assert out["atomic_claims"][0]["null_hypothesis"] == "X does not affect Y"
    assert out["key_search_terms"] == ["X Y"]


def test_librarian_preserves_paper_alignment_when_batch_is_short(monkeypatch):
    formalized = {
        "core_hypothesis": "A hypothesis",
        "domain": "biology",
        "key_entities": ["GENE1"],
        "starter_entities": ["GENE2"],
        "cited_literature": [],
        "atomic_claims": [
            {"id": "c1", "statement": "GENE1 changes outcome", "null_hypothesis": "GENE1 does not change outcome"},
        ],
    }

    monkeypatch.setattr(librarian, "get_relevant_flags", lambda *args, **kwargs: [])
    monkeypatch.setattr(librarian, "format_flags_for_prompt", lambda flags: "")
    monkeypatch.setattr(librarian, "normalize_cited_reference", lambda ref: ref)
    monkeypatch.setattr(librarian, "find_by_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        librarian,
        "expand_queries",
        lambda claim, starter_entities: [{"query": "q1"}, {"query": "q2"}],
    )
    monkeypatch.setattr(
        librarian,
        "federated_search",
        lambda query, limit, health: (
            [
                {
                    "source": "src",
                    "id": query,
                    "title": f"Title {query}",
                    "abstract": "Abstract sentence.",
                    "year": 2024,
                    "venue": "Journal",
                }
            ],
            {},
        ),
    )
    monkeypatch.setattr(
        librarian,
        "llm_call_json_batch",
        lambda *args, **kwargs: [
            {
                "classification": "supports",
                "justification_quote": "Abstract sentence.",
                "reasoning": "Matched",
            }
        ],
    )
    monkeypatch.setattr(
        librarian,
        "llm_call_json",
        lambda *args, **kwargs: {
            "claim_id": "c1",
            "confounders_identified": [],
            "evidence_strength": "moderate",
            "novelty_flag": "well-studied",
            "literature_gap": "none",
            "synthesis": "summary",
        },
    )

    evidence = librarian.retrieve_evidence(formalized)
    claim = evidence["claim_evidence"]["c1"]

    assert len(claim["retrieved_papers"]) == 2
    assert len(claim["classifications"]) == 1
    assert len(claim["failed_classifications"]) == 1
