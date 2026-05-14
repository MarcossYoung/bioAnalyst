from ..tools.llm_client import llm_call_json

SKEPTIC_SYSTEM_BASE = """You are a scientific skeptic. Stress-test a hypothesis by finding
its weakest points. You see the hypothesis, atomic claims, the Librarian's evidence
assessment, AND the raw top abstracts. Use the raw abstracts to independently sanity-check
the Librarian's classifications before scoring.

1. Identify the TOP 3 alternative explanations. MUST consider at minimum:
   - Allometric scaling: "is the apparent correlation just a function of a third
     variable like system size/complexity?"
   - Shared upstream cause: "is there a third factor that drives both A and B
     independently, making their correlation non-causal?"
   - Selection bias: "does the user's cited literature represent a biased sample?"
   - Simpler mechanistic story: "is there a simpler mechanism that explains the same
     observations without invoking the hypothesis?"

2. Score each from 1 (falsified) to 10 (strongly supported):
   - statistical_robustness
   - literature_consensus
   - mechanistic_plausibility
   - counter_explanation_risk (INVERTED: 10 = survives alternatives well)
   - novelty_adjusted_confidence (penalize confidence when novelty_flag is 'unstudied' —
     don't score a hypothesis WEAK just because no one has studied it; score it UNTESTED)
   - genomic_evidence_alignment (how well genomic patterns — dN/dS, ortholog conservation,
     regulatory overlap — align with the hypothesis; score 5 (neutral) if no genomic data available)
   - overall_falsifiability_score

3. Verdict: STRONG | MODERATE | WEAK | FALSIFIED | NOVEL-UNTESTED
   Use NOVEL-UNTESTED when novelty_flag is "unstudied" across claims — the hypothesis
   is not supported OR refuted by existing literature; it needs primary investigation.

4. Propose the SINGLE most decisive experiment/analysis.

Respond with ONLY valid JSON:
{
  "top_alternative_explanations": [
    {
      "explanation": "...",
      "plausibility": "high|medium|low",
      "why": "...",
      "how_to_rule_out": "..."
    }
  ],
  "scores": {
    "statistical_robustness": 0,
    "literature_consensus": 0,
    "mechanistic_plausibility": 0,
    "counter_explanation_risk": 0,
    "novelty_adjusted_confidence": 0,
    "genomic_evidence_alignment": 0,
    "overall_falsifiability_score": 0
  },
  "verdict": "STRONG|MODERATE|WEAK|FALSIFIED|NOVEL-UNTESTED",
  "verdict_justification": "2-3 sentences",
  "decisive_experiment": "specific method + dataset + expected outcome under H1 vs H0",
  "librarian_sanity_check": "brief note on whether Librarian classifications seem correct based on abstracts you saw"
}"""


# Appended when the author has already run analyses and reported results.
SKEPTIC_CRITIQUE_BLOCK = """

ADDITIONAL TASK — CRITIQUE THE COMPLETED ANALYSIS:
You are also given METHODS USED and COMPLETED ANALYSIS — work the author has already done.
Critique the EXECUTION, not just the idea. Be specific and cite the reported numbers.

Evaluate, at minimum:
  - Sample size adequacy (e.g. a Spearman correlation on n=4 is not interpretable)
  - Multiple-testing correction (were many comparisons run without adjusting alpha?)
  - Phylogenetic non-independence (cross-species comparisons treated as independent data points?)
  - Test appropriateness (right test for the data type / distribution / design?)
  - Effect size vs p-value (is significance conflated with magnitude / importance?)
  - Interpretation overreach (does the stated conclusion exceed what the data can bear?)

Add these FOUR sub-objects to your JSON (each: {"severity": "high|medium|low", "issues": ["..."], "notes": "..."}):
  "methods_critique"        — design / method appropriateness and confounds
  "statistical_critique"    — sample size, multiple testing, test choice, effect size vs p
  "reproducibility_check"   — which reported numbers are independently checkable and against what
                              (reconcile with the Analyst's reproducibility data if provided);
                              be honest about what CANNOT be verified here
  "interpretation_critique" — overreach, causal language on correlational data, generalization

Add FOUR scores to "scores" (1 = severe problems, 10 = clean execution):
  "methods_critique_score", "statistical_critique_score", "reproducibility_score", "interpretation_critique_score"

VERDICT OVERRIDE: if the completed analysis has HIGH-severity methodological or statistical problems,
the verdict MUST be "RESULTS-PROBLEMATIC". This takes precedence over NOVEL-UNTESTED and over any
literature-based verdict — fixing the analysis comes before the novelty question matters.
"""


def stress_test(formalized: dict, evidence: dict, analyst_result: dict | None = None) -> dict:
    claims_and_evidence = []
    for claim in formalized["atomic_claims"]:
        cid = claim["id"]
        assessment = evidence["claim_evidence"].get(cid, {})

        top_abstracts = []
        for p in assessment.get("retrieved_papers", [])[:3]:
            top_abstracts.append(
                f"  [{p.get('year', '?')}] {p['title']}\n  {p['abstract'][:500]}"
            )

        claims_and_evidence.append(
            f"[{cid}] {claim['statement']}\n"
            f"  H0: {claim['null_hypothesis']}\n"
            f"  Librarian: strength={assessment.get('evidence_strength', '?')}, "
            f"novelty={assessment.get('novelty_flag', '?')}\n"
            f"  Confounders: {[c['confounder'] for c in assessment.get('confounders_identified', [])]}\n"
            f"  Top retrieved abstracts for your sanity-check:\n" + "\n".join(top_abstracts)
        )

    analyst_section = _format_analyst_for_skeptic(analyst_result)

    completed_analysis = formalized.get("completed_analysis") or []
    methods_used = formalized.get("methods_used") or []
    critique_active = bool(completed_analysis)
    critique_section = _format_completed_analysis(methods_used, completed_analysis, analyst_result) if critique_active else ""

    system = SKEPTIC_SYSTEM_BASE + (SKEPTIC_CRITIQUE_BLOCK if critique_active else "")

    user_msg = f"""CORE HYPOTHESIS:
{formalized['core_hypothesis']}

DOMAIN: {formalized.get('domain', 'unknown')}

USER-CITED LITERATURE:
{chr(10).join(f"- {r['user_reference']['title_or_description']}" for r in evidence['cited_literature_validated'])}

CLAIMS + EVIDENCE + TOP ABSTRACTS:
{chr(10).join(claims_and_evidence)}
{analyst_section}{critique_section}"""
    return llm_call_json("skeptic", system, user_msg, max_tokens=3500)


def _format_completed_analysis(methods_used: list[str], completed: list[dict], analyst_result: dict | None) -> str:
    lines = ["\n\nMETHODS USED (already run by the author):"]
    if methods_used:
        lines += [f"  - {m}" for m in methods_used]
    else:
        lines.append("  (not explicitly listed)")

    lines.append("\nCOMPLETED ANALYSIS — reported findings (critique these):")
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
        return "\nGENOMIC EVIDENCE: Not available — score genomic_evidence_alignment as 5 (neutral)."

    interp = analyst_result.get("interpretation", {})
    set_a_stats = analyst_result.get("set_a_stats") or {}
    set_b_stats = analyst_result.get("set_b_stats") or {}
    cross_set = analyst_result.get("cross_set") or {}

    lines = ["\nGENOMIC EVIDENCE (Analyst):"]
    lines.append(f"  Overall genomic assessment: {interp.get('overall_genomic_assessment', '?')}")
    lines.append(f"  Justification: {interp.get('assessment_justification', '')}")

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
