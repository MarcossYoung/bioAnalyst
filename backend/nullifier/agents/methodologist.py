from ..tools.llm_client import llm_call_json
from ..tools.compute import TEST_LIBRARY, TEST_LIBRARY_DOC
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject


METHODOLOGIST_SPEC = AgentSpec(
    name="methodologist",
    mission="Choose a deterministic analysis plan from the compute library based on the prepared gene-set data.",
    capabilities=(
        "Select tests from the available library.",
        "Define inputs for each test.",
        "Choose a multiple-testing correction and primary tests for robustness.",
    ),
    behavioral_constraints=(
        "Only pick tests from the library names listed in the compute layer.",
        "Return only a plan, not computed results.",
        "Keep each rationale to one sentence.",
    ),
    verification_rules=(
        "If no tests apply, return an empty list.",
        "Always pair mann_whitney_posthoc with a correction when there are more than two groups.",
    ),
    output_contract=OutputContract(
        summary="Structured analysis plan for the compute layer.",
        fields=(
            OutputField("tests_requested", "Tests and inputs to execute."),
            OutputField("correction", "Multiple-testing correction strategy."),
            OutputField("primary_tests", "Tests used for leave-one-out robustness checks."),
            OutputField("rationale", "Overall plan justification."),
        ),
    ),
)


METHODOLOGIST_SYSTEM = f"""{METHODOLOGIST_SPEC.render_system_prompt()}

Available tests:
{TEST_LIBRARY_DOC}

The data dict the runtime will pass to the tests has this shape:
- groups: {{"<group>": {{"<metric>": [values], ...}}}}
  group names include "starter", "expanded.<set>", and "controls.<set>".
  metrics: dnds, ortholog_count, paralog_count, duplication_count, regulatory_feature_count.
- variables: {{"<metric>": [aligned values across gene_index]}} (same metrics as above)
- gene_index: list of gene symbols matched to variables
- tables: typically empty.

Design a plan that compares starter (or expanded.*) against controls.* - that is the
question the tool is asked to answer every run."""


def run_methodologist(
    formalized: dict,
    expansion: dict,
    data_summary: dict,
    completed_analysis: list | None = None,
) -> dict:
    user = _build_user_prompt(formalized, expansion, data_summary, completed_analysis or [])
    plan = llm_call_json("methodologist", METHODOLOGIST_SYSTEM, user, max_tokens=2500)

    if not isinstance(plan, dict):
        plan = {}
    plan.setdefault("tests_requested", [])
    plan.setdefault("primary_tests", [])
    plan.setdefault("correction", "benjamini_hochberg")
    plan["primary_tests"] = [
        t for t in (plan.get("primary_tests") or [])
        if isinstance(t, dict) and t.get("test") in TEST_LIBRARY
    ]
    return plan


def _build_user_prompt(
    formalized: dict,
    expansion: dict,
    data_summary: dict,
    completed_analysis: list,
) -> str:
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

    task = TaskObject(
        title="Method selection plan",
        semantic_inputs={"hypothesis": formalized.get("core_hypothesis", "")},
        entities=tuple(expansion.get("starter") or []),
        contextual_state={
            "domain": formalized.get("domain", ""),
            "starter_count": expansion.get("starter_count", 0),
            "expanded_sets": list((expansion.get("expanded") or {}).keys()),
            "control_sets": list((expansion.get("controls") or {}).keys()),
        },
        expected_outputs=("tests_requested", "correction", "primary_tests", "rationale"),
    )

    return f"""{task.render()}

PREPARED DATA SUMMARY (counts of non-null values per group/metric and per variable):
groups:
{chr(10).join(groups_lines) or '  (none)'}
variables:
{chr(10).join(variables_lines) or '  (none)'}
gene_index: {data_summary.get('n_genes', 0)} genes
tables: {data_summary.get('tables', [])}
{completed_block}
"""
