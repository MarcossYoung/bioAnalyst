from statistics import mean

from ..tools.llm_client import llm_call_json
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject


INTERPRETER_SPEC = AgentSpec(
    name="interpreter",
    mission="Translate typed compute results and genomic measurements into a calibrated narrative without inventing numbers.",
    capabilities=(
        "Summarize observed genomic patterns from the compute layer and raw gene data.",
        "Flag outlier genes and regulatory overlap.",
        "Cross-reference reproducibility data when present.",
    ),
    behavioral_constraints=(
        "Do not invent numbers.",
        "Do not override typed compute results.",
        "Return JSON only.",
    ),
    verification_rules=(
        "Every numeric claim must trace back to the inputs.",
        "If reproducibility data is present, report what is and is not checkable here.",
    ),
    output_contract=OutputContract(
        summary="Calibrated genomic interpretation.",
        fields=(
            OutputField("patterns_observed", "Observed patterns with support polarity and evidence."),
            OutputField("outlier_genes", "Genes that stand out and why."),
            OutputField("regulatory_overlap", "Shared TF motifs, Jaccard index, and interpretation."),
            OutputField("reproducibility_check", "Cross-reference of reported findings against Ensembl values.", required=False),
            OutputField("limitations", "Explicit limitations of the analysis."),
            OutputField("overall_genomic_assessment", "supports, neutral, contradicts, or inconclusive."),
            OutputField("assessment_justification", "Short justification for the overall assessment."),
        ),
    ),
)

INTERPRETER_SYSTEM = f"""{INTERPRETER_SPEC.render_system_prompt()}

You read structured outputs from a deterministic Compute layer (statistical test results with
effect sizes, p-values, multiple-testing corrections), together with raw per-gene genomic data
and the original hypothesis.

You cannot invent numbers. Every claim about a number must trace to a value in the inputs.

Limitations to state explicitly:
- dN/dS is pairwise human-vs-X, not branch-specific.
- Regulatory overlap is Jaccard-style and not statistically normalized.
- The tool is observational, not a phylogenetic comparative method.

Omit reproducibility_check - or return [] - when no reproducibility section is present."""


def run_interpreter(
    formalized: dict,
    expansion: dict,
    compute_results: dict,
    gene_data: dict,
    robustness: dict | None = None,
    reproducibility: dict | None = None,
) -> dict:
    user = _build_user_prompt(formalized, expansion, compute_results, gene_data, robustness, reproducibility)
    return llm_call_json("interpreter", INTERPRETER_SYSTEM, user, max_tokens=3500)


def _build_user_prompt(
    formalized: dict,
    expansion: dict,
    compute_results: dict,
    gene_data: dict,
    robustness: dict | None,
    reproducibility: dict | None,
) -> str:
    tests = compute_results.get("tests") or []
    test_lines = []
    for t in tests:
        if not t.get("available", True):
            test_lines.append(
                f"  - {t.get('requested', '?')}: NOT AVAILABLE "
                f"({t.get('closest_alternative', '')})"
            )
            continue
        bits = [t.get("test", "?")]
        for k in ("statistic", "p_value", "p_value_adjusted", "effect_size", "ci", "significant", "significant_adjusted"):
            if k in t and t[k] is not None:
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
        dnds_mean = f"{mean(dnds_vals):.3f}" if dnds_vals else "n/a"
        per_gene_lines.append(
            f"  {g}: orthologs={len(orthologs)}, paralogs={len(paralogs)}, "
            f"duplications={tree.get('duplication_count', 0)}, "
            f"regulatory_features={len(reg)}, mean_dN/dS={dnds_mean}"
        )

    task = TaskObject(
        title="Interpret typed compute results",
        semantic_inputs={"hypothesis": formalized.get("core_hypothesis", "")},
        entities=tuple(expansion.get("starter") or []),
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

    return f"""{task.render()}

DETERMINISTIC COMPUTE RESULTS (typed; you cannot invent or override these):
tests:
{chr(10).join(test_lines) or '  (none)'}
corrections_applied:
{chr(10).join(corr_lines) or '  (none)'}
{rb_block}{repro_block}
PER-GENE GENOMIC DATA SUMMARY:
{chr(10).join(per_gene_lines) or '  (no gene data)'}
"""
