"""Smoke tests for agent/data handoff guards."""

from nullifier.agents import analyst, formalizer, interpreter, librarian, methodologist, skeptic
from nullifier.agents.analyst import _filter_expansion_to_targets, _screen_comparable, _set_statistics, _set_usability
from nullifier.agents.semantic import normalize_atomic_claim
from nullifier.tools import gene_sets
from nullifier.tools.genomic_data import build_data
from nullifier.tools.literature import citation_similarity
from nullifier.tools.llm_client import _loads_json_response
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


def test_formalizer_stage2_accepts_plain_english_alias(monkeypatch):
    payload = {
        "atomic_claims": [
            {
                "claim_id": "AC1",
                "plain_english": "Synaptic and BBB genes have correlated rates across mammals",
                "null_hypothesis": "Rates are independent across mammals",
                "construct": "cross_lineage_rate_correlation",
            },
        ],
        "key_search_terms": ["synapse BBB coevolution"],
    }
    monkeypatch.setattr(formalizer, "llm_call_json", lambda *args, **kwargs: payload)

    out = formalizer.formalize_stage2({"core_hypothesis": "co-evolution"})

    assert out["atomic_claims"][0]["id"] == "AC1"
    assert out["atomic_claims"][0]["statement"] == "Synaptic and BBB genes have correlated rates across mammals"
    assert out["atomic_claims"][0]["construct"] == "cross_lineage_rate_correlation"


def test_formalizer_stage2_accepts_bare_claim_array(monkeypatch):
    payload = [
        {
            "statement": "Synaptic and BBB genes co-evolve across mammals",
            "null_hypothesis": "Synaptic and BBB gene rates are independent across mammals",
        },
    ]
    monkeypatch.setattr(formalizer, "llm_call_json", lambda *args, **kwargs: payload)

    out = formalizer.formalize_stage2({"core_hypothesis": "co-evolution"})

    assert out["atomic_claims"][0]["statement"] == "Synaptic and BBB genes co-evolve across mammals"
    assert out["atomic_claims"][0]["construct"] == "cross_lineage_rate_correlation"
    assert out["key_search_terms"] == []


def test_formalizer_stage2_accepts_claims_alias(monkeypatch):
    payload = {
        "claims": [
            {
                "plain_english": "BBB genes show phenotype-linked evolution",
                "null": "BBB gene evolution is unrelated to phenotype",
            },
        ],
        "key_search_terms": ["BBB evolution phenotype"],
    }
    monkeypatch.setattr(formalizer, "llm_call_json", lambda *args, **kwargs: payload)

    out = formalizer.formalize_stage2({"core_hypothesis": "phenotype association"})

    assert out["atomic_claims"][0]["statement"] == "BBB genes show phenotype-linked evolution"
    assert out["atomic_claims"][0]["construct"] == "phenotype_association"
    assert out["key_search_terms"] == ["BBB evolution phenotype"]


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


def test_llm_json_parser_accepts_wrapped_json():
    assert _loads_json_response('Here is the JSON:\n{"ok": true}\nDone.') == {"ok": True}
    assert _loads_json_response('```json\n{"ok": true}\n```') == {"ok": True}


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


def test_methodologist_returns_erc_for_cross_lineage_construct():
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
        {
            "starter": ["A", "B"],
            "expanded": {"bbb.endothelial": ["C", "D"]},
            "controls": {"matched": ["E", "F"]},
        },
        {"groups": {}, "variables": {}, "n_genes": 0},
    )

    assert plan.get("untestable") is not True
    assert plan["tests_requested"][0]["test"] == "erc"
    assert plan["tests_requested"][0]["inputs"]["set_b"] == "expanded.bbb.endothelial"
    assert plan["tests_requested"][0]["inputs"]["controls"] == ["controls.matched"]
    assert plan["tests_requested"][0]["inputs"]["background"] == "background.random_300"
    assert plan["claim_constructs"] == ["cross_lineage_rate_correlation"]
    assert plan["primary_tests"][0]["test"] == "erc"


