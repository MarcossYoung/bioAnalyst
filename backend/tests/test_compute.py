"""Smoke tests for tools/compute.py. Run from repo root with:

    PYTHONPATH=backend python -m pytest backend/tests/test_compute.py -q
"""
import math

import pytest

from nullifier.tools import compute as c


def assert_full_test_result(result: dict):
    missing = [field for field in c.TEST_RESULT_FIELDS if field not in result]
    assert missing == []
    c.validate_test_result(result)


def test_kruskal_separates_groups():
    r = c.kruskal_wallis({"a": [1, 2, 3], "b": [10, 11, 12], "c": [20, 21, 22]})
    assert_full_test_result(r)
    assert r["significant"] is True
    assert r["df"] == 2
    assert 0 < r["p_value"] < 0.05
    assert r["effect_size_name"] == "epsilon_squared"


def test_kruskal_handles_empty():
    r = c.kruskal_wallis({"a": [1, 2, 3]})
    assert_full_test_result(r)
    assert "error" in r


def test_spearman_perfect_rank():
    r = c.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
    assert_full_test_result(r)
    assert r["statistic"] == pytest.approx(1.0)
    assert r["significant"] is True


def test_spearman_skips_mismatched_lengths():
    r = c.spearman([1, 2, 3], [1, 2, 3, 4])
    assert_full_test_result(r)
    assert r["available"] is False
    assert r["skipped"] is True
    assert r["skip_reason"] == "x and y have different lengths"


def test_spearman_drops_nulls_but_requires_minimum_n():
    r = c.spearman([1, None, 3, 4], [1, 2, None, 4])
    assert_full_test_result(r)
    assert r["available"] is False
    assert r["skipped"] is True
    assert r["skip_reason"] == "need >=3 paired observations"


def test_spearman_skips_constant_input():
    r = c.spearman([1, 1, 1, 1], [1, 2, 3, 4])
    assert_full_test_result(r)
    assert r["available"] is False
    assert r["skipped"] is True
    assert r["skip_reason"] == "correlation undefined for constant input"


def test_pearson_ci_present():
    r = c.pearson([1, 2, 3, 4, 5, 6], [2.1, 3.9, 6.0, 8.2, 9.8, 12.1])
    assert r["ci"] and r["ci"][0] < r["statistic"] < r["ci"][1]


def test_fisher_exact_2x2():
    r = c.fisher_exact([[8, 2], [1, 9]])
    assert r["significant"] is True
    assert r["effect_size_name"] == "odds_ratio"


def test_chi_square_flags_low_expected():
    r = c.chi_square([[1, 2], [3, 4]])
    assert r["low_expected_counts"] is True


def test_bonferroni_caps_at_one():
    r = c.bonferroni([0.5, 0.6, 0.7])
    assert all(p == 1.0 for p in r["pvals_adjusted"])
    assert r["reject"] == [False, False, False]


def test_bh_monotone_and_correct():
    # Known BH adjustment of [0.01, 0.02, 0.2, 0.5] with n=4
    r = c.benjamini_hochberg([0.01, 0.02, 0.2, 0.5])
    p = r["pvals_adjusted"]
    assert p[0] == pytest.approx(0.04, abs=1e-6)
    assert p[1] == pytest.approx(0.04, abs=1e-6)
    assert p[2] == pytest.approx(0.2666667, abs=1e-3)
    assert p[3] == pytest.approx(0.5, abs=1e-6)
    assert all(p[i] <= p[i + 1] + 1e-9 for i in range(3))
    assert r["reject"] == [True, True, False, False]


def test_bootstrap_ci_brackets_true_mean():
    vals = [10, 11, 9, 10, 12, 8, 10, 11, 9, 10]
    r = c.bootstrap_ci(vals, statistic="mean", n_iter=2000, seed=1)
    lo, hi = r["ci"]
    assert lo < 10 < hi


def test_permutation_test_finds_difference():
    r = c.permutation_test([1, 2, 3, 4], [10, 11, 12, 13], n_iter=5000, seed=1)
    assert r["significant"] is True


