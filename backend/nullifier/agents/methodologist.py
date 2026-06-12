from ..tools.llm_client import llm_call_json
from ..tools.compute import TEST_LIBRARY, TEST_LIBRARY_DOC
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject

SUPPORTED_CONSTRUCTS = {
    construct
    for spec in TEST_LIBRARY.values()
    for construct in (spec.get("constructs") or set())
}
DEFERRED_CONSTRUCTS = {
}

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
    claim_constructs = _claim_constructs(formalized)
    unsupported = sorted(c for c in claim_constructs if c not in SUPPORTED_CONSTRUCTS)
    if unsupported:
        required = unsupported[0]
        return {
            "tests_requested": [],
            "primary_tests": [],
            "correction": "none",
            "untestable": True,
            "required_construct": required,
            "untestable_reason": DEFERRED_CONSTRUCTS.get(
                required,
                f"No compute test is registered for construct '{required}'.",
            ),
            "claim_constructs": sorted(claim_constructs),
            "rationale": "Construct-validity gate prevented mismatched statistical tests.",
        }
    deterministic = _deterministic_plan_for_constructs(claim_constructs, expansion)
    if deterministic:
        deterministic["claim_constructs"] = sorted(claim_constructs)
        return deterministic

    user = _build_user_prompt(formalized, expansion, data_summary, completed_analysis or [])
    plan = llm_call_json("methodologist", METHODOLOGIST_SYSTEM, user, max_tokens=2500)

    if not isinstance(plan, dict):
        plan = {}
    plan.setdefault("tests_requested", [])
    plan.setdefault("primary_tests", [])
    plan.setdefault("correction", "benjamini_hochberg")
    allowed_tests = {
        name for name, spec in TEST_LIBRARY.items()
        if (spec.get("constructs") or set()) & claim_constructs
    }
    plan["tests_requested"] = [
        t for t in (plan.get("tests_requested") or [])
        if isinstance(t, dict) and t.get("test") in allowed_tests
    ]
    plan["primary_tests"] = [
        t for t in (plan.get("primary_tests") or [])
        if isinstance(t, dict) and t.get("test") in allowed_tests
    ]
    plan["claim_constructs"] = sorted(claim_constructs)
    return plan


def _deterministic_plan_for_constructs(claim_constructs: set[str], expansion: dict) -> dict | None:
    tests_requested = []
    primary_tests = []
    rationale_bits = []

    if "cross_lineage_rate_correlation" in claim_constructs:
        erc_inputs = {
            "set_a": "starter",
            "set_b": _select_default_expanded_set(expansion),
            "controls": _control_sets(expansion),
            "background": "background.random_300",
            "min_shared_branches": 5,
            "n_iter": 2000,
            "seed": 0,
        }
        tests_requested.extend([
            {
                "test": "erc",
                "inputs": erc_inputs,
                "rationale": "Cross-lineage rate-correlation claims require ERC on per-branch relative rates, not scalar set differences.",
            },
            {
                "test": "mirrortree_lite",
                "inputs": {
                    "set_a": "starter",
                    "set_b": _select_default_expanded_set(expansion),
                    "background": "background.random_300",
                    "min_shared_species": 5,
                    "n_iter": 2000,
                    "seed": 0,
                },
                "rationale": "Mirrortree-lite is retained as the NG86 cross-check for cross-lineage covariation.",
            },
        ])
        primary_tests.append({
                "test": "erc",
                "inputs": {**erc_inputs, "n_iter": 500},
        })
        rationale_bits.append("Use ERC as the Stage-3 primary test and mirrortree-lite as the NG86 cross-check.")

    if "phenotype_association" in claim_constructs:
        rer_inputs = {
            "sets": _phenotype_sets(expansion),
            "controls": _control_sets(expansion),
            "trait": "cortical_neurons",
            "min_species": 20,
            "secondary_to": "erc",
            "require_primate_out": True,
        }
        tests_requested.append({
            "test": "rerconverge",
            "inputs": rer_inputs,
            "rationale": "Cortical-neuron phenotype association is secondary/exploratory and must pass species-overlap and primate-out guards.",
        })
        rationale_bits.append("Run RERconverge only as a secondary cortical-neuron association check; ERC carries the verdict.")

    if not tests_requested:
        return None
    return {
        "tests_requested": tests_requested,
        "primary_tests": primary_tests,
        "correction": "none",
        "rationale": " ".join(rationale_bits),
    }


def _claim_constructs(formalized: dict) -> set[str]:
    claims = formalized.get("atomic_claims") or []
    constructs = {
        str((claim or {}).get("construct") or "set_difference")
        for claim in claims
        if isinstance(claim, dict)
    }
    return constructs or {"set_difference"}


def _select_default_expanded_set(expansion: dict) -> str | None:
    names = list((expansion or {}).get("expanded") or {})
    bbb = [name for name in names if "bbb" in name.lower()]
    chosen = sorted(bbb or names)[0] if names else None
    return f"expanded.{chosen}" if chosen else None


def _control_sets(expansion: dict) -> list[str]:
    return [f"controls.{name}" for name in sorted((expansion or {}).get("controls") or {})]


def _phenotype_sets(expansion: dict) -> list[str]:
    out = ["starter"]
    default = _select_default_expanded_set(expansion)
    if default:
        out.append(default)
    return out


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
        "claim_constructs": sorted(_claim_constructs(formalized)),
        "data_shape": (
            "groups: {<group>: {<metric>: [values]}} "
            "— names: starter, expanded.<set>, controls.<set>; "
            "Evolutionary metrics: dnds (pairwise dN/dS from homology pal2nal + NG86 when CDS/alignment gates pass), "
            "ortholog_count, paralog_count, duplication_count, regulatory_feature_count. "
            "For coordinated-rate hypotheses, prefer spearman/pearson over variable dnds and another aligned variable when n permits. "
            "PAML metrics when available: omega_foreground, omega_background, acceleration_ratio. "
            "For lineage-specific hypotheses, request paml_branch_model and compare omega_foreground or acceleration_ratio across sets. "
            "gnomAD constraint (None when unavailable): "
            "loeuf (LOEUF score — lower = more constrained, intolerant to LoF); "
            "pli (pLI — probability of LoF intolerance, 0–1). "
            "Phylostratigraphy (None when unavailable): "
            "phylo_age (integer phylostratum, 1=oldest/most conserved, higher=more recently evolved; "
            "Liebeskind 2016 consensus). "
            "variables: same metrics as aligned vectors across gene_index. tables: typically empty. "
            "rate_vectors: panel-aligned per-lineage dN/dS vectors for mirrortree_lite, "
            "with sets starter, expanded.<set>, controls.<set>, and background.random_300."
        ),
        "prepared_groups": "\n".join(groups_lines) or "(none)",
        "prepared_variables": "\n".join(variables_lines) or "(none)",
        "gene_index_size": data_summary.get("n_genes", 0),
        "tables": data_summary.get("tables", []),
    }
    gnomad_prov = data_summary.get("gnomad_coverage")
    if gnomad_prov:
        context["gnomad_coverage"] = (
            f"LOEUF available for {gnomad_prov['genes_with_loeuf']} / "
            f"{gnomad_prov['total_genes']} genes (gnomAD GRCh38)"
        )
    phylo_prov = data_summary.get("phylo_coverage")
    if phylo_prov:
        context["phylo_coverage"] = (
            f"Phylostratigraphy (Liebeskind 2016) available for "
            f"{phylo_prov['genes_with_age']} / {phylo_prov['total_genes']} genes"
        )
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