def test_methodologist_routes_phenotype_association_to_secondary_rerconverge():
    plan = methodologist.run_methodologist(
        {
            "core_hypothesis": "V genes track cortical neuron number",
            "atomic_claims": [
                {
                    "id": "c1",
                    "statement": "Rates associate with cortical neuron number",
                    "null_hypothesis": "Rates do not associate with cortical neuron number",
                    "construct": "phenotype_association",
                }
            ],
        },
        {
            "starter": ["A", "B"],
            "expanded": {"bbb.endothelial": ["C", "D"]},
            "controls": {"matched": ["E", "F"]},
        },
        {"groups": {}, "variables": {}, "n_genes": 0},
    )

    assert plan.get("untestable") is not True
    assert plan["tests_requested"][0]["test"] == "rerconverge"
    assert plan["tests_requested"][0]["inputs"]["trait"] == "cortical_neurons"
    assert plan["tests_requested"][0]["inputs"]["secondary_to"] == "erc"
    assert plan["primary_tests"] == []


def test_methodologist_keeps_erc_primary_when_rerconverge_is_added():
    plan = methodologist.run_methodologist(
        {
            "core_hypothesis": "V genes co-evolve and track cortical neuron number",
            "atomic_claims": [
                {"id": "c1", "construct": "cross_lineage_rate_correlation"},
                {"id": "c2", "construct": "phenotype_association"},
            ],
        },
        {
            "starter": ["A", "B"],
            "expanded": {"bbb": ["C", "D"]},
            "controls": {"matched": ["E", "F"]},
        },
        {"groups": {}, "variables": {}, "n_genes": 0},
    )

    requested = [entry["test"] for entry in plan["tests_requested"]]
    assert requested == ["erc", "mirrortree_lite", "rerconverge"]
    assert [entry["test"] for entry in plan["primary_tests"]] == ["erc"]


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
    assert vectors["provenance"]["source"] == "homology_pal2nal_ng86"
    assert data["provenance"]["rate_vectors"]["background_genes"] == 1
    assert data["provenance"]["rate_vectors"]["source"] == "homology_pal2nal_ng86"
    assert data["phenotypes"]["cortical_neurons"]["name"] == "cortical_neurons"
    assert data["provenance"]["phenotypes"]["cortical_neurons"]["overclaim_guard"]


def test_build_data_prefers_branch_rate_vectors_for_stage3():
    expansion = {
        "starter": ["G1", "G2"],
        "expanded": {"bbb": ["B1", "B2"]},
        "controls": {"matched": ["C1", "C2"]},
        "background": {"background.random_300": ["BG1"]},
    }
    gene_data = {g: {"orthologs": []} for g in ["G1", "G2", "B1", "B2", "C1", "C2", "BG1"]}
    branch_rate_data = {
        "G1": {"status": "computed", "rates": {"branch_1": 1.0, "branch_2": 2.0}},
        "G2": {"status": "computed", "rates": {"branch_1": 1.1, "branch_2": 2.1}},
        "B1": {"status": "computed", "rates": {"branch_1": 2.0, "branch_2": 4.0}},
        "B2": {"status": "computed", "rates": {"branch_1": 2.1, "branch_2": 4.1}},
        "C1": {"status": "computed", "rates": {"branch_1": 4.0, "branch_2": 1.0}},
        "C2": {"status": "computed", "rates": {"branch_1": 1.0, "branch_2": 4.0}},
        "BG1": {"status": "computed", "rates": {"branch_1": 0.5, "branch_2": 0.5}},
    }

    data = build_data(gene_data, expansion, branch_rate_data=branch_rate_data)
    vectors = data["rate_vectors"]

    assert vectors["panel"] == ["branch_1", "branch_2"]
    assert vectors["sets"]["controls.matched"] == ["C1", "C2"]
    assert vectors["rates"]["G1"] == [1.0, 2.0]
    assert vectors["provenance"]["source"] == "iqtree_fixed_topology_relative_branch_rates"
    assert data["provenance"]["rate_vectors"]["source"] == "iqtree_fixed_topology_relative_branch_rates"