def test_cliffs_delta_directional():
    pos = c.cliffs_delta([5, 6, 7], [1, 2, 3])
    neg = c.cliffs_delta([1, 2, 3], [5, 6, 7])
    assert pos["effect_size"] == pytest.approx(1.0)
    assert neg["effect_size"] == pytest.approx(-1.0)


def test_run_analysis_plan_dispatch_and_correction():
    data = {
        "groups": {
            "syn": {"dnds": [0.10, 0.12, 0.09, 0.11]},
            "ctrl": {"dnds": [0.40, 0.45, 0.42, 0.50]},
        },
        "variables": {"x": [1, 2, 3, 4, 5], "y": [2.1, 3.9, 6.1, 8.0, 10.2]},
        "gene_index": ["G1", "G2"], "tables": {},
    }
    plan = {"tests_requested": [
        {"test": "kruskal_wallis", "inputs": {"metric": "dnds", "groups": ["syn", "ctrl"]}},
        {"test": "pearson", "inputs": {"x": "x", "y": "y"}},
        {"test": "made_up", "inputs": {}},
    ], "correction": "benjamini_hochberg"}
    out = c.run_analysis_plan(plan, data)
    assert len(out["tests"]) == 3
    for result in out["tests"]:
        assert_full_test_result(result)
    assert out["tests"][2]["available"] is False
    assert any(t.get("p_value_adjusted") is not None for t in out["tests"][:2])
    assert out["corrections_applied"][0]["adjust_method"] == "fdr_bh"


def test_run_analysis_plan_never_produces_sparse_test_results():
    data = {
        "groups": {
            "a": {"dnds": [0.10, 0.12, 0.09, 0.11]},
            "b": {"dnds": [0.30, 0.35, 0.33, 0.31]},
            "c": {"dnds": [0.50, 0.55, 0.53, 0.51]},
        },
        "variables": {
            "x": [1, 2, 3, 4, 5, 6],
            "y": [2, 4, 6, 8, 10, 12],
            "values": [10, 11, 9, 10, 12, 8],
        },
        "tables": {"contingency": [[8, 2], [1, 9]], "rxc": [[10, 20, 30], [6, 9, 17]]},
        "gene_index": ["G1", "G2", "G3"],
    }
    plan = {"tests_requested": [
        {"test": "kruskal_wallis", "inputs": {"metric": "dnds", "groups": ["a", "b", "c"]}},
        {"test": "mann_whitney_posthoc", "inputs": {"metric": "dnds", "groups": ["a", "b"]}},
        {"test": "spearman", "inputs": {"x": "x", "y": "y"}},
        {"test": "pearson", "inputs": {"x": "x", "y": "y"}},
        {"test": "fisher_exact", "inputs": {"table": "contingency"}},
        {"test": "chi_square", "inputs": {"table": "rxc"}},
        {"test": "bootstrap_ci", "inputs": {"values": "values", "statistic": "mean"}},
        {"test": "permutation_test", "inputs": {"a": "a.dnds", "b": "b.dnds"}},
        {"test": "cliffs_delta", "inputs": {"a": "a.dnds", "b": "b.dnds"}},
        {"test": "cohens_d", "inputs": {"a": "a.dnds", "b": "b.dnds"}},
        {"test": "made_up_test", "inputs": {}},
    ], "correction": "benjamini_hochberg"}

    out = c.run_analysis_plan(plan, data)

    assert len(out["tests"]) == len(plan["tests_requested"])
    for result in out["tests"]:
        assert_full_test_result(result)
    assert out["tests"][-1]["available"] is False


def test_mann_whitney_posthoc_populates_and_corrects_pairs():
    r = c.mann_whitney_posthoc({
        "a": [0.10, 0.11, 0.12, 0.13],
        "b": [0.30, 0.31, 0.32, 0.33],
        "c": [0.50, 0.51, 0.52, 0.53],
    })
    assert_full_test_result(r)
    pairs = r["details"]["pairs"]
    assert len(pairs) == 3
    assert all(pair.get("p_value") is not None for pair in pairs)
    assert all(pair.get("p_value_adjusted") is not None for pair in pairs)
    assert r["details"]["correction"]["method"] == "fdr_bh"


