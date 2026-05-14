"""Smoke tests for tools/compute.py. Run from repo root with:

    PYTHONPATH=backend python -m pytest backend/tests/test_compute.py -q
"""
import math

import pytest

from nullifier.tools import compute as c


def test_kruskal_separates_groups():
    r = c.kruskal_wallis({"a": [1, 2, 3], "b": [10, 11, 12], "c": [20, 21, 22]})
    assert r["significant"] is True
    assert r["df"] == 2
    assert 0 < r["p_value"] < 0.05
    assert r["effect_size_name"] == "epsilon_squared"


def test_kruskal_handles_empty():
    r = c.kruskal_wallis({"a": [1, 2, 3]})
    assert "error" in r


def test_spearman_perfect_rank():
    r = c.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
    assert r["statistic"] == pytest.approx(1.0)
    assert r["significant"] is True


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
    assert out["tests"][2]["available"] is False
    assert any(t.get("p_value_adjusted") is not None for t in out["tests"][:2])
    assert out["corrections_applied"][0]["adjust_method"] == "fdr_bh"


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
