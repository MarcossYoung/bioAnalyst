"""Smoke tests for agent/data handoff guards."""

from nullifier.agents import formalizer, librarian, methodologist
from nullifier.agents.analyst import _set_statistics
from nullifier.agents.semantic import normalize_atomic_claim
from nullifier.tools.genomic_data import build_data
from nullifier.tools.literature import citation_similarity
from nullifier.tools.query_expander import expand_queries


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
    assert out["atomic_claims"][0]["construct"] == "set_difference"
    assert out["key_search_terms"] == ["X Y"]


def test_formalizer_stage2_infers_cross_lineage_construct(monkeypatch):
    payload = {
        "atomic_claims": [
            {
                "statement": "Synaptic and BBB genes have correlated rates of sequence evolution across mammalian species",
                "null_hypothesis": "Rates are independent across species",
            },
        ],
        "key_search_terms": [],
    }
    monkeypatch.setattr(formalizer, "llm_call_json", lambda *args, **kwargs: payload)

    out = formalizer.formalize_stage2({"core_hypothesis": "co-evolution"})

    assert out["atomic_claims"][0]["construct"] == "cross_lineage_rate_correlation"


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
    assert claim["classification_summary"] == {
        "retrieved": 2,
        "classified": 1,
        "dropped": 1,
        "drop_reasons": {"other": 1},
        "classifier_degraded": False,
    }


def test_librarian_marks_classifier_degraded_and_drop_reason(monkeypatch):
    formalized = {
        "core_hypothesis": "A hypothesis",
        "domain": "biology",
        "key_entities": [],
        "starter_entities": [],
        "cited_literature": [],
        "atomic_claims": [
            {"id": "c1", "statement": "GENE1 changes outcome", "null_hypothesis": "GENE1 does not change outcome"},
        ],
    }

    monkeypatch.setattr(librarian, "get_relevant_flags", lambda *args, **kwargs: [])
    monkeypatch.setattr(librarian, "format_flags_for_prompt", lambda flags: "")
    monkeypatch.setattr(librarian, "expand_queries", lambda claim, starter_entities: [{"query": "q1"}])
    monkeypatch.setattr(
        librarian,
        "federated_search",
        lambda query, limit, health: (
            [
                {"source": "src", "id": "1", "title": "Title 1", "abstract": "A.", "year": 2024},
                {"source": "src", "id": "2", "title": "Title 2", "abstract": "B.", "year": 2024},
            ],
            {},
        ),
    )
    monkeypatch.setattr(
        librarian,
        "llm_call_json_batch",
        lambda *args, **kwargs: [
            {"_error": "Error code: 400 - {'error': \"'response_format.type' must be 'json_schema' or 'text'\"}"},
            {"_error": "Error code: 400 - {'error': \"'response_format.type' must be 'json_schema' or 'text'\"}"},
        ],
    )
    monkeypatch.setattr(
        librarian,
        "llm_call_json",
        lambda *args, **kwargs: {
            "claim_id": "c1",
            "confounders_identified": [],
            "evidence_strength": "absent",
            "novelty_flag": "unstudied",
            "literature_gap": "unknown",
            "synthesis": "summary",
        },
    )

    evidence = librarian.retrieve_evidence(formalized)
    summary = evidence["claim_evidence"]["c1"]["classification_summary"]

    assert evidence["classifier_degraded"] is True
    assert summary["classifier_degraded"] is True
    assert summary["drop_reasons"] == {"api_schema_error": 2}


def test_citation_similarity_rejects_wrong_domain_match():
    paper = {
        "title": "Radiation Resistant Camera System for Monitoring Deuterium Plasma Discharges",
        "abstract": "A camera system was constructed for monitoring plasma discharges.",
    }

    score = citation_similarity("Functionalization of a Protosynaptic Gene Expression Network", paper)

    assert score < 0.35


