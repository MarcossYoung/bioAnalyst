"""Runtime validators for LLM agent output contracts.

Validators report violations without raising or mutating the payload so the
pipeline can retain degraded agent output while making the degradation visible.
"""

import math
from numbers import Real
from typing import Any


CORE_SCORE_FIELDS = (
    "statistical_robustness",
    "literature_consensus",
    "mechanistic_plausibility",
    "counter_explanation_risk",
    "novelty_adjusted_confidence",
    "genomic_evidence_alignment",
    "overall_falsifiability_score",
)

OPTIONAL_CRITIQUE_SCORE_FIELDS = (
    "methods_critique_score",
    "statistical_critique_score",
    "reproducibility_score",
    "interpretation_critique_score",
)

INTERPRETATION_ASSESSMENTS = {
    "supports",
    "neutral",
    "contradicts",
    "inconclusive",
    "untested",
    "untestable",
}


def validate_interpretation(interp: Any) -> list[str]:
    violations: list[str] = []
    if not isinstance(interp, dict):
        return [f"expected object, got {type(interp).__name__}"]

    _require_type(interp, "patterns_observed", list, violations)
    _require_type(interp, "outlier_genes", list, violations)
    _require_type(interp, "regulatory_overlap", dict, violations)

    limitations = interp.get("limitations")
    if not isinstance(limitations, list):
        violations.append("limitations must be a list")
    elif any(not isinstance(item, str) for item in limitations):
        violations.append("limitations must contain only strings")

    assessment = interp.get("overall_genomic_assessment")
    if not isinstance(assessment, str) or not assessment.strip():
        violations.append("overall_genomic_assessment must be a non-empty string")
    elif assessment not in INTERPRETATION_ASSESSMENTS:
        violations.append(
            "overall_genomic_assessment must be one of "
            + ", ".join(sorted(INTERPRETATION_ASSESSMENTS))
        )

    justification = interp.get("assessment_justification")
    if not isinstance(justification, str) or not justification.strip():
        violations.append("assessment_justification must be a non-empty string")

    if "reproducibility_check" in interp and not isinstance(interp["reproducibility_check"], list):
        violations.append("reproducibility_check must be a list when present")

    return violations


def validate_verdict(verdict: Any) -> list[str]:
    violations: list[str] = []
    if not isinstance(verdict, dict):
        return [f"expected object, got {type(verdict).__name__}"]

    verdict_value = verdict.get("verdict")
    if not isinstance(verdict_value, str) or not verdict_value.strip():
        violations.append("verdict must be a non-empty string")

    scores = verdict.get("scores")
    if not isinstance(scores, dict):
        violations.append("scores must be an object")
        return violations

    for field in CORE_SCORE_FIELDS:
        if field not in scores:
            violations.append(f"scores.{field} is required")
            continue
        value = scores[field]
        if field == "genomic_evidence_alignment" and value is None:
            continue
        _validate_score(field, value, violations)

    for field in OPTIONAL_CRITIQUE_SCORE_FIELDS:
        if field in scores:
            _validate_score(field, scores[field], violations)

    return violations


def _require_type(payload: dict, field: str, expected: type, violations: list[str]) -> None:
    if field not in payload:
        violations.append(f"{field} is required")
    elif not isinstance(payload[field], expected):
        violations.append(f"{field} must be a {expected.__name__}")


def _validate_score(field: str, value: Any, violations: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, Real):
        violations.append(f"scores.{field} must be numeric")
        return
    numeric = float(value)
    if not math.isfinite(numeric) or not 1 <= numeric <= 10:
        violations.append(f"scores.{field} must be between 1 and 10")
