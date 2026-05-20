import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..tools.llm_client import llm_call_json, llm_call_json_batch
from ..tools.literature import federated_search, find_by_title, SourceHealth
from ..tools.query_expander import expand_queries
from ..tools.flag_store import get_relevant_flags, format_flags_for_prompt
from .. import events as ev
from .semantic import (
    AgentSpec,
    OutputContract,
    OutputField,
    TaskObject,
    normalize_atomic_claim,
    normalize_cited_reference,
)


_PER_CLAIM_SEARCH_BUDGET = 45.0


LIBRARIAN_PAPER_SPEC = AgentSpec(
    name="scientific paper classifier",
    mission="Classify one paper against one atomic claim while preserving the exact abstract sentence that justifies the judgment.",
    capabilities=(
        "Map each paper to supports, contradicts, tangential, or confounder.",
        "Quote the exact abstract sentence that supports the classification.",
        "Prefer tangential when no exact supporting sentence exists.",
    ),
    behavioral_constraints=(
        "Do not invent a supporting quote.",
        "If you cannot quote a sentence that justifies the classification, classify as tangential.",
        "Return JSON only.",
    ),
    verification_rules=(
        "The quote must be verbatim from the abstract.",
        "The classifier should be conservative when evidence is indirect.",
    ),
    output_contract=OutputContract(
        summary="Per-paper classification result.",
        fields=(
            OutputField("classification", "supports, contradicts, tangential, or confounder."),
            OutputField("justification_quote", "Exact sentence copied verbatim from the abstract."),
            OutputField("reasoning", "Brief explanation of the classification."),
        ),
    ),
)

LIBRARIAN_SYNTHESIS_SPEC = AgentSpec(
    name="scientific librarian synthesizer",
    mission="Collapse per-paper classifications into an overall evidence assessment for one atomic claim.",
    capabilities=(
        "Distinguish unstudiable from contradicted claims.",
        "Summarize evidence strength and novelty state.",
        "Identify confounders and remaining literature gaps.",
    ),
    behavioral_constraints=(
        "Do not confuse unstudied with weak.",
        "Return JSON only.",
    ),
    guarantees=(
        "The synthesis reflects the classified papers rather than inventing new evidence.",
    ),
    output_contract=OutputContract(
        summary="Claim-level literature synthesis.",
        fields=(
            OutputField("claim_id", "Atomic claim identifier."),
            OutputField("confounders_identified", "Alternative explanations observed in the literature."),
            OutputField("evidence_strength", "strong, moderate, weak, or absent."),
            OutputField("novelty_flag", "well-studied, sparsely-studied, or unstudied."),
            OutputField("literature_gap", "What is still missing from the literature."),
            OutputField("synthesis", "Two to three sentence summary of the evidence state."),
        ),
    ),
)


