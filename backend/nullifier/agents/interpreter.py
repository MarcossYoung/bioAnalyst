from statistics import mean

from ..tools.llm_client import llm_call_json
from ..tools.diagnostics import fp_risk_settings
from ..tools.phenotypes import ASSOCIATION_ONLY_GUARD
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject


INTERPRETER_SPEC = AgentSpec(
    name="interpreter",
    mission="Translate typed compute results and genomic measurements into a calibrated narrative without inventing numbers.",
    capabilities=(
        "Summarize statistical test results (effect sizes, p-values, multiple-testing corrections) from the Compute layer.",
        "Summarize observed genomic patterns from raw per-gene data.",
        "Flag outlier genes and regulatory overlap.",
        "Cross-reference reproducibility data when present.",
    ),
    behavioral_constraints=(
        "Do not invent numbers.",
        "Do not override typed compute results; treat them as a read-only ground truth.",
        "Interpret every available test using its typed effect_size, effect_size_label, ci_lower, and ci_upper fields; when those fields are null, state that the typed result does not provide them.",
        "Return JSON only.",
    ),
    verification_rules=(
        "Every numeric claim must trace back to a value in the inputs.",
        "For each available computed test, cite the typed effect_size/effect_size_label fields and ci_lower/ci_upper fields when interpreting the result.",
        "If an available computed test has null effect-size or confidence-interval fields, state that the typed result does not provide them instead of inventing them.",
        "Pairwise dN/dS is computed from Ensembl homology protein alignments threaded onto transcript CDS with a conservative NG86 estimator; describe missing values as coverage limitations, not evidence against the hypothesis.",
        "When paml_branch_model or omega metrics are present, interpret omega_foreground, omega_background, acceleration_ratio, and LRT p-values as branch-model PAML results.",
        "For PAML limitations, state that genes without sufficient alignment depth or successful codeml runs were excluded.",
        "Regulatory overlap is Jaccard-style and not statistically normalized.",
        "The tool is observational, not a phylogenetic comparative method.",
        "When rerconverge is present, present it as secondary to ERC, surface underpowered/primate_confounded flags, and never claim causal co-evolution.",
        "Omit reproducibility_check (or return []) when no reproducibility section is present in the input.",
        "Every outlier_genes item must include non-empty gene, why_notable, and implication strings; omit the item if you cannot explain it.",
    ),
    output_contract=OutputContract(
        summary="Calibrated genomic interpretation.",
        fields=(
            OutputField("patterns_observed", "Observed patterns with support polarity and evidence."),
            OutputField("outlier_genes", "Genes that stand out; each item requires gene, why_notable, and implication."),
            OutputField("regulatory_overlap", "Shared TF motifs, Jaccard index, and interpretation."),
            OutputField("reproducibility_check", "Cross-reference of reported findings against Ensembl values.", required=False),
            OutputField("limitations", "Explicit limitations of the analysis."),
            OutputField("overall_genomic_assessment", "supports, neutral, contradicts, or inconclusive."),
            OutputField("assessment_justification", "Short justification for the overall assessment."),
        ),
    ),
)

INTERPRETER_SYSTEM = INTERPRETER_SPEC.render_system_prompt()


