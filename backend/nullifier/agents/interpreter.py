"""Interpreter agent (v6).

Reads typed Compute results + raw per-gene genomic data + (optional) robustness
output + (optional) reproducibility check, and produces a calibrated
interpretation. Cannot invent numbers — every claim about a value must trace to
one of the inputs.
"""
from statistics import mean
from ..tools.llm_client import llm_call_json


INTERPRETER_SYSTEM = """You are the Interpreter. You read structured outputs from a deterministic
Compute layer (statistical test results with effect sizes, p-values, multiple-testing corrections),
together with raw per-gene genomic data and the original hypothesis, and produce a calibrated
interpretation.

You CANNOT invent numbers. Every claim about a number must trace to a value in the inputs.

You must:
1. Describe the genomic patterns observed (what the tests + raw data actually show).
2. Interpret what these patterns suggest about the hypothesis (supporting / neutral / contradicting).
3. Be explicit about LIMITATIONS — dN/dS is pairwise human-vs-X (not branch-specific);
   regulatory overlap is Jaccard-style (not statistically normalized); the tool is observational,
   not a phylogenetic comparative method.
4. Flag specific outlier genes if any single gene is doing the work.
5. If REPRODUCIBILITY_CHECK is non-empty, report which reported findings the tool can or cannot
   independently verify here. Be HONEST about what is not reconstructable from Ensembl alone.

Respond with ONLY valid JSON:
{
  "patterns_observed": [
    {"pattern": "...", "supports_hypothesis": "yes|no|neutral", "evidence": "specific numbers"}
  ],
  "outlier_genes": [
    {"gene": "...", "why_notable": "...", "implication": "..."}
  ],
  "regulatory_overlap": {
    "shared_tf_motifs": ["..."],
    "jaccard_index": 0.0,
    "interpretation": "..."
  },
  "reproducibility_check": [
    {"reported": "...", "ensembl_value": "... or 'n/a'", "verifiable": true,
     "note": "agrees / disagrees / not checkable here because ..."}
  ],
  "limitations": ["..."],
  "overall_genomic_assessment": "supports|neutral|contradicts|inconclusive",
  "assessment_justification": "2-3 sentences"
}
(Omit reproducibility_check — or return [] — when no REPRODUCIBILITY_CHECK section is given.)"""


def run_interpreter(formalized: dict, expansion: dict, compute_results: dict,
                    gene_data: dict, robustness: dict | None = None,
                    reproducibility: dict | None = None) -> dict:
    user = _build_user_prompt(formalized, expansion, compute_results, gene_data,
                              robustness, reproducibility)
    return llm_call_json("interpreter", INTERPRETER_SYSTEM, user, max_tokens=3500)


def _build_user_prompt(formalized: dict, expansion: dict, compute_results: dict,
                       gene_data: dict, robustness: dict | None,
                       reproducibility: dict | None) -> str:
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
        for k in ("statistic", "p_value", "p_value_adjusted", "effect_size", "ci",
                  "significant", "significant_adjusted"):
            if k in t and t[k] is not None:
                bits.append(f"{k}={t[k]}")
        line = "  - " + ", ".join(bits)
        if t.get("rationale"):
            line += f"  // {t['rationale']}"
        test_lines.append(line)

    corr_lines = []
    for c in compute_results.get("corrections_applied") or []:
        corr_lines.append(
            f"  - {c.get('method')} (n_tests={c.get('n_tests')}, alpha={c.get('alpha')})"
        )

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
        dnds_vals = [o["dnds"] for o in orthologs
                     if o.get("dnds") is not None and o["dnds"] < 10]
        dnds_mean = f"{mean(dnds_vals):.3f}" if dnds_vals else "n/a"
        per_gene_lines.append(
            f"  {g}: orthologs={len(orthologs)}, paralogs={len(paralogs)}, "
            f"duplications={tree.get('duplication_count', 0)}, "
            f"regulatory_features={len(reg)}, mean_dN/dS={dnds_mean}"
        )

    return f"""HYPOTHESIS: {formalized.get('core_hypothesis', '')}

GENE-SET CONTEXT:
  starter ({expansion.get('starter_count', 0)}): {', '.join(expansion.get('starter') or [])}
  expanded sets: {list((expansion.get('expanded') or {}).keys())}
  control sets: {list((expansion.get('controls') or {}).keys())}

DETERMINISTIC COMPUTE RESULTS (typed; you cannot invent or override these):
tests:
{chr(10).join(test_lines) or '  (none)'}
corrections_applied:
{chr(10).join(corr_lines) or '  (none)'}
{rb_block}{repro_block}
PER-GENE GENOMIC DATA SUMMARY:
{chr(10).join(per_gene_lines) or '  (no gene data)'}
"""
