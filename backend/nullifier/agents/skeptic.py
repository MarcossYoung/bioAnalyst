from ..tools.llm_client import llm_call_json
from ..tools.diagnostics import fp_risk_settings
from ..tools.phenotypes import ASSOCIATION_ONLY_GUARD
from .semantic import (
    AgentSpec,
    OutputContract,
    OutputField,
    TaskObject,
    normalize_atomic_claim,
    normalize_cited_reference,
)


SKEPTIC_SPEC = AgentSpec(
    name="scientific skeptic",
    mission="Stress-test a hypothesis, score its support, and surface the most serious alternative explanations and execution risks.",
    capabilities=(
        "Identify alternative explanations.",
        "Score evidence across statistical, literature, mechanistic, novelty, and genomic dimensions.",
        "Propose a single decisive experiment.",
    ),
    behavioral_constraints=(
        "Do not treat novelty as weakness by itself.",
        "Do not invent evidence not present in the inputs.",
        "Return JSON only.",
    ),
    verification_rules=(
        "If the analysis includes completed results, critique execution rather than just the idea.",
        "If high-severity statistical or methodological issues are present, the verdict must become RESULTS-PROBLEMATIC.",
    ),
    output_contract=OutputContract(
        summary="Final skeptical verdict and supporting breakdown.",
        fields=(
            OutputField("top_alternative_explanations", "Top competing explanations."),
            OutputField("scores", "Score breakdown across the evidence dimensions."),
            OutputField("verdict", "STRONG, MODERATE, WEAK, FALSIFIED, NOVEL-UNTESTED, or RESULTS-PROBLEMATIC."),
            OutputField("verdict_justification", "Short justification for the verdict."),
            OutputField("decisive_experiment", "Single most decisive experiment or analysis."),
            OutputField("librarian_sanity_check", "Brief note on paper-classification sanity."),
        ),
    ),
)

SKEPTIC_SYSTEM_BASE = f"""{SKEPTIC_SPEC.render_system_prompt()}

You see the hypothesis, atomic claims, the Librarian's evidence assessment, and the raw top abstracts.
Use the raw abstracts to independently sanity-check the Librarian's classifications before scoring.

Alternative explanations to consider at minimum:
- Allometric scaling
- Shared upstream cause
- Selection bias
- Simpler mechanistic story

Scores are from 1 (falsified) to 10 (strongly supported):
- statistical_robustness
- literature_consensus
- mechanistic_plausibility
- counter_explanation_risk
- novelty_adjusted_confidence
- genomic_evidence_alignment
- overall_falsifiability_score

Use NOVEL-UNTESTED when novelty_flag is unstudied across claims.
If classifier_degraded is true, do not treat empty classifications as a confirmed literature void.
If no genomic test ran or genomic evidence is marked untestable, report genomic_evidence_alignment as N/A; do not score it.
If RERconverge is present, treat it as secondary association evidence only; it cannot override ERC/mirrortree-lite and must not be framed as causal co-evolution.
Propose the single most decisive experiment or analysis."""

SKEPTIC_DNDS_LIMITATION = (
    "Known data limitation: pairwise dN/dS is computed from Ensembl homology protein alignments "
    "threaded onto transcript CDS with a conservative NG86 estimator. Missing pairwise dN/dS is "
    "a coverage/quality limitation and should not lower the methodological score by itself. "
    "When PAML branch-model omega is available, use it when considering evolutionary acceleration alternatives."
)


SKEPTIC_CRITIQUE_BLOCK = """

ADDITIONAL TASK - CRITIQUE THE COMPLETED ANALYSIS:
You are also given METHODS USED and COMPLETED ANALYSIS - work the author has already done.
Critique the execution, not just the idea. Be specific and cite the reported numbers.

Evaluate, at minimum:
  - Sample size adequacy
  - Multiple-testing correction
  - Phylogenetic non-independence
  - Test appropriateness
  - Effect size vs p-value
  - Interpretation overreach

Add these four sub-objects to your JSON:
  "methods_critique"
  "statistical_critique"
  "reproducibility_check"
  "interpretation_critique"

Add four scores to "scores":
  "methods_critique_score"
  "statistical_critique_score"
  "reproducibility_score"
  "interpretation_critique_score"

If the completed analysis has high-severity methodological or statistical problems, the verdict must be
"RESULTS-PROBLEMATIC".
"""


