import pytest

from nullifier.agents.skeptic import _apply_guardrails
from nullifier.tools.compute import mirrortree_lite
from nullifier.tools.diagnostics import (
    FP_RISK_WEIGHTS,
    RISK_TIER_EXCLUDED,
    RISK_TIER_FLAGGED,
    assess_aligner_rate_sensitivity,
    fp_risk,
    populate_result_changes_with_aligner,
    score_record,
    tier,
)
from nullifier.tools.genomic_data import per_gene_rate_vectors


def test_fp_risk_skips_null_aligner_term_and_sums_named_weights():
    record = {
        "alignment": {
            "result_changes_with_aligner": None,
            "mafft_prank_agreement": 0.7,
        },
        "recombination": {"gard_breakpoints": 1, "action": "none"},
        "saturation": {"saturated_branch_fraction": 0.7, "surviving_branches": 3},
        "gbgc": {"risk": "high"},
        "power": {"usable": False, "exclusion_reason": "too_few_taxa"},
        "ng86_crosscheck": {"model_vs_ng86_divergence": 0.6},
    }

    risk, reasons = fp_risk(record)

    expected = (
        FP_RISK_WEIGHTS["low_alignment_confidence"]
        + FP_RISK_WEIGHTS["recombination"]
        + FP_RISK_WEIGHTS["saturation"]
        + FP_RISK_WEIGHTS["gbgc"]
        + FP_RISK_WEIGHTS["low_power"]
        + FP_RISK_WEIGHTS["ng86_divergence"]
    )
    assert risk == pytest.approx(expected)
    assert "result_changes_with_aligner_not_assessed_weight_skipped" in reasons
    assert "result_changes_with_aligner" not in reasons
    assert tier(risk) == RISK_TIER_EXCLUDED


def test_fp_risk_flags_aligner_sensitive_record():
    record = {
        "alignment": {"result_changes_with_aligner": True},
        "recombination": {"gard_breakpoints": 0},
        "power": {"usable": True},
    }

    scored = score_record(record)

    assert scored["risk"] == pytest.approx(FP_RISK_WEIGHTS["result_changes_with_aligner"])
    assert scored["tier"] == RISK_TIER_FLAGGED
    assert "vgenes5_v1" in scored["calibration_state"]


def test_aligner_branch_rate_sensitivity_populates_risk_flag():
    changed, note = assess_aligner_rate_sensitivity(
        {"rates": {"b1": 1.0, "b2": 1.2, "b3": 0.8}},
        {"rates": {"b1": 1.7, "b2": 1.2, "b3": 0.8}},
    )

    assert changed is True
    assert "shared_branches=3" in note

    diagnostics = populate_result_changes_with_aligner(
        {"G1": {"alignment": {"result_changes_with_aligner": None}}},
        {
            "G1": {
                "mafft": {"rates": {"b1": 1.0, "b2": 1.2, "b3": 0.8}},
                "prank": {"rates": {"b1": 1.7, "b2": 1.2, "b3": 0.8}},
            }
        },
    )

    scored = score_record(diagnostics["G1"])
    assert diagnostics["G1"]["alignment"]["result_changes_with_aligner"] is True
    assert "result_changes_with_aligner" in scored["reasons"]


def test_rate_vectors_exclude_high_risk_genes_before_scoring():
    gene_data = {
        "A": {"orthologs": [{"target_species": "s1", "ortholog_type": "ortholog_one2one"}]},
        "B": {"orthologs": [{"target_species": "s1", "ortholog_type": "ortholog_one2one"}]},
        "C": {"orthologs": [{"target_species": "s1", "ortholog_type": "ortholog_one2one"}]},
    }
    expansion = {
        "starter": ["A", "B"],
        "expanded": {"bbb": ["C"]},
        "background": {"background.random_300": []},
    }
    rdnds = {"A": {"s1": 0.2}, "B": {"s1": 0.3}, "C": {"s1": 0.4}}
    diagnostics = {
        "A": {"alignment": {"result_changes_with_aligner": True}},
        "B": {
            "alignment": {"result_changes_with_aligner": True, "mafft_prank_agreement": 0.1},
            "power": {"usable": False, "exclusion_reason": "too_few_taxa"},
        },
    }

    out = per_gene_rate_vectors(
        gene_data,
        expansion,
        rdnds,
        panel=["s1"],
        diagnostics=diagnostics,
        min_low_risk_genes=2,
    )

    assert "A" in out["sets"]["starter"]
    assert "B" not in out["sets"]["starter"]
    assert out["risk_filter"]["flagged_genes"] == ["A"]
    assert out["risk_filter"]["excluded_genes"] == ["B"]
    assert out["set_usability"]["starter"]["risk_degraded"] is True


def test_mirrortree_lite_names_risk_degraded_skip_reason():
    rate_vectors = {
        "panel": ["s1", "s2", "s3"],
        "sets": {
            "starter": ["A"],
            "expanded.bbb": ["B"],
            "background.random_300": ["C1", "C2"],
        },
        "rates": {
            "A": [1.0, 2.0, 3.0],
            "B": [2.0, 4.0, 6.0],
            "C1": [0.0, 0.1, 0.0],
            "C2": [0.1, 0.0, 0.1],
        },
        "set_usability": {
            "starter": {"usable": False, "risk_degraded": True, "reason": "too few genes survive FP-risk filter"},
            "expanded.bbb": {"usable": True, "reason": ""},
        },
    }

    result = mirrortree_lite(rate_vectors, {"set_b": "expanded.bbb", "min_shared_species": 3, "n_iter": 20})

    assert result["available"] is False
    assert "set starter degraded: too few genes survive FP-risk filter" in result["skip_reason"]


def test_skeptic_guardrail_uses_named_risk_reason():
    verdict = {"scores": {"genomic_evidence_alignment": 7}, "verdict_justification": "Base."}
    analyst = {
        "compute_results": {"tests": []},
        "interpretation": {},
        "dnds_saturation": {
            "flag": True,
            "sets": {"set_a": {"risk_degraded": True}},
        },
    }

    out = _apply_guardrails(verdict, {}, analyst)

    assert out["scores"]["genomic_evidence_alignment"] is None
    assert "Risk filter left too few scorable genes" in out["verdict_justification"]


def test_skeptic_genomic_axis_is_advisory_until_promoted():
    verdict = {"scores": {"genomic_evidence_alignment": 7}, "verdict_justification": "Base."}
    analyst = {
        "compute_results": {"tests": [{"test": "mirrortree_lite", "available": True}]},
        "interpretation": {"overall_genomic_assessment": "supports"},
        "dnds_saturation": {"flag": False},
    }

    out = _apply_guardrails(verdict, {}, analyst, config={"genomics": {"axis_promoted": False}})

    assert out["scores"]["genomic_evidence_alignment"] is None
    assert "advisory pending Stage 5 promotion" in out["verdict_justification"]


def test_skeptic_keeps_genomic_score_after_promotion():
    verdict = {"scores": {"genomic_evidence_alignment": 7}, "verdict_justification": "Base."}
    analyst = {
        "compute_results": {"tests": [{"test": "mirrortree_lite", "available": True}]},
        "interpretation": {"overall_genomic_assessment": "supports"},
        "dnds_saturation": {"flag": False},
    }

    out = _apply_guardrails(verdict, {}, analyst, config={"genomics": {"axis_promoted": True}})

    assert out["scores"]["genomic_evidence_alignment"] == 7
    assert "advisory pending Stage 5 promotion" not in out["verdict_justification"]