def test_interpreter_adds_rerconverge_association_guard(monkeypatch):
    monkeypatch.setattr(
        interpreter,
        "llm_call_json",
        lambda *args, **kwargs: {
            "patterns_observed": [],
            "outlier_genes": [],
            "regulatory_overlap": {},
            "reproducibility_check": [],
            "limitations": [],
            "overall_genomic_assessment": "inconclusive",
            "assessment_justification": "secondary association only",
        },
    )
    compute_results = {
        "tests": [
            {
                "test": "rerconverge",
                "available": True,
                "effect_size": 0.3,
                "effect_size_name": "abs_rer_trait_r_minus_control_mean_abs_r",
                "ci_lower": None,
                "ci_upper": None,
                "details": {"secondary_to": "erc", "primate_confounded": False},
                "secondary": True,
            }
        ],
        "corrections_applied": [],
        "data_summary": {},
    }

    out = interpreter.run_interpreter(
        {"core_hypothesis": "h", "atomic_claims": []},
        {"starter": []},
        compute_results,
        {},
    )

    assert any("not directional or causal" in limitation for limitation in out["limitations"])


def test_interpreter_invalid_root_type_returns_inconclusive_fallback(monkeypatch):
    monkeypatch.setattr(interpreter, "llm_call_json", lambda *args, **kwargs: [])

    out = interpreter.run_interpreter(
        {"core_hypothesis": "h", "atomic_claims": []},
        {"starter": []},
        {"tests": [], "corrections_applied": [], "data_summary": {}},
        {},
    )

    assert out["overall_genomic_assessment"] == "inconclusive"
    assert out["patterns_observed"] == []
    assert "invalid root type (list)" in out["limitations"][0]


def test_gnomad_fetch_uses_nested_ensembl_ids(monkeypatch):
    calls = []

    def fake_fetch(ensembl_id):
        calls.append(ensembl_id)
        return {"loeuf": 0.42}

    monkeypatch.setattr(analyst, "fetch_constraint", fake_fetch)
    gene_data = {
        "SYP": {"info": {"ensembl_id": "ENSG_SYP"}},
        "MISSING": {"_error": "not found in Ensembl"},
        "NO_ID": {"info": {"symbol": "NO_ID"}},
    }

    out = analyst._fetch_gnomad_data(gene_data)

    assert calls == ["ENSG_SYP"]
    assert out == {"SYP": {"loeuf": 0.42}}


def test_paml_failures_emit_structured_events(monkeypatch):
    emitted = []
    gene_data = {
        "NOID": {"info": {}},
        "NOALIGN": {"info": {"ensembl_id": "ENSG_NOALIGN"}},
        "CODEML": {"info": {"ensembl_id": "ENSG_CODEML"}},
    }
    monkeypatch.setattr(
        analyst.ensembl,
        "fetch_gene_tree_aligned",
        lambda ensg, use_cache=True: None if ensg == "ENSG_NOALIGN" else {"sequences": {}, "newick": ""},
    )
    monkeypatch.setattr(
        "nullifier.tools.paml.run_branch_model",
        lambda *args, **kwargs: {
            "status": "error",
            "note": "codeml exited with a nonzero status",
            "phase": "null",
            "returncode": 1,
            "stderr": "failure",
        },
    )

    analyst._fetch_paml_data(
        gene_data,
        ["NOID", "NOALIGN", "CODEML"],
        on_event=emitted.append,
    )

    failures = [event for event in emitted if event.type == "paml.gene_failed"]
    assert [(event.payload["gene"], event.payload["status"]) for event in failures] == [
        ("NOID", "error"),
        ("NOALIGN", "no_compara_alignment"),
        ("CODEML", "error"),
    ]
    assert failures[-1].payload["phase"] == "null"
    assert failures[-1].payload["returncode"] == 1


