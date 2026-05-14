import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..tools.llm_client import llm_call_json, llm_call_json_batch
from ..tools.literature import federated_search, find_by_title, SourceHealth
from ..tools.query_expander import expand_queries
from ..tools.flag_store import get_relevant_flags, format_flags_for_prompt
from .. import events as ev

# Hard ceiling on the per-claim literature fan-out, in seconds. With the
# circuit breaker + tight per-request timeouts this should almost never fire.
_PER_CLAIM_SEARCH_BUDGET = 45.0


PER_PAPER_SYSTEM_BASE = """You are a scientific paper classifier. Given an atomic claim 
and ONE paper abstract, classify the paper as one of:
- "supports": directly supports the claim
- "contradicts": directly contradicts the claim
- "tangential": related but doesn't directly bear on the claim
- "confounder": describes an alternative explanation

CRITICAL: Quote the EXACT sentence from the abstract that justifies your classification. 
If you cannot quote a sentence that justifies it, classify as "tangential".

Respond with ONLY valid JSON:
{
  "classification": "supports|contradicts|tangential|confounder",
  "justification_quote": "EXACT sentence from abstract, verbatim",
  "reasoning": "brief explanation"
}"""


SYNTHESIZER_SYSTEM = """You are a scientific librarian synthesizing per-paper classifications 
into an overall evidence assessment for one atomic claim.

You receive:
- The atomic claim
- Per-paper classifications (from a faster model)

Produce an overall assessment.

CRITICAL: Distinguish UNSTUDIED from CONTRADICTED.
- "well-studied": many papers directly address this exact claim
- "sparsely-studied": a few papers touch on it
- "unstudied": no papers directly investigate this claim (even if adjacent topics are studied)

Unstudied is NOT the same as weak. A novel hypothesis with no literature is novel, not falsified.

Respond with ONLY valid JSON:
{
  "claim_id": "...",
  "confounders_identified": [
    {"confounder": "...", "source_paper_title": "...", "why_it_matters": "..."}
  ],
  "evidence_strength": "strong|moderate|weak|absent",
  "novelty_flag": "well-studied|sparsely-studied|unstudied",
  "literature_gap": "what is NOT yet studied that would test this claim",
  "synthesis": "2-3 sentences summarizing the literature state"
}"""


def retrieve_evidence(formalized: dict, max_papers_per_claim: int = 12, on_event=None) -> dict:
    relevant_flags = get_relevant_flags(
        formalized.get("core_hypothesis", ""),
        formalized.get("domain", "unknown"),
        formalized.get("key_entities", []) + formalized.get("starter_entities", [])
    )
    flags_section = format_flags_for_prompt(relevant_flags)
    per_paper_system = (flags_section + "\n\n" + PER_PAPER_SYSTEM_BASE) if flags_section else PER_PAPER_SYSTEM_BASE

    # One circuit breaker shared across every search in this run.
    health = SourceHealth()

    # Validate user-cited literature (in parallel — one federated search per ref)
    cited_refs = formalized.get("cited_literature", [])
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

    for claim in formalized.get("atomic_claims", []):
        # Expand queries
        expanded = expand_queries(claim, starter_entities)
        if on_event:
            on_event(ev.queries_expanded(claim["id"], len(expanded)))

        # Federated search across all query variants concurrently
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

        # === Per-paper classification (LOCAL, parallel) ===
        per_paper_inputs = [
            (per_paper_system, _build_per_paper_input(claim, p), 800)
            for p in all_papers
        ]
        classifications_raw = llm_call_json_batch("librarian_per_paper", per_paper_inputs)

        # Stitch results back to papers
        classifications = []
        for paper, cls in zip(all_papers, classifications_raw):
            if "_error" in cls:
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

        # === Synthesizer (CLAUDE) ===
        synthesizer_input = _build_synthesizer_input(claim, classifications)
        synthesis = llm_call_json(
            "librarian_synthesizer", SYNTHESIZER_SYSTEM, synthesizer_input, max_tokens=2000
        )

        if on_event:
            on_event(ev.synthesis_ready(
                claim["id"],
                synthesis.get("evidence_strength", "?"),
                synthesis.get("novelty_flag", "?"),
            ))

        claim_evidence[claim["id"]] = {
            **synthesis,
            "classifications": classifications,
            "retrieved_papers": all_papers,
            "queries_used": expanded,
        }

    return {
        "cited_literature_validated": cited_validated,
        "claim_evidence": claim_evidence,
        "api_status": api_status_acc,
        "flags_applied": len(relevant_flags),
    }


def _build_per_paper_input(claim: dict, paper: dict) -> str:
    return f"""ATOMIC CLAIM: {claim['statement']}
NULL HYPOTHESIS: {claim['null_hypothesis']}

PAPER:
Title: {paper['title']}
Year: {paper.get('year', '?')}
Abstract: {paper['abstract'][:2000]}
"""


def _build_synthesizer_input(claim: dict, classifications: list[dict]) -> str:
    cls_str = "\n".join(
        f"- [{c['classification']}] {c['paper_title']} ({c.get('year', '?')})\n"
        f"  Quote: \"{c['justification_quote']}\"\n"
        f"  Reasoning: {c['reasoning']}"
        for c in classifications
    ) or "(no papers classified)"

    return f"""ATOMIC CLAIM: {claim['statement']}
NULL HYPOTHESIS: {claim['null_hypothesis']}
CONTEXT: {claim.get('context', '')}

PER-PAPER CLASSIFICATIONS:
{cls_str}
"""