def stress_test(formalized: dict, evidence: dict, analyst_result: dict | None = None) -> dict:
    claims_and_evidence = []
    for idx, raw_claim in enumerate(formalized.get("atomic_claims", []) or []):
        claim = normalize_atomic_claim(raw_claim, idx)
        cid = claim["id"]
        assessment = evidence["claim_evidence"].get(cid, {})

        top_abstracts = []
        for p in assessment.get("retrieved_papers", [])[:3]:
            top_abstracts.append(f"  [{p.get('year', '?')}] {p['title']}\n  {p['abstract'][:500]}")

        claims_and_evidence.append(
            f"[{cid}] {claim['statement']}\n"
            f"  H0: {claim['null_hypothesis']}\n"
            f"  Librarian: strength={assessment.get('evidence_strength', '?')}, "
            f"novelty={assessment.get('novelty_flag', '?')}\n"
            f"  Classifier degraded: {assessment.get('classifier_degraded', False)} "
            f"summary={assessment.get('classification_summary', {})}\n"
            f"  Confounders: {assessment.get('confounders_identified', '')}\n"
            f"  Top retrieved abstracts for your sanity-check:\n" + "\n".join(top_abstracts)
        )

    analyst_section = _format_analyst_for_skeptic(analyst_result)

    completed_analysis = formalized.get("completed_analysis") or []
    methods_used = formalized.get("methods_used") or []
    critique_active = bool(completed_analysis)
    critique_section = _format_completed_analysis(methods_used, completed_analysis, analyst_result) if critique_active else ""

    system = SKEPTIC_SYSTEM_BASE + "\n\n" + SKEPTIC_DNDS_LIMITATION + (
        SKEPTIC_CRITIQUE_BLOCK if critique_active else ""
    )

    task = TaskObject(
        title="Final skeptical review",
        semantic_inputs={"hypothesis": formalized.get("core_hypothesis", "")},
        entities=tuple(formalized.get("starter_entities", []) or []),
        contextual_state={"domain": formalized.get("domain", "unknown")},
        expected_outputs=(
            "top_alternative_explanations",
            "scores",
            "verdict",
            "verdict_justification",
            "decisive_experiment",
            "librarian_sanity_check",
        ),
    )

    cited_refs = [normalize_cited_reference(r.get("user_reference", r)) for r in evidence.get("cited_literature_validated", [])]

    user_msg = f"""{task.render()}

CORE HYPOTHESIS:
{formalized['core_hypothesis']}

DOMAIN: {formalized.get('domain', 'unknown')}

CLASSIFIER DEGRADED: {bool(evidence.get('classifier_degraded'))}

USER-CITED LITERATURE:
{chr(10).join(f"- {r['title_or_description']}" for r in cited_refs)}

CLAIMS + EVIDENCE + TOP ABSTRACTS:
{chr(10).join(claims_and_evidence)}
{analyst_section}{critique_section}"""
    verdict = llm_call_json("skeptic", system, user_msg, max_tokens=3500)
    return _apply_guardrails(verdict, evidence, analyst_result)


def _apply_guardrails(
    verdict: dict,
    evidence: dict,
    analyst_result: dict | None,
    config: dict | None = None,
) -> dict:
    return _apply_guardrails_with_config(verdict, evidence, analyst_result, config)


