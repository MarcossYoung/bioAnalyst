import time
from dataclasses import dataclass, field

BIOLOGY_DOMAINS = {"biology", "neuroscience", "genomics", "molecular_biology", "neurobiology"}


@dataclass
class Event:
    type: str
    payload: dict
    ts: float = field(default_factory=time.time)


def run_started() -> Event:
    return Event("run_started", {})


def stage_started(stage: str, label: str) -> Event:
    return Event("stage_started", {"stage": stage, "label": label})


def stage_completed(stage: str) -> Event:
    return Event("stage_completed", {"stage": stage})


def hypothesis_extracted(stage1: dict) -> Event:
    domain = stage1.get("domain", "unknown").lower()
    return Event("hypothesis_extracted", {
        "core_hypothesis": stage1.get("core_hypothesis", ""),
        "domain": domain,
        "is_biology": domain in BIOLOGY_DOMAINS,
        "key_entities": stage1.get("key_entities", []),
        "starter_entities": stage1.get("starter_entities", []),
        "cited_count": len(stage1.get("cited_literature", [])),
        "method_count": len(stage1.get("proposed_methods", [])),
    })


# ── Section-based confirmation gate ─────────────────────────────────────────
# Each spec: (section_id, label, stage1_key, removable, kind)
#   kind ∈ {"text", "list", "findings"} — drives how the UI renders / edits it.
SECTION_SPECS = [
    ("hypothesis",         "Core hypothesis",   "core_hypothesis",    False, "text"),
    ("methods_proposed",   "Proposed methods",  "proposed_methods",   True,  "list"),
    ("methods_used",       "Methods used",      "methods_used",       True,  "list"),
    ("starter_entities",   "Starter entities",  "starter_entities",   True,  "list"),
    ("completed_analysis", "Completed analysis","completed_analysis", True,  "findings"),
]

# Sections that exist only when the extractor actually detected them in the text.
_DETECTED_SECTIONS = {"methods_used", "completed_analysis"}


def build_confirm_sections(stage1: dict) -> list[dict]:
    """Build the section cards shown at the confirmation gate. Sections with no
    content are omitted (except the hypothesis, which is always present)."""
    sections = []
    for sid, label, key, removable, kind in SECTION_SPECS:
        value = stage1.get(key)
        has_content = bool(value) if isinstance(value, (list, str)) else value is not None
        if sid != "hypothesis" and not has_content:
            continue
        sections.append({
            "id": sid,
            "label": label,
            "kind": kind,
            "removable": removable,
            "detected": sid in _DETECTED_SECTIONS,
            "value": value if value is not None else ("" if kind == "text" else []),
        })
    return sections


_EMPTY_FOR_KIND = {"text": "", "list": [], "findings": []}


def apply_section_edits(stage1: dict, edits: dict) -> tuple[dict, list[str]]:
    """Apply {section_id: {"action": "keep"|"edit"|"remove", "value"?: ...}} to a
    copy of stage1. Returns (new_stage1, list_of_changed_section_ids)."""
    out = dict(stage1)
    changed: list[str] = []
    spec_by_id = {sid: (key, kind) for sid, _, key, _, kind in SECTION_SPECS}
    for sid, edit in (edits or {}).items():
        if sid not in spec_by_id:
            continue
        key, kind = spec_by_id[sid]
        action = (edit or {}).get("action", "keep")
        if action == "remove":
            out[key] = _EMPTY_FOR_KIND.get(kind, "")
            changed.append(sid)
        elif action == "edit" and "value" in (edit or {}):
            out[key] = edit["value"]
            changed.append(sid)
    return out, changed


def confirmation_required(stage1: dict) -> Event:
    return Event("confirmation_required", {
        "sections": build_confirm_sections(stage1),
        "domain": stage1.get("domain", "unknown"),
    })


def confirmation_received(action: str, changed_sections: list[str] | None = None) -> Event:
    return Event("confirmation_received", {
        "action": action,  # "proceed" | "edited" | "aborted"
        "changed_sections": changed_sections or [],
    })


def formalizer_detected_completed_analysis(findings: list[dict]) -> Event:
    return Event("formalizer_detected_completed_analysis", {
        "finding_count": len(findings or []),
        "findings": findings or [],
    })