def test_methodologist_returns_mirrortree_for_cross_lineage_construct():
    plan = methodologist.run_methodologist(
        {
            "core_hypothesis": "co-evolution",
            "atomic_claims": [
                {
                    "id": "c1",
                    "statement": "Rates correlate across lineages",
                    "null_hypothesis": "Rates do not correlate",
                    "construct": "cross_lineage_rate_correlation",
                }
            ],
        },
        {"starter_count": 2},
        {"groups": {}, "variables": {}, "n_genes": 0},
    )

    assert plan.get("untestable") is not True
    assert plan["tests_requested"][0]["test"] == "mirrortree_lite"
    assert plan["tests_requested"][0]["inputs"]["background"] == "background.random_300"
    assert plan["claim_constructs"] == ["cross_lineage_rate_correlation"]
    assert plan["primary_tests"] == []


def test_build_data_attaches_filtered_rate_vectors():
    expansion = {
        "starter": ["G1"],
        "expanded": {"bbb": ["G2"]},
        "background": {"background.random_300": ["BG1"]},
    }
    gene_data = {
        "G1": {
            "orthologs": [
                {"target_species": "mus_musculus", "ortholog_type": "ortholog_one2one"},
                {"target_species": "rattus_norvegicus", "ortholog_type": "ortholog_one2one"},
                {"target_species": "canis_lupus_familiaris", "ortholog_type": "ortholog_one2many"},
            ],
        },
        "G2": {
            "orthologs": [
                {"target_species": "mus_musculus", "ortholog_type": "ortholog_one2one"},
                {"target_species": "rattus_norvegicus", "ortholog_type": "ortholog_one2one"},
            ],
        },
        "BG1": {
            "orthologs": [
                {"target_species": "mus_musculus", "ortholog_type": "ortholog_one2one"},
                {"target_species": "rattus_norvegicus", "ortholog_type": "ortholog_one2one"},
            ],
        },
    }
    rdnds_data = {
        "G1": {"mus_musculus": 1.0, "rattus_norvegicus": 0.2, "canis_lupus_familiaris": 0.3},
        "G2": {"mus_musculus": 0.4, "rattus_norvegicus": 10.0},
        "BG1": {"mus_musculus": 0.15, "rattus_norvegicus": 0.25},
    }

    data = build_data(
        gene_data,
        expansion,
        rdnds_data=rdnds_data,
        panel=["mus_musculus", "rattus_norvegicus", "canis_lupus_familiaris"],
    )

    vectors = data["rate_vectors"]
    assert data["gene_index"] == ["G1", "G2"]
    assert vectors["gene_index"] == ["G1", "G2", "BG1"]
    assert vectors["sets"]["expanded.bbb"] == ["G2"]
    assert vectors["rates"]["G1"] == [None, 0.2, None]
    assert vectors["rates"]["G2"] == [0.4, None, None]
    assert vectors["coverage"]["BG1"]["usable_rates"] == 2
    assert data["provenance"]["rate_vectors"]["background_genes"] == 1


def test_set_statistics_flags_dnds_saturation():
    gene_data = {
        "G1": {"orthologs": [{"dnds": 1.0}], "paralogs": [], "gene_tree": {}},
        "G2": {"orthologs": [{"dnds": 1.005}], "paralogs": [], "gene_tree": {}},
        "G3": {"orthologs": [{"dnds": 0.2}], "paralogs": [], "gene_tree": {}},
    }

    stats = _set_statistics(["G1", "G2", "G3"], gene_data)

    assert stats["dnds_saturation_flag"] is True
    assert stats["dnds_saturation_fraction"] == 2 / 3


def test_query_expander_accepts_new_claim_shape(monkeypatch):
    monkeypatch.setattr(
        "nullifier.tools.query_expander.llm_call_json",
        lambda *args, **kwargs: {"queries": [{"query": "test", "intent": "direct"}]},
    )

    out = expand_queries(
        {
            "statement": "GENE1 affects outcome",
            "null_hypothesis": "GENE1 does not affect outcome",
            "entities": ["GENE1", "OUTCOME"],
        },
        starter_entities=["GENE1"],
    )

    assert out == [{"query": "test", "intent": "direct"}]


def test_normalize_atomic_claim_accepts_strings():
    claim = normalize_atomic_claim("GENE1 affects outcome", 2)
    assert claim["id"] == "claim_3"
    assert claim["statement"] == "GENE1 affects outcome"
    assert claim["null_hypothesis"] == "Not: GENE1 affects outcome"
    assert claim["construct"] == "set_difference"