def _apply_guardrails_with_config(
    verdict: dict,
    evidence: dict,
    analyst_result: dict | None,
    config: dict | None = None,
) -> dict:
    if not isinstance(verdict, dict):
        return verdict
    out = dict(verdict)
    scores = dict(out.get("scores") or {})
    analyst_compute = (analyst_result or {}).get("compute_results") or {}
    analyst_interp = (analyst_result or {}).get("interpretation") or {}
    tests = analyst_compute.get("tests") or []
    dnds_saturation = ((analyst_result or {}).get("dnds_saturation") or {})
    risk_gate_active = any(
        bool((s or {}).get("risk_degraded"))
        for s in ((dnds_saturation.get("sets") or {}).values())
    )
    genomic_test_ran = any(
        isinstance(t, dict)
        and t.get("test") != "untestable"
        and not t.get("skipped")
        and t.get("available", True)
        and not t.get("error")
        for t in tests
    )
    genomic_not_scored = (
        not genomic_test_ran
        or not analyst_result
        or analyst_compute.get("untestable")
        or analyst_interp.get("overall_genomic_assessment") == "untestable"
        or dnds_saturation.get("flag")
    )
    axis_promoted = _genomic_axis_promoted(config)
    if genomic_not_scored:
        scores["genomic_evidence_alignment"] = None
        out["scores"] = scores
        if risk_gate_active:
            note = "Risk filter left too few scorable genes; genomic axis not scored."
        elif dnds_saturation.get("flag"):
            note = "dN/dS saturation or coverage degradation made genomic evidence untestable; genomic axis not scored."
        else:
            note = "No genomic test was run; genomic axis not scored."
        out["verdict_justification"] = _append_note(out.get("verdict_justification", ""), note)
    elif not axis_promoted:
        scores["genomic_evidence_alignment"] = None
        out["scores"] = scores
        out["verdict_justification"] = _append_note(
            out.get("verdict_justification", ""),
            "Genomic axis remains advisory pending Stage 5 promotion; genomic axis not scored.",
        )
    if evidence.get("classifier_degraded"):
        out["librarian_sanity_check"] = _append_note(
            out.get("librarian_sanity_check", ""),
            "Classifier degraded: empty classifications are a tool failure signal, not confirmed literature absence.",
        )
    return out


def _genomic_axis_promoted(config: dict | None = None) -> bool:
    if config is None:
        try:
            from ..config.loader import load_config

            config = load_config()
        except Exception:
            config = {}
    return bool(((config or {}).get("genomics") or {}).get("axis_promoted", False))


def _append_note(text: str, note: str) -> str:
    text = str(text or "").strip()
    if note in text:
        return text
    return f"{text} {note}".strip()


def _format_completed_analysis(methods_used: list[str], completed: list[dict], analyst_result: dict | None) -> str:
    lines = ["\n\nMETHODS USED (already run by the author):"]
    if methods_used:
        lines += [f"  - {m}" for m in methods_used]
    else:
        lines.append("  (not explicitly listed)")

    lines.append("\nCOMPLETED ANALYSIS - reported findings (critique these):")
    for i, f in enumerate(completed, 1):
        lines.append(
            f"  {i}. {f.get('finding', '')}"
            + (f"  [statistic: {f['statistic']}]" if f.get("statistic") else "")
            + (f"  [test: {f['test']}]" if f.get("test") else "")
            + (f"  [n: {f['sample_size']}]" if f.get("sample_size") else "")
        )
        if f.get("interpretation"):
            lines.append(f"     author's interpretation: {f['interpretation']}")

    repro = (analyst_result or {}).get("reproducibility") if analyst_result else None
    if repro:
        lines.append("\nANALYST REPRODUCIBILITY DATA (Ensembl-derived values available for cross-reference):")
        for gene, metrics in (repro.get("ensembl_retrievable") or {}).items():
            lines.append(f"  {gene}: {metrics}")
        not_verifiable = repro.get("not_verifiable_here") or []
        if not_verifiable:
            lines.append("  NOT verifiable from Ensembl here: " + "; ".join(not_verifiable))

    return "\n".join(lines)