def claims_formalized(stage2: dict) -> Event:
    return Event("claims_formalized", {
        "claim_count": len(stage2.get("atomic_claims", [])),
        "claims": stage2.get("atomic_claims", []),
        "key_search_terms": stage2.get("key_search_terms", []),
    })


def queries_expanded(claim_id: str, query_count: int) -> Event:
    return Event("queries_expanded", {"claim_id": claim_id, "query_count": query_count})


def papers_retrieved(claim_id: str, paper_count: int) -> Event:
    return Event("papers_retrieved", {"claim_id": claim_id, "paper_count": paper_count})


def paper_classified(claim_id: str, paper_title: str, classification: str) -> Event:
    return Event("paper_classified", {
        "claim_id": claim_id,
        "paper_title": paper_title,
        "classification": classification,
    })


def synthesis_ready(claim_id: str, evidence_strength: str, novelty_flag: str) -> Event:
    return Event("synthesis_ready", {
        "claim_id": claim_id,
        "evidence_strength": evidence_strength,
        "novelty_flag": novelty_flag,
    })


def librarian_skipped(reason: str) -> Event:
    return Event("librarian_skipped", {"reason": reason})


def classifier_degraded(claim_id: str, summary: dict) -> Event:
    return Event("classifier_degraded", {
        "claim_id": claim_id,
        "retrieved": summary.get("retrieved", 0),
        "classified": summary.get("classified", 0),
        "dropped": summary.get("dropped", 0),
        "drop_reasons": summary.get("drop_reasons", {}),
    })


def analyst_started(gene_count: int) -> Event:
    return Event("analyst_started", {"gene_count": gene_count})


def analyst_comparability_screen(total: int, kept: int, dropped: int, threshold: int) -> Event:
    return Event("analyst_comparability_screen", {
        "total": total,
        "kept": kept,
        "dropped": dropped,
        "threshold": threshold,
    })


def analyst_progress(step: str, completed: int, total: int, message: str = "") -> Event:
    return Event("analyst_progress", {
        "step": step,
        "completed": completed,
        "total": total,
        "message": message,
    })


def analyst_gene_fetched(gene: str, status: str) -> Event:
    return Event("analyst_gene_fetched", {"gene": gene, "status": status})


def analyst_symbol_resolved(retired: str, canonical: str) -> Event:
    return Event("analyst_symbol_resolved", {"retired": retired, "canonical": canonical})


def analyst_phylo_loaded(genes_with_age: int, total_genes: int) -> Event:
    return Event("analyst_phylo_loaded", {
        "genes_with_age": genes_with_age,
        "total_genes": total_genes,
    })


def analyst_gnomad_fetched(genes_with_loeuf: int, total_genes: int) -> Event:
    return Event("analyst_gnomad_fetched", {
        "genes_with_loeuf": genes_with_loeuf,
        "total_genes": total_genes,
    })


def analyst_paml_complete(n_computed: int, total: int) -> Event:
    return Event("analyst_paml_complete", {"n_computed": n_computed, "total": total})


def analyst_rdnds_complete(genes_with_dnds: int, total: int, orthologs_attached: int) -> Event:
    return Event("analyst_rdnds_complete", {
        "genes_with_dnds": genes_with_dnds,
        "total": total,
        "orthologs_attached": orthologs_attached,
    })


def diagnostics_risk_scored(gene: str, risk, tier: str, reasons: list[str]) -> Event:
    return Event("diagnostics_risk_scored", {
        "gene": gene,
        "risk": risk,
        "tier": tier,
        "reasons": reasons or [],
    })


def diagnostics_risk_survival_summary(set_name: str, summary: dict) -> Event:
    return Event("diagnostics_risk_survival_summary", {
        "set": set_name,
        **(summary or {}),
    })


def paml_gene_started(gene: str, foreground: str | None = None, model: str = "branch") -> Event:
    return Event("paml.gene_started", {"gene": gene, "model": model, **({"foreground": foreground} if foreground else {})})


