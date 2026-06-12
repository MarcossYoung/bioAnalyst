"""Per-gene diagnostic records and Stage-2 false-positive risk scoring.

Stage 2 treats ``result_changes_with_aligner is None`` as "not applicable yet",
not as evidence of alignment stability. The 0.40 aligner-sensitivity term is
therefore skipped until Stage 3 can populate it from primary-test reruns.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
import math
from statistics import median
from typing import Any


RISK_TIER_CONTRIBUTES = "contributes"
RISK_TIER_FLAGGED = "flagged"
RISK_TIER_EXCLUDED = "excluded"

FLAGGED_RISK_FLOOR = 0.25
EXCLUDED_RISK_FLOOR = 0.50

RESULT_CHANGES_WITH_ALIGNER_WEIGHT = 0.40
LOW_ALIGNMENT_CONFIDENCE_WEIGHT = 0.15
RECOMBINATION_WEIGHT = 0.10
SATURATION_WEIGHT = 0.10
GBGC_WEIGHT = 0.10
LOW_POWER_WEIGHT = 0.10
NG86_DIVERGENCE_WEIGHT = 0.05

MAFFT_PRANK_AGREEMENT_FLOOR = 0.80
GUIDANCE2_MEAN_COL_SCORE_FLOOR = 0.80
COLUMNS_MASKED_FRACTION_FLOOR = 0.20
SATURATED_BRANCH_FRACTION_FLOOR = 0.50
SURVIVING_BRANCHES_FLOOR = 5
NG86_DIVERGENCE_FLOOR = 0.50
ALIGNER_RATE_VECTOR_DELTA_FLOOR = 0.20

FP_RISK_WEIGHTS = {
    "result_changes_with_aligner": RESULT_CHANGES_WITH_ALIGNER_WEIGHT,
    "low_alignment_confidence": LOW_ALIGNMENT_CONFIDENCE_WEIGHT,
    "recombination": RECOMBINATION_WEIGHT,
    "saturation": SATURATION_WEIGHT,
    "gbgc": GBGC_WEIGHT,
    "low_power": LOW_POWER_WEIGHT,
    "ng86_divergence": NG86_DIVERGENCE_WEIGHT,
}
FP_RISK_CALIBRATION_STATE = "heuristic"
FP_RISK_DISCLAIMER = "FP-risk weights are a heuristic, not yet calibrated (see Stage 5)."


@dataclass
class AlignmentDiagnostics:
    mafft_prank_agreement: float | None = None
    guidance2_mean_col_score: float | None = None
    columns_masked: int | None = None
    aligned_codons: int | None = None
    result_changes_with_aligner: bool | None = None
    result_changes_with_aligner_note: str = (
        "Not populated until Stage 3; null is not evidence of alignment stability."
    )


@dataclass
class RecombinationDiagnostics:
    gard_breakpoints: int | list | None = None
    action: str | None = "none"


@dataclass
class SaturationDiagnostics:
    median_branch_dS: float | None = None
    saturated_branch_fraction: float | None = None
    surviving_branches: int | None = None


@dataclass
class GBGCDiagnostics:
    gc3_skew: float | None = None
    risk: str | None = None


@dataclass
class PowerDiagnostics:
    taxa_after_gate: int | None = None
    aligned_codons: int | None = None
    tree_length: float | None = None
    usable: bool | None = None
    exclusion_reason: str | None = None


@dataclass
class NG86Crosscheck:
    model_vs_ng86_divergence: float | None = None


@dataclass
class GeneDiagnostics:
    gene: str
    alignment: AlignmentDiagnostics = field(default_factory=AlignmentDiagnostics)
    recombination: RecombinationDiagnostics = field(default_factory=RecombinationDiagnostics)
    saturation: SaturationDiagnostics = field(default_factory=SaturationDiagnostics)
    gbgc: GBGCDiagnostics = field(default_factory=GBGCDiagnostics)
    power: PowerDiagnostics = field(default_factory=PowerDiagnostics)
    ng86_crosscheck: NG86Crosscheck = field(default_factory=NG86Crosscheck)

    def to_dict(self) -> dict:
        return asdict(self)


def diagnostics_to_dict(record: Any) -> dict:
    if record is None:
        return {}
    if is_dataclass(record):
        return asdict(record)
    if isinstance(record, dict):
        return record
    return {}


def _get(record: Any, path: str, default=None):
    cur = record
    for part in path.split("."):
        if cur is None:
            return default
        if is_dataclass(cur):
            cur = getattr(cur, part, default)
        elif isinstance(cur, dict):
            cur = cur.get(part, default)
        else:
            return default
    return cur


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _breakpoint_count(value) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def tier(risk: float | None) -> str:
    value = max(0.0, min(1.0, float(risk or 0.0)))
    if value >= EXCLUDED_RISK_FLOOR:
        return RISK_TIER_EXCLUDED
    if value >= FLAGGED_RISK_FLOOR:
        return RISK_TIER_FLAGGED
    return RISK_TIER_CONTRIBUTES


def fp_risk(d: Any) -> tuple[float, list[str]]:
    """Return the heuristic Stage-2 false-positive risk and named reasons.

    The score is a bounded sum of named terms. ``result_changes_with_aligner``
    contributes only when explicitly true; when null, the weight is skipped
    because Stage 2 has no aligner-rerun primary result to compare.
    """
    risk = 0.0
    reasons: list[str] = []

    aligner_changed = _get(d, "alignment.result_changes_with_aligner")
    if aligner_changed is True:
        risk += RESULT_CHANGES_WITH_ALIGNER_WEIGHT
        reasons.append("result_changes_with_aligner")
    elif aligner_changed is None:
        reasons.append("result_changes_with_aligner_not_assessed_weight_skipped")

    agreement = _as_float(_get(d, "alignment.mafft_prank_agreement"))
    guidance = _as_float(_get(d, "alignment.guidance2_mean_col_score"))
    columns_masked = _as_float(_get(d, "alignment.columns_masked"))
    aligned_codons = _as_float(_get(d, "alignment.aligned_codons") or _get(d, "power.aligned_codons"))
    masked_fraction = (
        columns_masked / aligned_codons
        if columns_masked is not None and aligned_codons and aligned_codons > 0
        else None
    )
    low_alignment = (
        (agreement is not None and agreement < MAFFT_PRANK_AGREEMENT_FLOOR)
        or (guidance is not None and guidance < GUIDANCE2_MEAN_COL_SCORE_FLOOR)
        or (masked_fraction is not None and masked_fraction > COLUMNS_MASKED_FRACTION_FLOOR)
    )
    if low_alignment:
        risk += LOW_ALIGNMENT_CONFIDENCE_WEIGHT
        reasons.append("low_alignment_confidence")

    breakpoints = _breakpoint_count(_get(d, "recombination.gard_breakpoints"))
    action = str(_get(d, "recombination.action", "") or "").lower()
    if breakpoints > 0 or action in {"partition", "exclude", "mask"}:
        risk += RECOMBINATION_WEIGHT
        reasons.append("recombination_detected")

    saturated = _as_float(_get(d, "saturation.saturated_branch_fraction"))
    surviving = _get(d, "saturation.surviving_branches")
    try:
        surviving_n = int(surviving) if surviving is not None else None
    except (TypeError, ValueError):
        surviving_n = None
    if (
        (saturated is not None and saturated > SATURATED_BRANCH_FRACTION_FLOOR)
        or (surviving_n is not None and surviving_n < SURVIVING_BRANCHES_FLOOR)
    ):
        risk += SATURATION_WEIGHT
        reasons.append("dS_saturation_or_too_few_surviving_branches")

    gbgc_risk = str(_get(d, "gbgc.risk", "") or "").lower()
    if gbgc_risk == "high":
        risk += GBGC_WEIGHT
        reasons.append("high_gbgc_risk")

    power_usable = _get(d, "power.usable")
    if power_usable is False:
        risk += LOW_POWER_WEIGHT
        reason = _get(d, "power.exclusion_reason") or "low_power"
        reasons.append(f"low_power:{reason}")

    divergence = _as_float(_get(d, "ng86_crosscheck.model_vs_ng86_divergence"))
    if divergence is not None and divergence >= NG86_DIVERGENCE_FLOOR:
        risk += NG86_DIVERGENCE_WEIGHT
        reasons.append("model_vs_ng86_divergence")

    return max(0.0, min(1.0, round(risk, 6))), reasons


def score_record(record: Any) -> dict:
    risk, reasons = fp_risk(record)
    return {
        "risk": risk,
        "tier": tier(risk),
        "reasons": reasons,
        "calibration_state": FP_RISK_CALIBRATION_STATE,
    }


def _rate_mapping(result: Any) -> dict[str, float]:
    if not isinstance(result, dict):
        return {}
    raw = result.get("rates") if isinstance(result.get("rates"), dict) else result
    out: dict[str, float] = {}
    for key, value in (raw or {}).items():
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out[str(key)] = v
    return out


def assess_aligner_rate_sensitivity(
    mafft_result: Any,
    prank_result: Any,
    *,
    material_delta: float = ALIGNER_RATE_VECTOR_DELTA_FLOOR,
) -> tuple[bool | None, str]:
    """Compare MAFFT/PRANK relative branch-rate vectors.

    Returns ``(None, note)`` when the comparison is not assessable, keeping the
    Stage-2 risk term skipped rather than silently marking the gene stable.
    """
    mafft = _rate_mapping(mafft_result)
    prank = _rate_mapping(prank_result)
    shared = sorted(set(mafft) & set(prank))
    if len(shared) < 3:
        return None, "aligner branch-rate comparison unavailable: fewer than 3 shared branches"
    deltas = [abs(mafft[b] - prank[b]) for b in shared]
    mean_delta = sum(deltas) / len(deltas)
    max_delta = max(deltas)
    changed = mean_delta >= material_delta or max_delta >= material_delta * 2
    return changed, (
        f"MAFFT/PRANK relative branch-rate delta mean={mean_delta:.3f}, "
        f"max={max_delta:.3f}, shared_branches={len(shared)}, threshold={material_delta:.3f}"
    )


def populate_result_changes_with_aligner(
    diagnostics: dict[str, dict],
    aligner_branch_rates: dict[str, dict],
    *,
    material_delta: float = ALIGNER_RATE_VECTOR_DELTA_FLOOR,
) -> dict[str, dict]:
    """Attach Stage-3 aligner sensitivity to existing GeneDiagnostics records."""
    out = {gene: diagnostics_to_dict(record) for gene, record in (diagnostics or {}).items()}
    for gene, pair in (aligner_branch_rates or {}).items():
        record = out.setdefault(gene, GeneDiagnostics(gene=gene).to_dict())
        alignment = record.setdefault("alignment", {})
        changed, note = assess_aligner_rate_sensitivity(
            (pair or {}).get("mafft"),
            (pair or {}).get("prank"),
            material_delta=material_delta,
        )
        alignment["result_changes_with_aligner"] = changed
        alignment["result_changes_with_aligner_note"] = note
    return out


def summarize_set_risk(genes: list[str], diagnostics: dict | None, min_survivors: int) -> dict:
    scored = []
    for gene in genes or []:
        record = (diagnostics or {}).get(gene)
        if record is None:
            continue
        scored.append({"gene": gene, **score_record(record)})
    excluded = [g for g in scored if g["tier"] == RISK_TIER_EXCLUDED]
    flagged = [g for g in scored if g["tier"] == RISK_TIER_FLAGGED]
    survivors = [g for g in scored if g["tier"] != RISK_TIER_EXCLUDED]
    unscored_count = max(0, len(genes or []) - len(scored))
    degraded = bool(scored and len(survivors) < int(min_survivors))
    return {
        "scored_count": len(scored),
        "unscored_count": unscored_count,
        "survivor_count": len(survivors),
        "flagged_count": len(flagged),
        "excluded_count": len(excluded),
        "min_survivors": int(min_survivors),
        "risk_degraded": degraded,
        "reason": "too few genes survive FP-risk filter" if degraded else "",
        "flagged_genes": [g["gene"] for g in flagged],
        "excluded_genes": [g["gene"] for g in excluded],
    }


def run_diagnostics(gene_data: dict, panel: list[str] | None = None, on_event=None) -> dict[str, dict]:
    """Cheap Stage-2-compatible diagnostics fallback.

    The full Stage-1 container layer is not present in this checkout. This
    fallback records what the current NG86 floor can already observe and leaves
    container-only fields null with named semantics.
    """
    out: dict[str, dict] = {}
    panel_set = {str(s).lower() for s in (panel or [])}
    for gene, record in (gene_data or {}).items():
        if not isinstance(record, dict) or "_error" in record:
            out[gene] = GeneDiagnostics(
                gene=gene,
                power=PowerDiagnostics(usable=False, exclusion_reason=(record or {}).get("_error", "missing")),
            ).to_dict()
            continue
        dnds_vals = [
            float(o["dnds"])
            for o in (record.get("orthologs") or [])
            if o.get("dnds") is not None and float(o["dnds"]) < 10
        ]
        saturated_fraction = (
            sum(1 for v in dnds_vals if abs(v - 1.0) < 0.01) / len(dnds_vals)
            if dnds_vals else None
        )
        one2one_species = {
            str(o.get("target_species") or "").lower()
            for o in (record.get("orthologs") or [])
            if "one2one" in str(o.get("ortholog_type") or "").lower()
        }
        if panel_set:
            one2one_species &= panel_set
        taxa = len(one2one_species)
        usable = taxa >= SURVIVING_BRANCHES_FLOOR and bool(dnds_vals)
        out[gene] = GeneDiagnostics(
            gene=gene,
            saturation=SaturationDiagnostics(
                median_branch_dS=median(dnds_vals) if dnds_vals else None,
                saturated_branch_fraction=saturated_fraction,
                surviving_branches=len(dnds_vals),
            ),
            power=PowerDiagnostics(
                taxa_after_gate=taxa,
                usable=usable,
                exclusion_reason="" if usable else "too_few_taxa",
            ),
        ).to_dict()
    return out