def _format_analyst_for_skeptic(analyst_result: dict | None) -> str:
    if not analyst_result or analyst_result.get("skipped"):
        return "\nGENOMIC EVIDENCE: Not available - report genomic_evidence_alignment as N/A; do not score."

    interp = analyst_result.get("interpretation", {})
    if (
        interp.get("overall_genomic_assessment") == "untestable"
        or (analyst_result.get("compute_results") or {}).get("untestable")
        or (analyst_result.get("dnds_saturation") or {}).get("flag")
    ):
        reason = interp.get("assessment_justification") or (analyst_result.get("compute_results") or {}).get("untestable_reason", "")
        if not reason:
            reason = (analyst_result.get("dnds_saturation") or {}).get("reason", "")
        if any(
            bool((s or {}).get("risk_degraded"))
            for s in (((analyst_result.get("dnds_saturation") or {}).get("sets") or {}).values())
        ):
            reason = "risk filter left too few scorable genes"
        return (
            "\nGENOMIC EVIDENCE: Untestable/low-confidence by guardrail - "
            "report genomic_evidence_alignment as N/A; do not score.\n"
            f"  Required construct: {interp.get('required_construct') or (analyst_result.get('compute_results') or {}).get('required_construct')}\n"
            f"  Reason: {reason}"
        )
    set_a_stats = analyst_result.get("set_a_stats") or {}
    set_b_stats = analyst_result.get("set_b_stats") or {}
    cross_set = analyst_result.get("cross_set") or {}

    lines = ["\nGENOMIC EVIDENCE (Analyst):"]
    lines.append(f"  {SKEPTIC_DNDS_LIMITATION}")
    lines.append(f"  {fp_risk_settings()['disclaimer']}")
    lines.append(f"  {ASSOCIATION_ONLY_GUARD}")
    lines.append(f"  Overall genomic assessment: {interp.get('overall_genomic_assessment', '?')}")
    lines.append(f"  Justification: {interp.get('assessment_justification', '')}")
    rer_tests = [
        t for t in ((analyst_result.get("compute_results") or {}).get("tests") or [])
        if t.get("test") == "rerconverge"
    ]
    for test in rer_tests:
        details = test.get("details") or {}
        lines.append(
            "  RERconverge secondary: "
            f"available={test.get('available')}, "
            f"underpowered={test.get('underpowered', details.get('underpowered'))}, "
            f"primate_confounded={test.get('primate_confounded', details.get('primate_confounded'))}; "
            "ERC/mirrortree-lite remains the primary comparative test when the genomic axis is promoted."
        )

    if set_a_stats.get("valid_gene_count"):
        dnds = set_a_stats.get("dnds_mean")
        dnds_str = f"{dnds:.3f}" if dnds is not None else "n/a"
        lines.append(
            f"  Set A ({set_a_stats['valid_gene_count']} genes): "
            f"mean_dN/dS={dnds_str}, "
            f"mean_orthologs={set_a_stats.get('mean_ortholog_count', 0):.1f}"
        )
    if set_b_stats.get("valid_gene_count"):
        dnds = set_b_stats.get("dnds_mean")
        dnds_str = f"{dnds:.3f}" if dnds is not None else "n/a"
        lines.append(
            f"  Set B ({set_b_stats['valid_gene_count']} genes): "
            f"mean_dN/dS={dnds_str}, "
            f"mean_orthologs={set_b_stats.get('mean_ortholog_count', 0):.1f}"
        )
    if cross_set:
        lines.append(f"  Regulatory overlap (Jaccard): {cross_set.get('jaccard_index', 0):.3f}")
        shared = cross_set.get("shared_tfs", [])
        if shared:
            lines.append(f"  Shared TF motifs: {shared[:5]}")

    outliers = interp.get("outlier_genes", [])
    if outliers:
        lines.append(f"  Outlier genes: {[o['gene'] for o in outliers]}")

    limitations = interp.get("limitations", [])
    if limitations:
        lines.append(f"  Analyst limitations: {limitations[:2]}")

    return "\n".join(lines)