def test_all_results_explain_ci_when_bounds_absent():
    results = [
        c.kruskal_wallis({"a": [1, 2, 3], "b": [4, 5, 6]}),
        c.fisher_exact([[8, 2], [1, 9]]),
        c.chi_square([[10, 20], [5, 25]]),
        c.mann_whitney_posthoc({"a": [1, 2, 3], "b": [4, 5, 6]}),
    ]
    for result in results:
        assert_full_test_result(result)
        if result["ci_lower"] is None and result["ci_upper"] is None:
            assert isinstance(result["ci"], dict)
            assert result["ci"]["reason"]


def test_run_analysis_plan_preserves_unavailable_paml_result():
    plan = {"tests_requested": [
        {"test": "paml_branch_model", "inputs": {"foreground": "primates"}},
    ]}
    data = {"paml": {
        "GENE1": {"status": "codeml_unavailable", "gene": "GENE1"},
        "GENE2": {"status": "no_alignment", "gene": "GENE2"},
    }}

    out = c.run_analysis_plan(plan, data)
    result = out["tests"][0]

    assert_full_test_result(result)
    assert result["available"] is False
    assert result["details"]["paml_status_counts"] == {
        "codeml_unavailable": 1,
        "no_alignment": 1,
    }


def test_run_analysis_plan_returns_typed_untestable_result():
    plan = {
        "untestable": True,
        "required_construct": "cross_lineage_rate_correlation",
        "untestable_reason": "requires mirrortree_lite",
    }

    out = c.run_analysis_plan(plan, {"groups": {}, "variables": {}, "gene_index": [], "tables": {}})
    result = out["tests"][0]

    assert out["untestable"] is True
    assert result["available"] is False
    assert result["skipped"] is True
    assert result["skip_reason"] == "requires mirrortree_lite"
    assert_full_test_result(result)


def test_residualize_rate_vectors_uses_background_means():
    rate_vectors = {
        "panel": ["s1", "s2", "s3"],
        "sets": {"background.random_300": ["BG1", "BG2"]},
        "rates": {
            "BG1": [1.0, 2.0, None],
            "BG2": [3.0, None, 6.0],
            "G1": [4.0, 5.0, 7.0],
        },
    }

    residualized = c.residualize_rate_vectors(rate_vectors)

    assert residualized["background_means"] == [2.0, 2.0, 6.0]
    assert residualized["rates"]["G1"] == [2.0, 3.0, 1.0]
    assert residualized["rates"]["BG1"] == [-1.0, 0.0, None]


def test_mirrortree_lite_detects_known_cross_signal():
    rate_vectors = {
        "panel": ["s1", "s2", "s3", "s4", "s5"],
        "sets": {
            "starter": ["A1", "A2"],
            "expanded.bbb": ["B1", "B2"],
            "background.random_300": ["C1", "C2", "C3", "C4"],
        },
        "rates": {
            "A1": [1.0, 2.0, 3.0, 4.0, 5.0],
            "A2": [1.1, 2.1, 3.1, 4.1, 5.1],
            "B1": [2.0, 4.0, 6.0, 8.0, 10.0],
            "B2": [2.1, 4.2, 6.1, 8.1, 10.2],
            "C1": [0.0, 0.0, 0.0, 0.0, 0.0],
            "C2": [0.2, -0.1, 0.1, -0.2, 0.0],
            "C3": [-0.1, 0.2, -0.2, 0.1, 0.0],
            "C4": [0.1, 0.1, -0.1, -0.1, 0.0],
        },
    }

    result = c.mirrortree_lite(
        rate_vectors,
        {
            "set_a": "starter",
            "set_b": "expanded.bbb",
            "background": "background.random_300",
            "min_shared_species": 3,
            "n_iter": 200,
            "seed": 1,
        },
    )

    assert_full_test_result(result)
    assert result["available"] is True
    assert result["statistic"] > 0.95
    assert result["effect_size"] > 0
    assert result["details"]["cross_pair_count"] == 4
    assert result["details"]["null_n"] > 0