def run_interpreter(
    formalized: dict,
    expansion: dict,
    compute_results: dict,
    gene_data: dict,
    robustness: dict | None = None,
    reproducibility: dict | None = None,
) -> dict:
    risk_disclaimer = fp_risk_settings()["disclaimer"]
    if compute_results.get("untestable"):
        reason = compute_results.get("untestable_reason") or "No compatible compute method for the claim construct."
        return {
            "patterns_observed": [],
            "outlier_genes": [],
            "regulatory_overlap": {},
            "reproducibility_check": [],
            "limitations": [reason, risk_disclaimer],
            "overall_genomic_assessment": "untestable",
            "assessment_justification": reason,
            "required_construct": compute_results.get("required_construct"),
        }
    user = _build_user_prompt(formalized, expansion, compute_results, gene_data, robustness, reproducibility)
    out = llm_call_json("interpreter", INTERPRETER_SYSTEM, user, max_tokens=3500)
    if not isinstance(out, dict):
        limitations = [
            f"Interpreter returned an invalid root type ({type(out).__name__}); expected a JSON object.",
            risk_disclaimer,
        ]
        if _has_rerconverge(compute_results):
            limitations.append(ASSOCIATION_ONLY_GUARD)
        return {
            "patterns_observed": [],
            "outlier_genes": [],
            "regulatory_overlap": {},
            "reproducibility_check": [],
            "limitations": limitations,
            "overall_genomic_assessment": "inconclusive",
            "assessment_justification": "The interpreter response did not satisfy its JSON object contract.",
        }

    limitations = list(out.get("limitations") or [])
    if risk_disclaimer not in limitations:
        limitations.append(risk_disclaimer)
    if _has_rerconverge(compute_results) and ASSOCIATION_ONLY_GUARD not in limitations:
        limitations.append(ASSOCIATION_ONLY_GUARD)
    out["limitations"] = limitations
    return out


