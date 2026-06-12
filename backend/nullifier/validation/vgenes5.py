"""V-Genes Stage 5 validation harness.

This runner evaluates the source-pinned benchmark in
``validation/benchmarks/vgenes5_v1``. The negative artifact cases calibrate and
test the FP-risk layer; the held-out positive case checks that the existing
mirrortree-lite path recovers the SLC30A9 comparative signal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ..tools.compute import mirrortree_lite
from ..tools.diagnostics import (
    RISK_TIER_EXCLUDED,
    RISK_TIER_FLAGGED,
    fp_risk_settings,
    score_record,
)


DEFAULT_BENCHMARK_VERSION = "vgenes5_v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def benchmark_dir(version: str = DEFAULT_BENCHMARK_VERSION, root: Path | None = None) -> Path:
    return (root or _repo_root()) / "validation" / "benchmarks" / version


def load_benchmark(version: str = DEFAULT_BENCHMARK_VERSION, root: Path | None = None) -> dict:
    path = benchmark_dir(version, root) / "manifest.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def roc_auc(artifact_scores: list[float], robust_scores: list[float]) -> float | None:
    if not artifact_scores or not robust_scores:
        return None
    wins = 0.0
    total = 0
    for artifact in artifact_scores:
        for robust in robust_scores:
            total += 1
            if artifact > robust:
                wins += 1.0
            elif artifact == robust:
                wins += 0.5
    return round(wins / total, 6) if total else None


def evaluate_negative_artifacts(benchmark: dict, *, config: dict | None = None) -> dict:
    settings = fp_risk_settings(config or {"fp_risk": benchmark.get("fp_risk") or {}})
    artifact_rows = []
    robust_rows = []
    allowed_tiers = {RISK_TIER_FLAGGED, RISK_TIER_EXCLUDED}

    for case in benchmark.get("negative_artifacts") or []:
        scored = score_record(case.get("diagnostics") or {}, weights=settings["weights"], config={"fp_risk": settings})
        row = {
            "id": case.get("id"),
            "source": case.get("source"),
            "artifact_mode": case.get("artifact_mode"),
            "risk": scored["risk"],
            "tier": scored["tier"],
            "reasons": scored["reasons"],
            "expected_reasons": case.get("expected_reasons") or [],
            "passed": scored["tier"] in allowed_tiers,
        }
        artifact_rows.append(row)

    for case in benchmark.get("robust_controls") or []:
        scored = score_record(case.get("diagnostics") or {}, weights=settings["weights"], config={"fp_risk": settings})
        row = {
            "id": case.get("id"),
            "source": case.get("source"),
            "risk": scored["risk"],
            "tier": scored["tier"],
            "reasons": scored["reasons"],
            "passed": scored["tier"] not in allowed_tiers,
        }
        robust_rows.append(row)

    auc = roc_auc(
        [float(r["risk"]) for r in artifact_rows],
        [float(r["risk"]) for r in robust_rows],
    )
    threshold = float((benchmark.get("thresholds") or {}).get("negative_auc_min", 0.95))
    return {
        "passed": all(r["passed"] for r in artifact_rows) and all(r["passed"] for r in robust_rows)
        and auc is not None and auc >= threshold,
        "auc": auc,
        "auc_threshold": threshold,
        "artifact_cases": artifact_rows,
        "robust_controls": robust_rows,
        "calibration_state": settings["calibration_state"],
        "weights": settings["weights"],
    }


def evaluate_positive_recovery(benchmark: dict) -> dict:
    case = benchmark.get("positive_case") or {}
    rate_vectors = case.get("rate_vectors") or {}
    inputs = case.get("inputs") or {}
    result = mirrortree_lite(rate_vectors, inputs)
    thresholds = case.get("thresholds") or {}
    p_max = float(thresholds.get("max_p_value", 0.05))
    min_effect = float(thresholds.get("min_effect_size", 0.25))
    statistic = result.get("statistic")
    effect = result.get("effect_size")
    p_value = result.get("p_value")
    recovered = (
        bool(result.get("available"))
        and isinstance(statistic, (int, float))
        and isinstance(effect, (int, float))
        and isinstance(p_value, (int, float))
        and statistic > 0
        and effect >= min_effect
        and p_value <= p_max
    )
    return {
        "passed": recovered,
        "id": case.get("id"),
        "source": case.get("source"),
        "query_gene": case.get("query_gene"),
        "result": result,
        "thresholds": {"max_p_value": p_max, "min_effect_size": min_effect},
    }


def run_benchmark(
    version: str = DEFAULT_BENCHMARK_VERSION,
    *,
    root: Path | None = None,
    config: dict | None = None,
) -> dict:
    benchmark = load_benchmark(version, root)
    negative = evaluate_negative_artifacts(benchmark, config=config)
    positive = evaluate_positive_recovery(benchmark)
    passed = bool(negative.get("passed") and positive.get("passed"))
    return {
        "benchmark": {
            "id": benchmark.get("id"),
            "version": benchmark.get("version"),
            "description": benchmark.get("description"),
            "sources": benchmark.get("sources") or [],
        },
        "passed": passed,
        "promotion_allowed": passed,
        "negative_artifacts": negative,
        "positive_recovery": positive,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the V-Genes Stage 5 validation benchmark.")
    parser.add_argument("--benchmark", default=DEFAULT_BENCHMARK_VERSION)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args(argv)

    result = run_benchmark(args.benchmark)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        neg = result["negative_artifacts"]
        pos = result["positive_recovery"]
        print(f"V-Genes Stage 5 {args.benchmark}: {status}")
        print(f"  negative AUC: {neg['auc']} (threshold {neg['auc_threshold']})")
        print(f"  positive recovered: {pos['passed']} ({pos['id']})")
        print(f"  promotion_allowed: {result['promotion_allowed']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