def test_analyst_comparability_screen_drops_low_coverage_but_keeps_starters(monkeypatch):
    expansion = {
        "starter": ["S1"],
        "expanded": {"synaptic": ["G2", "G3", "UNRESOLVED"]},
        "controls": {"control": ["C1"]},
        "background": {"background.random_300": ["BG1"]},
    }
    targets = ["S1", "G2", "G3", "C1", "BG1", "UNRESOLVED"]

    monkeypatch.setattr(
        analyst,
        "_syngo_ensembl_by_symbol",
        lambda use_cache=True: {
            "S1": "ENSGS1",
            "G2": "ENSGG2",
            "G3": "ENSGG3",
            "BG1": "ENSGBG1",
        },
    )
    monkeypatch.setattr(
        analyst.ensembl,
        "lookup_gene",
        lambda gene, use_cache=True: {"symbol": gene, "ensembl_id": "ENSGC1"} if gene == "C1" else None,
    )
    monkeypatch.setattr(
        analyst.ensembl,
        "screen_panel_coverage_by_id_batch",
        lambda ensg_ids, panel, use_cache=True: {
            "ENSGS1": {"species_count": 0, "panel_species": set()},
            "ENSGG2": {"species_count": 6, "panel_species": {f"sp{i}" for i in range(6)}},
            "ENSGG3": {"species_count": 5, "panel_species": {f"sp{i}" for i in range(5)}},
            "ENSGC1": {"species_count": 6, "panel_species": {f"sp{i}" for i in range(6)}},
            "ENSGBG1": {"species_count": 0, "panel_species": set()},
        },
    )

    kept, report = _screen_comparable(
        targets,
        expansion,
        panel=[f"sp{i}" for i in range(8)],
        min_panel_species=6,
        use_cache=True,
    )

    assert kept == ["S1", "G2", "C1"]
    assert report["total"] == 6
    assert report["kept"] == 3
    assert report["dropped"] == 3
    by_gene = {row["gene"]: row for row in report["genes"]}
    assert by_gene["S1"]["kept"] is True
    assert by_gene["S1"]["reason"] == "starter"
    assert by_gene["G3"]["kept"] is False
    assert by_gene["UNRESOLVED"]["ensembl_id"] is None

    filtered = _filter_expansion_to_targets(expansion, kept)

    assert filtered["starter"] == ["S1"]
    assert filtered["expanded"]["synaptic"] == ["G2"]
    assert filtered["controls"]["control"] == ["C1"]
    assert filtered["background"]["background.random_300"] == []
    assert filtered["total_expanded"] == 1
    assert filtered["total_controls"] == 1


def test_set_statistics_flags_dnds_saturation():
    gene_data = {
        "G1": {"orthologs": [{"dnds": 1.0}], "paralogs": [], "gene_tree": {}},
        "G2": {"orthologs": [{"dnds": 1.005}], "paralogs": [], "gene_tree": {}},
        "G3": {"orthologs": [{"dnds": 0.2}], "paralogs": [], "gene_tree": {}},
    }

    stats = _set_statistics(["G1", "G2", "G3"], gene_data)

    assert stats["dnds_saturation_flag"] is True
    assert stats["dnds_saturation_fraction"] == 2 / 3
    assert stats["dnds_degraded"] is True


def test_set_statistics_reports_missing_split_gene_without_crashing():
    gene_data = {
        "SYNGAP1": {"orthologs": [{"dnds": 0.2}], "paralogs": [], "gene_tree": {}},
    }

    stats = _set_statistics(["SYP", "SYNGAP1"], gene_data)

    assert stats["valid_gene_count"] == 1
    assert stats["missing_genes"] == ["SYP"]
    assert stats["dnds_n"] == 1


