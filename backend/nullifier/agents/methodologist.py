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
        "Only pick tests from the library names listed in the task context.",
        "Always compare starter or expanded.* sets against controls.* — that is the core question every run.",
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


METHODOLOGIST_SYSTEM = METHODOLOGIST_SPEC.render_system_prompt()


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

    context = {
        "domain": formalized.get("domain", ""),
        "starter_count": expansion.get("starter_count", 0),
        "expanded_sets": list((expansion.get("expanded") or {}).keys()),
        "control_sets": list((expansion.get("controls") or {}).keys()),
        "available_tests": TEST_LIBRARY_DOC,
        "data_shape": (
            "groups: {<group>: {<metric>: [values]}} "
            "— names: starter, expanded.<set>, controls.<set>; "
            "metrics: dnds, ortholog_count, paralog_count, duplication_count, regulatory_feature_count. "
            "variables: {<metric>: [values aligned to gene_index]}. tables: typically empty."
        ),
        "prepared_groups": "\n".join(groups_lines) or "(none)",
        "prepared_variables": "\n".join(variables_lines) or "(none)",
        "gene_index_size": data_summary.get("n_genes", 0),
        "tables": data_summary.get("tables", []),
    }
    if completed_analysis:
        lines = []
        for i, finding in enumerate(completed_analysis, 1):
            entry = f"{i}. {finding.get('finding', '')}"
            if finding.get("test"):
                entry += f"  [test: {finding['test']}]"
            if finding.get("statistic"):
                entry += f"  [statistic: {finding['statistic']}]"
            if finding.get("sample_size"):
                entry += f"  [n: {finding['sample_size']}]"
            lines.append(entry)
        context["completed_analysis"] = (
            "Author-reported (design tests that could reproduce/contradict): " + "; ".join(lines)
        )

    task = TaskObject(
        title="Method selection plan",
        semantic_inputs={"hypothesis": formalized.get("core_hypothesis", "")},
        entities=tuple(expansion.get("starter") or []),
        contextual_state=context,
        expected_outputs=("tests_requested", "correction", "primary_tests", "rationale"),
    )
    return task.render()