def test_erc_detects_known_branch_rate_covariation():
    rate_vectors = {
        "panel": ["b1", "b2", "b3", "b4", "b5", "b6"],
        "sets": {
            "starter": ["A1", "A2"],
            "expanded.bbb": ["B1", "B2"],
            "controls.matched": ["C1", "C2", "C3", "C4"],
            "background.random_300": ["BG1", "BG2"],
        },
        "rates": {
            "A1": [1, 2, 3, 4, 5, 6],
            "A2": [1.1, 2.1, 3.1, 4.1, 5.1, 6.1],
            "B1": [2, 4, 6, 8, 10, 12],
            "B2": [2.2, 4.1, 6.2, 8.1, 10.2, 12.1],
            "C1": [6, 1, 5, 2, 4, 3],
            "C2": [3, 6, 2, 5, 1, 4],
            "C3": [2, 5, 1, 4, 6, 3],
            "C4": [4, 2, 6, 1, 5, 3],
            "BG1": [0, 0, 0, 0, 0, 0],
            "BG2": [0, 0, 0, 0, 0, 0],
        },
        "provenance": {"source": "iqtree_fixed_topology_relative_branch_rates"},
    }

    result = c.erc(
        rate_vectors,
        {
            "set_b": "expanded.bbb",
            "controls": ["controls.matched"],
            "min_shared_branches": 5,
            "n_iter": 200,
            "seed": 1,
        },
    )

    assert_full_test_result(result)
    assert result["available"] is True
    assert result["statistic"] > 0.99
    assert result["effect_size_name"] == "erc_r_minus_matched_control_mean_r"
    assert result["details"]["null_n"] > 0


def test_mirrortree_lite_skips_degraded_set():
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
            "starter": {"usable": False, "reason": "set_a: too few computable rates"},
            "expanded.bbb": {"usable": True, "reason": ""},
        },
    }

    result = c.mirrortree_lite(
        rate_vectors,
        {"set_b": "expanded.bbb", "min_shared_species": 3, "n_iter": 20},
    )

    assert_full_test_result(result)
    assert result["available"] is False
    assert result["skipped"] is True
    assert "starter" in result["skip_reason"]


def test_run_analysis_plan_dispatches_mirrortree_lite():
    data = {
        "rate_vectors": {
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
        }
    }
    plan = {"tests_requested": [
        {
            "test": "mirrortree_lite",
            "inputs": {"set_b": "expanded.bbb", "min_shared_species": 3, "n_iter": 20},
        }
    ]}

    out = c.run_analysis_plan(plan, data)

    assert len(out["tests"]) == 1
    assert_full_test_result(out["tests"][0])
    assert out["tests"][0]["test"] == "mirrortree_lite"


def test_run_analysis_plan_dispatches_erc():
    data = {
        "rate_vectors": {
            "panel": ["b1", "b2", "b3", "b4", "b5"],
            "sets": {
                "starter": ["A1", "A2"],
                "expanded.bbb": ["B1", "B2"],
                "controls.matched": ["C1", "C2"],
            },
            "rates": {
                "A1": [1, 2, 3, 4, 5],
                "A2": [1, 2.1, 3.1, 4.1, 5.1],
                "B1": [2, 4, 6, 8, 10],
                "B2": [2.2, 4.2, 6.2, 8.2, 10.2],
                "C1": [5, 1, 4, 2, 3],
                "C2": [3, 5, 1, 4, 2],
            },
            "provenance": {"source": "iqtree_fixed_topology_relative_branch_rates"},
        }
    }
    plan = {"tests_requested": [
        {"test": "erc", "inputs": {"set_b": "expanded.bbb", "controls": ["controls.matched"], "n_iter": 20}},
    ]}

    out = c.run_analysis_plan(plan, data)

    assert len(out["tests"]) == 1
    assert_full_test_result(out["tests"][0])
    assert out["tests"][0]["test"] == "erc"