def test_gene_set_expansion_keeps_only_primary_sets_for_compute(monkeypatch):
    candidates = {
        "synaptic.all": {"genes": ["SYP", "SYNGAP1"], "source": "test", "label": "synaptic.all"},
        "synaptic.process.a": {"genes": ["SYP", "A1"], "source": "test", "label": "synaptic.process.a"},
        "synaptic.process.b": {"genes": ["SYP", "B1"], "source": "test", "label": "synaptic.process.b"},
        "synaptic.process.c": {"genes": ["SYP", "C1"], "source": "test", "label": "synaptic.process.c"},
        "bbb.endothelial": {"genes": ["CLDN5"], "source": "test", "label": "bbb.endothelial"},
        "control.housekeeping": {"genes": ["GAPDH"], "source": "test", "label": "control.housekeeping"},
    }
    scores = {
        "synaptic.all": 2,
        "synaptic.process.a": 3,
        "synaptic.process.b": 3,
        "synaptic.process.c": 2,
        "bbb.endothelial": 2,
        "control.housekeeping": 2,
    }

    monkeypatch.setattr(gene_sets, "load_syngo", lambda *args, **kwargs: {})
    monkeypatch.setattr(gene_sets, "_all_canonical_sets", lambda syngo: candidates)
    monkeypatch.setattr(gene_sets, "_gemma_relevance", lambda hypothesis, candidate: (scores[candidate["label"]], "test"))
    monkeypatch.setattr(gene_sets, "random_background_genes", lambda: [])
    monkeypatch.setattr(
        gene_sets,
        "load_config",
        lambda: {"gene_sets": {"cache_ttl_days": 7, "process_min_score": 3, "max_primary_process_sets": 1}},
    )

    expansion = gene_sets.expand(["SYP"], "synapse BBB hypothesis", "biology")

    assert set(expansion["expanded"]) == {"synaptic.all", "synaptic.process.a", "bbb.endothelial"}
    assert set(expansion["exploratory"]) == {"synaptic.process.b", "synaptic.process.c"}
    assert expansion["total_expanded"] == 3
    assert expansion["total_expanded_memberships"] == 3
    assert gene_sets.all_genes(expansion) == ["SYP", "SYNGAP1", "A1", "CLDN5", "GAPDH"]


def test_set_usability_flags_sets_independently():
    set_a = {
        "dnds_n": 3,
        "dnds_saturation_fraction": 0.67,
        "dnds_degraded": True,
        "dnds_unusable_reason": "Most computable dN/dS values are pinned near 1.0.",
    }
    set_b = {
        "dnds_n": 6,
        "dnds_saturation_fraction": 0.0,
        "dnds_degraded": False,
        "dnds_unusable_reason": "",
    }

    usability = _set_usability(set_a, set_b)

    assert usability["cross_set_allowed"] is False
    assert usability["sets"]["set_a"]["usable"] is False
    assert usability["sets"]["set_b"]["usable"] is True
    assert "set_a" in usability["reason"]


def test_skeptic_sets_genomic_score_none_when_no_primary_test_ran():
    verdict = {
        "scores": {"genomic_evidence_alignment": 5, "overall_falsifiability_score": 6},
        "verdict_justification": "Initial.",
    }
    analyst_result = {
        "compute_results": {
            "tests": [{
                "test": "mirrortree_lite",
                "available": False,
                "skipped": True,
                "skip_reason": "cross-set comparison refused",
            }]
        },
        "interpretation": {"overall_genomic_assessment": "untestable"},
        "dnds_saturation": {"flag": True, "reason": "set_a degraded"},
    }

    out = skeptic._apply_guardrails(verdict, {"classifier_degraded": False}, analyst_result)

    assert out["scores"]["genomic_evidence_alignment"] is None
    assert "not scored" in out["verdict_justification"]


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