def paml_gene_complete(gene: str, omega_foreground=None, omega_background=None, lrt_pvalue=None,
                       model: str = "branch", foreground: str | None = None) -> Event:
    return Event("paml.gene_complete", {
        "gene": gene, "model": model,
        "omega_foreground": omega_foreground,
        "omega_background": omega_background,
        "lrt_pvalue": lrt_pvalue,
        **({"foreground": foreground} if foreground else {}),
    })


def paml_gene_timeout(gene: str, model: str = "branch", foreground: str | None = None) -> Event:
    return Event("paml.gene_timeout", {"gene": gene, "model": model, **({"foreground": foreground} if foreground else {})})


def paml_gene_failed(gene: str, status: str, note: str,
                     diagnostics: dict | None = None, model: str = "branch",
                     foreground: str | None = None) -> Event:
    return Event("paml.gene_failed", {
        "gene": gene, "model": model,
        "status": status,
        "note": note,
        **(diagnostics or {}),
        **({"foreground": foreground} if foreground else {}),
    })


def rdnds_gene_started(gene: str) -> Event:
    return Event("rdnds.gene_started", {"gene": gene})


def rdnds_gene_complete(gene: str, species_count: int) -> Event:
    return Event("rdnds.gene_complete", {"gene": gene, "species_count": species_count})


def ensembl_batch_progress(fetched: int, total: int) -> Event:
    return Event("ensembl.batch_progress", {"fetched": fetched, "total": total})


def analyst_ready(assessment: str) -> Event:
    return Event("analyst_ready", {"overall_genomic_assessment": assessment})


def analyst_skipped(reason: str) -> Event:
    return Event("analyst_skipped", {"reason": reason})


def analyst_failed(reason: str) -> Event:
    return Event("analyst_failed", {"reason": reason})


def contract_violation(agent: str, violations: list[str]) -> Event:
    return Event("contract_violation", {
        "agent": agent,
        "violations": list(violations),
    })


def analyst_reproducibility_check_start(finding_count: int) -> Event:
    return Event("analyst_reproducibility_check_start", {"finding_count": finding_count})


def analyst_reproducibility_check_complete(verifiable_count: int, total: int) -> Event:
    return Event("analyst_reproducibility_check_complete", {
        "verifiable_count": verifiable_count,
        "total": total,
    })


def skeptic_critique_mode_active(finding_count: int) -> Event:
    return Event("skeptic_critique_mode_active", {"finding_count": finding_count})


def verdict_ready(scores: dict, verdict_str: str) -> Event:
    return Event("verdict_ready", {
        "verdict": verdict_str,
        "scores": scores,
    })


def token_update(tracker) -> Event:
    return Event("token_update", {
        "claude_input": tracker.claude_input,
        "claude_output": tracker.claude_output,
        "local_input": tracker.local_input,
        "local_output": tracker.local_output,
        "calls_claude": tracker.calls_claude,
        "calls_local": tracker.calls_local,
        "cost_estimate": tracker.cost_estimate(),
    })


def _normalize_expansion(expansion: dict | None) -> dict | None:
    """Add `expanded_sets` / `control_sets` arrays + totals derived from the raw
    `expanded` / `controls` dicts so the UI sees the same shape from the streaming
    `gene_sets_expanded` event and from `run_completed.analyst.expansion`.

    Idempotent: a payload that already has the array keys is returned unchanged.
    Preserves every other key on the input dict.
    """
    if not expansion:
        return expansion
    expanded = expansion.get("expanded") or {}
    exploratory = expansion.get("exploratory") or {}
    controls = expansion.get("controls") or {}
    expanded_keys = list(expanded.keys()) if isinstance(expanded, dict) else []
    exploratory_keys = list(exploratory.keys()) if isinstance(exploratory, dict) else []
    control_keys = list(controls.keys()) if isinstance(controls, dict) else []
    return {
        **expansion,
        "expanded_sets": expansion.get("expanded_sets") or expanded_keys,
        "exploratory_sets": expansion.get("exploratory_sets") or exploratory_keys,
        "control_sets": expansion.get("control_sets") or control_keys,
        "total_expanded": expansion.get(
            "total_expanded",
            len({str(g).upper() for genes in expanded.values() for g in (genes or [])})
            if isinstance(expanded, dict) else 0,
        ),
        "total_expanded_memberships": expansion.get(
            "total_expanded_memberships",
            sum(len(v) for v in expanded.values()) if isinstance(expanded, dict) else 0,
        ),
        "total_exploratory": expansion.get(
            "total_exploratory",
            len({str(g).upper() for genes in exploratory.values() for g in (genes or [])})
            if isinstance(exploratory, dict) else 0,
        ),
        "total_exploratory_memberships": expansion.get(
            "total_exploratory_memberships",
            sum(len(v) for v in exploratory.values()) if isinstance(exploratory, dict) else 0,
        ),
        "total_controls": expansion.get(
            "total_controls",
            len({str(g).upper() for genes in controls.values() for g in (genes or [])})
            if isinstance(controls, dict) else 0,
        ),
        "total_control_memberships": expansion.get(
            "total_control_memberships",
            sum(len(v) for v in controls.values()) if isinstance(controls, dict) else 0,
        ),
    }