def test_rerconverge_summarizes_secondary_container_result():
    data = {
        "phenotypes": {
            "cortical_neurons": {
                "name": "cortical_neurons",
                "label": "Cortical neuron number",
                "usable_species": 22,
                "min_species": 20,
                "underpowered": False,
                "primate_coverage": 7,
                "non_primate_coverage": 15,
            }
        },
        "rerconverge": {
            "status": "computed",
            "trait": "cortical_neurons",
            "set_results": {
                "starter": {"r": 0.71, "p_value": 0.01, "n": 22},
                "expanded.bbb": {"r": 0.55, "p_value": 0.04, "n": 22},
            },
            "control_results": {
                "controls.matched": {"r": 0.20, "p_value": 0.3, "n": 22},
            },
            "primate_out_results": {
                "starter": {"r": 0.52, "p_value": 0.08, "n": 15},
            },
            "secondary": True,
            "method": "RERconverge rate-phenotype correlation",
            "source": "rerconverge_container",
        },
    }

    result = c.rerconverge_test(
        {"sets": ["starter", "expanded.bbb"], "controls": ["controls.matched"]},
        data,
    )

    assert_full_test_result(result)
    assert result["available"] is True
    assert result["secondary"] is True
    assert result["primate_confounded"] is False
    assert result["effect_size_name"] == "abs_rer_trait_r_minus_control_mean_abs_r"
    assert result["effect_size"] == pytest.approx(0.51)
    assert result["details"]["secondary_to"] == "erc"
    assert "shared drivers" in result["warnings"][0]


def test_rerconverge_reports_primate_confounded_association():
    data = {
        "phenotypes": {
            "cortical_neurons": {
                "name": "cortical_neurons",
                "usable_species": 21,
                "min_species": 20,
                "underpowered": False,
            }
        },
        "rerconverge": {
            "status": "computed",
            "set_results": {"starter": {"r": 0.80, "p_value": 0.005, "n": 21}},
            "control_results": {"controls.matched": {"r": 0.10, "n": 21}},
            "primate_out_results": {"starter": {"r": 0.10, "n": 14}},
        },
    }

    result = c.rerconverge_test({"sets": ["starter"], "controls": ["controls.matched"]}, data)

    assert_full_test_result(result)
    assert result["available"] is True
    assert result["primate_confounded"] is True
    assert any("primate-confounded" in warning for warning in result["warnings"])


def test_rerconverge_skips_underpowered_trait_axis():
    data = {
        "phenotypes": {
            "cortical_neurons": {
                "name": "cortical_neurons",
                "usable_species": 12,
                "min_species": 20,
                "underpowered": True,
                "reason": "only 12 panel species have cortical-neuron counts; need >= 20",
            }
        },
        "rerconverge": {},
    }

    result = c.rerconverge_test({"sets": ["starter"], "trait": "cortical_neurons"}, data)

    assert_full_test_result(result)
    assert result["available"] is False
    assert result["skipped"] is True
    assert "need >= 20" in result["skip_reason"]
    assert result["details"]["secondary"] is True


def test_run_analysis_plan_dispatches_rerconverge():
    data = {
        "phenotypes": {
            "cortical_neurons": {
                "name": "cortical_neurons",
                "usable_species": 20,
                "min_species": 20,
                "underpowered": False,
            }
        },
        "rerconverge": {
            "status": "computed",
            "set_results": {"starter": {"r": 0.5, "p_value": 0.02, "n": 20}},
            "control_results": {"controls.matched": {"r": 0.1, "n": 20}},
            "primate_out_results": {"starter": {"survives": True, "r": 0.4, "n": 13}},
        },
    }
    plan = {"tests_requested": [
        {"test": "rerconverge", "inputs": {"sets": ["starter"], "controls": ["controls.matched"]}},
    ]}

    out = c.run_analysis_plan(plan, data)

    assert len(out["tests"]) == 1
    assert_full_test_result(out["tests"][0])
    assert out["tests"][0]["test"] == "rerconverge"
    assert out["data_summary"]["rerconverge"]["secondary"] is True


