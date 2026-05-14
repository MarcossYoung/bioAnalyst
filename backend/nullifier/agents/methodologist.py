"""Methodologist agent (v6).

Receives the hypothesis, the gene-set expansion result, and a summary of the
data the runtime has prepared. Returns a structured analysis plan consumable
by ``tools.compute.run_analysis_plan``. Does NOT run any computation.
"""
from ..tools.llm_client import llm_call_json
from ..tools.compute import TEST_LIBRARY, TEST_LIBRARY_DOC


METHODOLOGIST_SYSTEM = f"""You are a Methodologist. Given a hypothesis, the gene-set context
(starter + canonical sets + matched controls), and a summary of prepared data, you decide
which statistical tests from the available library to run, with what inputs, and what
multiple-testing correction to apply.

You produce only a PLAN. You do NOT compute results.

{TEST_LIBRARY_DOC}

The data dict the runtime will pass to the tests has this shape:
- groups: {{"<group>": {{"<metric>": [values], ...}}}}
  group names include "starter", "expanded.<set>", and "controls.<set>".
  metrics: dnds, ortholog_count, paralog_count, duplication_count, regulatory_feature_count.
- variables: {{"<metric>": [aligned values across gene_index]}} (same metrics as above)
- gene_index: list of gene symbols matched to variables
- tables: typically empty.

Rules:
- Only pick tests from the library names listed above. If nothing applicable, return an empty list.
- For mann_whitney_posthoc with >2 groups, always pair with a correction.
- Mark 1-3 tests as primary_tests — those are used for leave-one-out robustness checks.
- The default question is: does the starter (or an expanded canonical set) differ from
  controls.* on one or more genomic metrics?
- Keep each rationale to one sentence.

Respond with ONLY valid JSON:
{{
  "tests_requested": [
    {{"test": "<library_name>", "inputs": {{...}}, "rationale": "<one sentence>"}}
  ],
  "correction": "benjamini_hochberg|bonferroni|holm|none",
  "primary_tests": [
    {{"test": "<library_name>", "inputs": {{...}}}}
  ],
  "rationale": "<2-3 sentence overall plan justification>"
}}"""


def run_methodologist(formalized: dict, expansion: dict, data_summary: dict,
                      completed_analysis: list | None = None) -> dict:
    """Produce a structured analysis plan consumable by ``tools.compute.run_analysis_plan``."""
    user = _build_user_prompt(formalized, expansion, data_summary, completed_analysis or [])
    plan = llm_call_json("methodologist", METHODOLOGIST_SYSTEM, user, max_tokens=2500)

    if not isinstance(plan, dict):
        plan = {}
    plan.setdefault("tests_requested", [])
    plan.setdefault("primary_tests", [])
    plan.setdefault("correction", "benjamini_hochberg")
    # Drop primary_tests entries that reference an unknown test (they would never run).
    plan["primary_tests"] = [
        t for t in (plan.get("primary_tests") or [])
        if isinstance(t, dict) and t.get("test") in TEST_LIBRARY
    ]
    return plan


def _build_user_prompt(formalized: dict, expansion: dict, data_summary: dict,
                       completed_analysis: list) -> str:
    groups_lines = []
    for grp, metrics in (data_summary.get("groups") or {}).items():
        groups_lines.append(f"  {grp}: {dict(metrics)}")
    variables_lines = []
    for var, n in (data_summary.get("variables") or {}).items():
        variables_lines.append(f"  {var}: n={n}")

    completed_block = ""
    if completed_analysis:
        completed_block = (
            "\nCOMPLETED ANALYSIS (author-reported; consider designing tests that could "
            "independently reproduce or contradict these):\n"
        )
        for i, f in enumerate(completed_analysis, 1):
            completed_block += (
                f"  {i}. {f.get('finding', '')}"
                + (f"  [test: {f['test']}]" if f.get("test") else "")
                + (f"  [statistic: {f['statistic']}]" if f.get("statistic") else "")
                + (f"  [n: {f['sample_size']}]" if f.get("sample_size") else "")
                + "\n"
            )

    return f"""HYPOTHESIS: {formalized.get('core_hypothesis', '')}

DOMAIN: {formalized.get('domain', '')}

GENE-SET CONTEXT:
  starter ({expansion.get('starter_count', 0)}): {', '.join(expansion.get('starter') or [])}
  expanded sets: {list((expansion.get('expanded') or {}).keys())}
  control sets: {list((expansion.get('controls') or {}).keys())}
  source: {expansion.get('source', '')}

PREPARED DATA SUMMARY (counts of non-null values per group/metric and per variable):
groups:
{chr(10).join(groups_lines) or '  (none)'}
variables:
{chr(10).join(variables_lines) or '  (none)'}
gene_index: {data_summary.get('n_genes', 0)} genes
tables: {data_summary.get('tables', [])}
{completed_block}
Design a plan that compares starter (or expanded.*) against controls.* — that's the
question the tool is asked to answer every run."""