def run_completed(formalized: dict, evidence: dict, verdict: dict, analyst: dict | None) -> Event:
    if analyst and isinstance(analyst.get("expansion"), dict):
        analyst = {**analyst, "expansion": _normalize_expansion(analyst["expansion"])}
    return Event("run_completed", {
        "formalized": formalized,
        "evidence": evidence,
        "verdict": verdict,
        "analyst": analyst,
    })


def run_failed(error: str) -> Event:
    return Event("run_failed", {"error": error})


def run_aborted() -> Event:
    return Event("run_aborted", {})


# ── v6 events ────────────────────────────────────────────────────────────────
def gene_sets_expanded(expansion: dict) -> Event:
    return Event("gene_sets_expanded", {
        "source": expansion.get("source"),
        "syngo_release": expansion.get("syngo_release"),
        "bbb_version": expansion.get("bbb_version"),
        "starter_count": expansion.get("starter_count", 0),
        "expanded_set_count": len(expansion.get("expanded") or {}),
        "exploratory_set_count": len(expansion.get("exploratory") or {}),
        "control_set_count": len(expansion.get("controls") or {}),
        "total_expanded": expansion.get("total_expanded", 0),
        "total_expanded_memberships": expansion.get("total_expanded_memberships", 0),
        "total_exploratory": expansion.get("total_exploratory", 0),
        "total_exploratory_memberships": expansion.get("total_exploratory_memberships", 0),
        "total_controls": expansion.get("total_controls", 0),
        "total_control_memberships": expansion.get("total_control_memberships", 0),
        "expanded_sets": list((expansion.get("expanded") or {}).keys()),
        "exploratory_sets": list((expansion.get("exploratory") or {}).keys()),
        "control_sets": list((expansion.get("controls") or {}).keys()),
    })


def methodologist_plan_complete(plan: dict) -> Event:
    return Event("methodologist_plan_complete", {
        "test_count": len(plan.get("tests_requested") or []),
        "correction": plan.get("correction"),
        "primary_test_count": len(plan.get("primary_tests") or []),
        "rationale": plan.get("rationale"),
    })


def no_applicable_tests(constructs: list[str]) -> Event:
    return Event("no_applicable_tests", {"constructs": list(constructs)})


def compute_start(test_count: int) -> Event:
    return Event("compute_start", {"test_count": test_count})


def compute_test_complete(test_name: str, p_value, significant) -> Event:
    return Event("compute_test_complete", {
        "test": test_name,
        "p_value": p_value,
        "significant": significant,
    })


def compute_all_complete(test_count: int, corrections_applied: list) -> Event:
    return Event("compute_all_complete", {
        "test_count": test_count,
        "corrections_applied": corrections_applied,
    })


def compute_robustness_start(n_perturbations: int) -> Event:
    return Event("compute_robustness_start", {"n_perturbations": n_perturbations})


def compute_robustness_complete(stability: str, agreement_fraction: float,
                                most_influential: list) -> Event:
    return Event("compute_robustness_complete", {
        "stability": stability,
        "agreement_fraction": agreement_fraction,
        "most_influential_genes": most_influential,
    })


def interpreter_start() -> Event:
    return Event("interpreter_start", {})


def interpreter_complete(assessment: str) -> Event:
    return Event("interpreter_complete", {"overall_assessment": assessment})