def test_leave_one_out_skips_when_primary_tests_have_no_result():
    def rebuild(_excluded: set) -> dict:
        return {"groups": {}, "variables": {"x": [1, 2], "y": [2, 3]}, "gene_index": [], "tables": {}}

    res = c.leave_one_out(
        ["G1", "G2"],
        [{"test": "spearman", "inputs": {"x": "x", "y": "y"}}],
        rebuild,
    )
    assert res["applicable"] is False
    assert res["status"] == "skipped"
    assert res["reason"] == "primary tests had insufficient results to perturb"


def test_leave_one_out_stable_when_signal_robust():
    # Three "syn" with dnds=0.1, three "ctrl" with dnds=0.5; dropping any one of either
    # group should keep the kruskal H-test significant → "stable".
    base = {"syn": {f"S{i}": 0.10 + 0.005 * i for i in range(8)},
            "ctrl": {f"C{i}": 0.50 + 0.005 * i for i in range(8)}}

    def rebuild(excluded: set) -> dict:
        groups = {g: [v for k, v in members.items() if k not in excluded]
                  for g, members in base.items()}
        return {"groups": {g: {"dnds": vals} for g, vals in groups.items()},
                "variables": {}, "gene_index": [], "tables": {}}

    primary = [{"test": "kruskal_wallis", "inputs": {"metric": "dnds", "groups": ["syn", "ctrl"]}}]
    all_genes = list(base["syn"]) + list(base["ctrl"])
    res = c.leave_one_out(all_genes, primary, rebuild)
    assert res["stability"] == "stable"
    assert res["agreement_fraction"] >= 0.8


def test_leave_one_out_fragile_when_signal_driven_by_one_gene():
    # Borderline: dropping G1 flips significance. Set up so the full result is sig
    # but each single drop changes it.
    base = {"syn": {"G1": 0.05, "G2": 0.10, "G3": 0.12},
            "ctrl": {"G4": 0.40, "G5": 0.30, "G6": 0.13}}

    def rebuild(excluded: set) -> dict:
        groups = {g: [v for k, v in members.items() if k not in excluded]
                  for g, members in base.items()}
        return {"groups": {g: {"dnds": vals} for g, vals in groups.items()},
                "variables": {}, "gene_index": [], "tables": {}}

    primary = [{"test": "mann_whitney_posthoc",
                "inputs": {"metric": "dnds", "groups": ["syn", "ctrl"]}}]
    # mann_whitney_posthoc returns a structure without a top-level p_value; the
    # stability check only inspects significant/statistic. Use pearson instead
    # on a small noisy series so dropping the driving point flips significance.
    primary = [{"test": "pearson", "inputs": {"x": "x", "y": "y"}}]

    def rebuild2(excluded: set) -> dict:
        x = [1, 2, 3, 4, 5]
        y = [1.0, 2.0, 3.0, 4.0, 10.0]  # last point drives the slope
        keep = [i for i, g in enumerate(["G1", "G2", "G3", "G4", "G5"]) if g not in excluded]
        return {"groups": {}, "variables": {"x": [x[i] for i in keep], "y": [y[i] for i in keep]},
                "gene_index": [], "tables": {}}

    res = c.leave_one_out(["G1", "G2", "G3", "G4", "G5"], primary, rebuild2)
    assert res["agreement_fraction"] < 1.0
    assert res["most_influential_genes"]


def test_verify_reported_stats_flags_small_n():
    completed = [{"finding": "spearman correlation between dN/dS and ortholog count",
                  "statistic": "rho=0.92, p=0.04", "test": "Spearman", "sample_size": "n=4"}]
    retrievable = {"GENE1": {"available": True, "ortholog_count": 50}}
    out = c.verify_reported_stats(completed, retrievable)
    assert out["total"] == 1
    note = out["checks"][0]["note"]
    assert "n=4" in note