def retrieve_evidence(formalized: dict, max_papers_per_claim: int = 12, on_event=None) -> dict:
    relevant_flags = get_relevant_flags(
        formalized.get("core_hypothesis", ""),
        formalized.get("domain", "unknown"),
        formalized.get("key_entities", []) + formalized.get("starter_entities", []),
    )
    flags_section = format_flags_for_prompt(relevant_flags)
    per_paper_system = LIBRARIAN_PAPER_SPEC.render_system_prompt()

    health = SourceHealth()

    cited_refs = [normalize_cited_reference(ref) for ref in (formalized.get("cited_literature", []) or [])]
    cited_validated = []
    if cited_refs:
        with ThreadPoolExecutor(max_workers=min(len(cited_refs), 2)) as ex:
            fut_to_ref = {
                ex.submit(find_by_title, ref["title_or_description"][:150], health): ref
                for ref in cited_refs
            }
            for fut in as_completed(fut_to_ref):
                ref = fut_to_ref[fut]
                try:
                    hit = fut.result()
                except Exception:
                    hit = None
                cited_validated.append({"user_reference": ref, "database_match": hit})

    claim_evidence = {}
    api_status_acc = {}
    starter_entities = formalized.get("starter_entities", [])

    for idx, raw_claim in enumerate(formalized.get("atomic_claims", []) or []):
        claim = normalize_atomic_claim(raw_claim, idx)
        expanded = expand_queries(claim, starter_entities)
        if on_event:
            on_event(ev.queries_expanded(claim["id"], len(expanded)))

        all_papers = []
        seen_ids = set()
        if expanded:
            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=min(len(expanded), 2)) as ex:
                fut_to_qv = {
                    ex.submit(federated_search, qv["query"], 3, health): qv
                    for qv in expanded
                }
                for fut in as_completed(fut_to_qv):
                    if time.monotonic() - t0 > _PER_CLAIM_SEARCH_BUDGET:
                        for f in fut_to_qv:
                            f.cancel()
                        break
                    try:
                        papers, status = fut.result()
                    except Exception:
                        papers, status = [], {}
                    for k, v in status.items():
                        api_status_acc.setdefault(k, []).append(v)
                    for p in papers:
                        dedup_key = p.get("doi") or p.get("title", "").lower()[:100]
                        if dedup_key and dedup_key not in seen_ids:
                            all_papers.append(p)
                            seen_ids.add(dedup_key)
                    if len(all_papers) >= max_papers_per_claim:
                        for f in fut_to_qv:
                            f.cancel()
                        break
            all_papers = all_papers[:max_papers_per_claim]

        if on_event:
            on_event(ev.papers_retrieved(claim["id"], len(all_papers)))

        per_paper_inputs = [
            (per_paper_system, _build_per_paper_input(claim, p, flags_section), 800)
            for p in all_papers
        ]
        classifications_raw = llm_call_json_batch("librarian_per_paper", per_paper_inputs)

        classifications = []
        failed_classifications = []
        for idx, paper in enumerate(all_papers):
            cls = classifications_raw[idx] if idx < len(classifications_raw) else {"_error": "missing batch result"}
            if not isinstance(cls, dict) or "_error" in cls:
                failed_classifications.append({
                    "paper_id": f"{paper['source']}:{paper['id']}",
                    "paper_title": paper["title"],
                    "error": (cls.get("_error") if isinstance(cls, dict) else "invalid classifier output") or "classifier failed",
                })
                continue
            entry = {
                "paper_id": f"{paper['source']}:{paper['id']}",
                "paper_title": paper["title"],
                "year": paper.get("year"),
                "venue": paper.get("venue"),
                "classification": cls.get("classification", "tangential"),
                "justification_quote": cls.get("justification_quote", ""),
                "reasoning": cls.get("reasoning", ""),
            }
            classifications.append(entry)
            if on_event:
                on_event(ev.paper_classified(claim["id"], paper["title"], entry["classification"]))

        cls_entries = tuple(
            f"[{c['classification']}] {c['paper_title']} ({c.get('year', '?')})\n"
            f"  Quote: \"{c['justification_quote']}\"\n"
            f"  Reasoning: {c['reasoning']}"
            for c in classifications
        ) or ("(no papers classified)",)

        synth_task = TaskObject(
            title="Claim-level literature synthesis",
            semantic_inputs={"claim": claim},
            evidence=cls_entries,
            contextual_state={"claim_id": claim["id"]},
            expected_outputs=("confounders_identified", "evidence_strength", "novelty_flag", "literature_gap", "synthesis"),
        )
        synthesis = llm_call_json(
            "librarian_synthesizer",
            LIBRARIAN_SYNTHESIS_SPEC.render_system_prompt(),
            synth_task.render(),
            max_tokens=2000,
        )

        if on_event:
            on_event(
                ev.synthesis_ready(
                    claim["id"],
                    synthesis.get("evidence_strength", "?"),
                    synthesis.get("novelty_flag", "?"),
                )
            )

        claim_evidence[claim["id"]] = {
            **synthesis,
            "classifications": classifications,
            "failed_classifications": failed_classifications,
            "retrieved_papers": all_papers,
            "queries_used": expanded,
        }

    return {
        "cited_literature_validated": cited_validated,
        "claim_evidence": claim_evidence,
        "api_status": api_status_acc,
        "flags_applied": len(relevant_flags),
    }


def _build_per_paper_input(claim: dict, paper: dict, flags_section: str = "") -> str:
    cs: dict = {"year": paper.get("year", "?"), "source": paper.get("source", "")}
    if flags_section:
        cs["prior_review_flags"] = flags_section
    task = TaskObject(
        title="Per-paper classification",
        semantic_inputs={"claim": claim["statement"], "null_hypothesis": claim["null_hypothesis"]},
        evidence=(paper.get("title", ""), paper.get("abstract", "")[:2000]),
        contextual_state=cs,
        constraints=("Quote the exact abstract sentence that justifies the classification.",),
        expected_outputs=("classification", "justification_quote", "reasoning"),
    )
    return task.render()
