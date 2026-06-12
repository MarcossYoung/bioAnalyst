from nullifier.validation.vgenes5 import load_benchmark, run_benchmark


def test_vgenes5_benchmark_has_locked_negative_and_positive_cases():
    benchmark = load_benchmark()

    assert [c["id"] for c in benchmark["negative_artifacts"]] == [
        "NEG-ALIGN-DROSOPHILA-2011",
        "NEG-ALIGN-SITEWISE-2011",
        "NEG-GBGC-HAR-2010",
        "NEG-GBGC-GENOME-2010",
    ]
    assert benchmark["positive_case"]["id"] == "POS-ERC-SLC30A9-2021"


def test_vgenes5_benchmark_passes_cached_stage5_gate():
    result = run_benchmark()

    assert result["passed"] is True
    assert result["promotion_allowed"] is True
    assert result["negative_artifacts"]["auc"] >= result["negative_artifacts"]["auc_threshold"]
    assert all(c["passed"] for c in result["negative_artifacts"]["artifact_cases"])
    assert all(c["passed"] for c in result["negative_artifacts"]["robust_controls"])
    assert result["positive_recovery"]["passed"] is True
    assert result["positive_recovery"]["result"]["available"] is True