def _build_user_prompt(
    formalized: dict,
    expansion: dict,
    compute_results: dict,
    gene_data: dict,
    robustness: dict | None,
    reproducibility: dict | None,
) -> str:
    risk_disclaimer = fp_risk_settings()["disclaimer"]
    tests = compute_results.get("tests") or []
    test_lines = []
    for t in tests:
        if not t.get("available", True):
            test_lines.append(
                f"  - {t.get('requested', '?')}: NOT AVAILABLE "
                f"({t.get('skip_reason') or t.get('error') or t.get('closest_alternative', '')})"
            )
            continue
        bits = [t.get("test", "?")]
        for k in (
            "n",
            "statistic",
            "p_value",
            "p_value_adjusted",
            "significant",
            "significant_adjusted",
            "effect_size",
            "effect_size_name",
            "effect_size_label",
            "ci",
            "ci_lower",
            "ci_upper",
            "method",
        ):
            if k in t:
                bits.append(f"{k}={t[k]}")
        line = "  - " + ", ".join(bits)
        if t.get("rationale"):
            line += f"  // {t['rationale']}"
        test_lines.append(line)

    corr_lines = []
    for c in compute_results.get("corrections_applied") or []:
        corr_lines.append(f"  - {c.get('method')} (n_tests={c.get('n_tests')}, alpha={c.get('alpha')})")

    rb_block = ""
    if robustness and robustness.get("applicable"):
        rb_block = (
            f"\nROBUSTNESS (leave-one-out on starter genes):\n"
            f"  stability: {robustness.get('stability')}  "
            f"agreement_fraction: {robustness.get('agreement_fraction')}\n"
            f"  most_influential_genes: {robustness.get('most_influential_genes', [])}\n"
        )
    elif robustness and not robustness.get("applicable"):
        rb_block = f"\nROBUSTNESS: not applicable ({robustness.get('reason', '')})\n"

    repro_block = ""
    if reproducibility:
        repro_block = "\nREPRODUCIBILITY_CHECK (the tool's deterministic cross-reference):\n"
        for c in reproducibility.get("checks") or []:
            repro_block += (
                f"  - reported: {c.get('reported')}\n"
                f"    classification: {c.get('classification')}\n"
                f"    note: {c.get('note')}\n"
            )
        nv = reproducibility.get("not_verifiable_here") or []
        if nv:
            repro_block += "CANNOT be verified from Ensembl gene records here:\n"
            for line in nv:
                repro_block += f"  - {line}\n"

    per_gene_lines = []
    for g, d in (gene_data or {}).items():
        if not isinstance(d, dict):
            continue
        if "_error" in d:
            per_gene_lines.append(f"  {g}: NOT FOUND ({d['_error']})")
            continue
        orthologs = d.get("orthologs") or []
        paralogs = d.get("paralogs") or []
        tree = d.get("gene_tree") or {}
        reg = d.get("regulatory_features") or []
        dnds_vals = [o["dnds"] for o in orthologs if o.get("dnds") is not None and o["dnds"] < 10]
        dnds_mean = f"{mean(dnds_vals):.3f}" if dnds_vals else "n/a (no usable pairwise dN/dS from homology pal2nal + NG86 for returned orthologs)"
        per_gene_lines.append(
            f"  {g}: orthologs={len(orthologs)}, paralogs={len(paralogs)}, "
            f"duplications={tree.get('duplication_count', 0)}, "
            f"regulatory_features={len(reg)}, mean_dN/dS={dnds_mean}"
        )

    risk_filter = (((compute_results or {}).get("data_summary") or {}).get("rate_vectors") or {}).get("risk_filter") or {}
    risk_block = ""
    if risk_filter:
        risk_block = (
            "\nFP-RISK FILTER:\n"
            f"  calibration_state: {risk_filter.get('calibration_state', 'heuristic')}\n"
            f"  disclaimer: {risk_disclaimer}\n"
            f"  flagged_genes: {risk_filter.get('flagged_genes', [])}\n"
            f"  excluded_genes: {risk_filter.get('excluded_genes', [])}\n"
            f"  set_survival: {risk_filter.get('sets', {})}\n"
        )

    rer_block = ""
    rer_tests = [t for t in tests if t.get("test") == "rerconverge"]
    if rer_tests:
        rer_block = "\nRERCONVERGE SECONDARY ASSOCIATION GUARD:\n"
        rer_block += f"  guard: {ASSOCIATION_ONLY_GUARD}\n"
        for t in rer_tests:
            details = t.get("details") or {}
            rer_block += (
                f"  - available={t.get('available')} underpowered={t.get('underpowered', details.get('underpowered'))} "
                f"primate_confounded={t.get('primate_confounded', details.get('primate_confounded'))} "
                f"secondary_to={details.get('secondary_to') or details.get('primary_test') or 'erc'}\n"
            )

    evidence_parts = [
        "DETERMINISTIC COMPUTE RESULTS:\n" + ("\n".join(test_lines) or "  (none)"),
        "CORRECTIONS APPLIED:\n" + ("\n".join(corr_lines) or "  (none)"),
    ]
    if compute_results.get("untestable"):
        evidence_parts.append(
            "CONSTRUCT GATE:\n"
            f"  required_construct: {compute_results.get('required_construct')}\n"
            f"  reason: {compute_results.get('untestable_reason')}"
        )
    if rb_block.strip():
        evidence_parts.append(rb_block.strip())
    if repro_block.strip():
        evidence_parts.append(repro_block.strip())
    if risk_block.strip():
        evidence_parts.append(risk_block.strip())
    if rer_block.strip():
        evidence_parts.append(rer_block.strip())
    evidence_parts.append(
        "PER-GENE GENOMIC DATA:\n" + ("\n".join(per_gene_lines) or "  (no gene data)")
    )

    task = TaskObject(
        title="Interpret typed compute results",
        semantic_inputs={"hypothesis": formalized.get("core_hypothesis", "")},
        entities=tuple(expansion.get("starter") or []),
        evidence=tuple(evidence_parts),
        contextual_state={
            "expanded_sets": list((expansion.get("expanded") or {}).keys()),
            "control_sets": list((expansion.get("controls") or {}).keys()),
        },
        expected_outputs=(
            "patterns_observed",
            "outlier_genes",
            "regulatory_overlap",
            "reproducibility_check",
            "limitations",
            "overall_genomic_assessment",
            "assessment_justification",
        ),
    )
    return task.render()


def _has_rerconverge(compute_results: dict) -> bool:
    return any((t or {}).get("test") == "rerconverge" for t in (compute_results or {}).get("tests") or [])
