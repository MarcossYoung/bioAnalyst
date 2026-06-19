import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config.loader import load_config
from ..tools.llm_client import llm_call_json, llm_call_json_batch
from ..tools.literature import (
    SourceHealth,
    citation_similarity,
    federated_search,
    find_by_title,
    find_snippets,
)
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


_PER_CLAIM_SEARCH_BUDGET = 30.0
_CLASSIFIER_DEGRADED_THRESHOLD = 0.5
_CITATION_MATCH_THRESHOLD = 0.35

# Disconfirming-evidence search defaults, overridable under [literature].
_HUNT_MAX_ROUNDS = 3
_HUNT_MIN_ROUNDS = 2
_HUNT_PATIENCE = 1
_HUNT_TARGET_CONTRADICTING = 2
_HUNT_TARGET_SOURCES = 2
_DISCONFIRMING = {"contradicts", "confounder"}


LIBRARIAN_PAPER_SPEC = AgentSpec(
    name="scientific paper classifier",
    mission="Classify one paper against one atomic claim while preserving the exact abstract sentence that justifies the judgment.",
    capabilities=(
        "Map each paper to supports, contradicts, tangential, or confounder.",
        "Quote exact evidence from the abstract or supplied Semantic Scholar snippet.",
        "Prefer tangential when no exact supporting sentence exists.",
    ),
    behavioral_constraints=(
        "Do not invent a supporting quote.",
        "Keep abstract quotations separate from snippet quotations.",
        "If you cannot quote supplied evidence that justifies the classification, classify as tangential.",
        "Return JSON only.",
    ),
    verification_rules=(
        "Each quote must be verbatim from its declared evidence source.",
        "The classifier should be conservative when evidence is indirect.",
    ),
    output_contract=OutputContract(
        summary="Per-paper classification result.",
        fields=(
            OutputField("classification", "supports, contradicts, tangential, or confounder."),
            OutputField("justification_quote", "Exact sentence copied from the abstract, or empty."),
            OutputField("snippet_quote", "Exact passage copied from the supplied snippet, or empty."),
            OutputField("quote_source", "abstract, snippet, or none."),
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

LIBRARIAN_HUNTER_SPEC = AgentSpec(
    name="disconfirming-evidence query strategist",
    mission="Propose new literature search queries that actively hunt for evidence capable of falsifying one atomic claim.",
    capabilities=(
        "Read the evidence gathered so far and identify which disconfirming angles are still unsearched.",
        "Generate targeted queries for contradicting findings, negative results, confounders, and alternative mechanisms.",
        "Avoid repeating queries already attempted.",
    ),
    behavioral_constraints=(
        "Prioritize falsification over confirmation.",
        "Do not repeat any query already tried.",
        "Return JSON only.",
    ),
    guarantees=(
        "Proposed queries are distinct from those already tried.",
    ),
    output_contract=OutputContract(
        summary="Next-round disconfirming search queries.",
        fields=(
            OutputField("queries", "List of search query strings aimed at falsifying the claim."),
            OutputField("rationale", "Brief note on what disconfirming angle these queries pursue."),
        ),
    ),
)


def retrieve_evidence(formalized: dict, max_papers_per_claim: int = 12, on_event=None) -> dict:
    lit_cfg = load_config().get("literature", {})
    budget = float(lit_cfg.get("per_claim_search_budget_seconds", _PER_CLAIM_SEARCH_BUDGET))
    max_rounds = int(lit_cfg.get("hunt_max_rounds", _HUNT_MAX_ROUNDS))
    min_rounds = int(lit_cfg.get("hunt_min_rounds", _HUNT_MIN_ROUNDS))
    patience = int(lit_cfg.get("hunt_patience", _HUNT_PATIENCE))
    targets = {
        "min_contradicting": int(lit_cfg.get("hunt_target_contradicting", _HUNT_TARGET_CONTRADICTING)),
        "min_sources": int(lit_cfg.get("hunt_target_sources", _HUNT_TARGET_SOURCES)),
    }
    use_snippet_search = bool(lit_cfg.get("use_snippet_search", False))
    snippet_query_limit = max(0, int(lit_cfg.get("snippet_search_query_limit", 2)))
    snippet_result_limit = max(1, int(lit_cfg.get("snippet_search_result_limit", 10)))

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
                query_text = ref.get("title_or_description", "")
                similarity = (
                    hit.get("match_score")
                    if hit and hit.get("match_score") is not None
                    else citation_similarity(query_text, hit)
                )
                if hit and similarity >= _CITATION_MATCH_THRESHOLD:
                    cited_validated.append({
                        "user_reference": ref,
                        "database_match": hit,
                        "status": "validated",
                        "similarity": round(similarity, 4),
                    })
                else:
                    cited_validated.append({
                        "user_reference": ref,
                        "database_match": None,
                        "candidate_match": hit,
                        "status": "unverified",
                        "similarity": round(similarity, 4),
                    })

    claim_evidence = {}
    api_status_acc = {}
    starter_entities = formalized.get("starter_entities", [])

    for idx, raw_claim in enumerate(formalized.get("atomic_claims", []) or []):
        claim = normalize_atomic_claim(raw_claim, idx)
        claim_errors = []

        # Evidence accumulates across the seed and disconfirming-search rounds.
        all_papers = []
        seen_ids = set()
        classifications = []
        failed_classifications = []
        used_queries = set()
        query_history = []
        hunt_trace = []
        no_progress = 0
        prev_contra = 0
        stop_reason = None
        deadline = time.monotonic() + budget

        try:
            round_queries = expand_queries(claim, starter_entities)
        except Exception as e:
            round_queries = []
            claim_errors.append(f"query expansion failed: {e}")
        if on_event:
            on_event(ev.queries_expanded(claim["id"], len(round_queries)))

        for round_idx in range(max_rounds):
            round_queries = [
                q for q in round_queries
                if q.get("query") and q["query"] not in used_queries
            ]
            if not round_queries:
                stop_reason = stop_reason or ("no_queries" if round_idx == 0 else "exhausted")
                break
            for q in round_queries:
                used_queries.add(q["query"])
                query_history.append(q["query"])

            remaining = max_papers_per_claim - len(all_papers)
            new_papers = _search_round(
                round_queries, health, seen_ids, remaining, api_status_acc, deadline
            )
            snippet_count = 0
            if use_snippet_search and snippet_query_limit and time.monotonic() < deadline:
                snippets = []
                for query_variant in round_queries[:snippet_query_limit]:
                    seconds_left = deadline - time.monotonic()
                    if seconds_left <= 0:
                        break
                    found, status = find_snippets(
                        query_variant["query"],
                        limit=snippet_result_limit,
                        health=health,
                        timeout_seconds=seconds_left,
                    )
                    api_status_acc.setdefault("semantic_scholar_snippets", []).append(status)
                    snippets.extend(found)
                snippet_count = len(snippets)
                new_papers = _merge_snippet_evidence(
                    new_papers, snippets, seen_ids, remaining
                )
            all_papers.extend(new_papers)
            if on_event:
                on_event(ev.papers_retrieved(claim["id"], len(all_papers)))

            new_class, new_failed = _classify_round(
                claim, new_papers, per_paper_system, flags_section, on_event
            )
            classifications.extend(new_class)
            failed_classifications.extend(new_failed)

            # Assess credible disconfirming evidence deterministically.
            assess = _critic_assess(classifications, targets)
            progress = assess["contradicting"] - prev_contra
            prev_contra = assess["contradicting"]
            hunt_trace.append({
                "round": round_idx,
                "queries": [q["query"] for q in round_queries],
                "new_papers": len(new_papers),
                "cumulative_papers": len(all_papers),
                "contradicting": assess["contradicting"],
                "distinct_contradicting_sources": assess["distinct_sources"],
                "progress": progress,
                "snippets_retrieved": snippet_count,
            })
            if on_event:
                on_event(ev.hunt_round(
                    claim["id"], round_idx, assess["contradicting"], len(new_papers)
                ))

            if assess["goal_met"]:
                stop_reason = "goal_met"
                break
            if len(all_papers) >= max_papers_per_claim:
                stop_reason = "budget_papers"
                break
            if time.monotonic() > deadline:
                stop_reason = "budget_time"
                break

            # Stop after repeated rounds without new disconfirming evidence.
            if not new_papers or progress <= 0:
                no_progress += 1
            else:
                no_progress = 0
            if no_progress >= patience and round_idx >= (min_rounds - 1):
                stop_reason = "saturated"
                break

            try:
                round_queries = _propose_disconfirming_queries(
                    claim, classifications, starter_entities, used_queries
                )
            except Exception as e:
                round_queries = []
                claim_errors.append(f"hunt query proposal failed: {e}")
        else:
            stop_reason = stop_reason or "budget_rounds"

        cls_entries = tuple(
            f"[{c['classification']}] {c['paper_title']} ({c.get('year', '?')})\n"
            f"  Abstract quote: \"{c['justification_quote']}\"\n"
            f"  Snippet quote: \"{c.get('snippet_quote', '')}\"\n"
            f"  Quote source: {c.get('quote_source', 'none')}\n"
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
        try:
            synthesis = llm_call_json(
                "librarian_synthesizer",
                LIBRARIAN_SYNTHESIS_SPEC.render_system_prompt(),
                synth_task.render(),
                max_tokens=2000,
            )
        except Exception as e:
            synthesis = {
                "claim_id": claim["id"],
                "confounders_identified": "",
                "evidence_strength": "absent",
                "novelty_flag": "unstudied",
                "literature_gap": f"Librarian synthesis failed: {e}",
                "synthesis": "",
            }
            claim_errors.append(f"synthesis failed: {e}")
        if not isinstance(synthesis, dict):
            synthesis = {
                "claim_id": claim["id"],
                "confounders_identified": "",
                "evidence_strength": "absent",
                "novelty_flag": "unstudied",
                "literature_gap": "Librarian synthesis returned a non-object JSON value.",
                "synthesis": "",
            }

        if on_event:
            on_event(
                ev.synthesis_ready(
                    claim["id"],
                    synthesis.get("evidence_strength", "?"),
                    synthesis.get("novelty_flag", "?"),
                )
            )

        classification_summary = _classification_summary(all_papers, classifications, failed_classifications)
        if on_event and classification_summary["classifier_degraded"]:
            on_event(ev.classifier_degraded(claim["id"], classification_summary))
        claim_evidence[claim["id"]] = {
            **synthesis,
            "classifications": classifications,
            "failed_classifications": failed_classifications,
            "classification_summary": classification_summary,
            "classifier_degraded": classification_summary["classifier_degraded"],
            "retrieved_papers": all_papers,
            "queries_used": [{"query": q} for q in query_history],
            "hunt_stop_reason": stop_reason,
            "hunt_rounds": len(hunt_trace),
            "hunt_trace": hunt_trace,
            "librarian_errors": claim_errors,
        }

    classifier_degraded = any(
        claim.get("classifier_degraded")
        for claim in claim_evidence.values()
    )
    return {
        "cited_literature_validated": cited_validated,
        "claim_evidence": claim_evidence,
        "classifier_degraded": classifier_degraded,
        "classification_summaries": {
            cid: claim.get("classification_summary", {})
            for cid, claim in claim_evidence.items()
        },
        "api_status": api_status_acc,
        "flags_applied": len(relevant_flags),
    }


def _search_round(queries, health, seen_ids, remaining, api_status_acc, deadline, workers=2):
    new_papers = []
    if remaining <= 0 or not queries:
        return new_papers
    with ThreadPoolExecutor(max_workers=min(len(queries), workers)) as ex:
        fut_to_qv = {
            ex.submit(federated_search, qv["query"], 3, health): qv
            for qv in queries
        }
        for fut in as_completed(fut_to_qv):
            if time.monotonic() > deadline:
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
                dedup_key = _paper_dedup_key(p)
                if dedup_key and dedup_key not in seen_ids:
                    new_papers.append(p)
                    seen_ids.add(dedup_key)
            if len(new_papers) >= remaining:
                for f in fut_to_qv:
                    f.cancel()
                break
    return new_papers[:remaining]


def _paper_dedup_key(paper: dict) -> str:
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return doi
    return str(paper.get("title") or "").strip().lower()[:100]


def _merge_snippet_evidence(papers, snippets, seen_ids, remaining):
    """Attach the best passage to matching papers and admit snippet-only papers."""
    merged = list(papers)
    by_key = {_paper_dedup_key(p): p for p in merged if _paper_dedup_key(p)}
    for snippet in sorted(
        snippets,
        key=lambda item: float(item.get("snippet_score") or 0),
        reverse=True,
    ):
        key = _paper_dedup_key(snippet)
        if not key:
            continue
        existing = by_key.get(key)
        if existing is not None:
            if float(snippet.get("snippet_score") or 0) > float(existing.get("snippet_score") or 0):
                existing.update({
                    "snippet_text": snippet.get("snippet_text", ""),
                    "snippet_kind": snippet.get("snippet_kind"),
                    "snippet_section": snippet.get("snippet_section"),
                    "snippet_score": snippet.get("snippet_score"),
                })
            continue
        if len(merged) >= remaining or key in seen_ids:
            continue
        merged.append(snippet)
        by_key[key] = snippet
        seen_ids.add(key)
    return merged


def _classify_round(claim, papers, per_paper_system, flags_section, on_event):
    if not papers:
        return [], []
    inputs = [
        (per_paper_system, _build_per_paper_input(claim, p, flags_section), 800)
        for p in papers
    ]
    raw = llm_call_json_batch("librarian_per_paper", inputs)
    classifications = []
    failed = []
    for i, paper in enumerate(papers):
        cls = raw[i] if i < len(raw) else {"_error": "missing batch result"}
        if not isinstance(cls, dict) or "_error" in cls:
            error = (cls.get("_error") if isinstance(cls, dict) else "invalid classifier output") or "classifier failed"
            failed.append({
                "paper_id": f"{paper['source']}:{paper['id']}",
                "paper_title": paper["title"],
                "error": error,
                "drop_reason": _classification_drop_reason(error),
            })
            continue
        abstract_quote = _exact_quote(cls.get("justification_quote"), paper.get("abstract"))
        snippet_quote = _exact_quote(cls.get("snippet_quote"), paper.get("snippet_text"))
        quote_source = "abstract" if abstract_quote else "snippet" if snippet_quote else "none"
        entry = {
            "paper_id": f"{paper['source']}:{paper['id']}",
            "paper_title": paper["title"],
            "source": paper.get("source", ""),
            "year": paper.get("year"),
            "venue": paper.get("venue"),
            "classification": cls.get("classification", "tangential"),
            "justification_quote": abstract_quote,
            "snippet_quote": snippet_quote,
            "quote_source": quote_source,
            "reasoning": cls.get("reasoning", ""),
        }
        classifications.append(entry)
        if on_event:
            on_event(ev.paper_classified(claim["id"], paper["title"], entry["classification"]))
    return classifications, failed


def _exact_quote(quote, source_text) -> str:
    quote = str(quote or "").strip()
    return quote if quote and quote in str(source_text or "") else ""


def _critic_assess(classifications, targets):
    contra = [
        c for c in classifications
        if c["classification"] in _DISCONFIRMING
        and (c.get("justification_quote") or c.get("snippet_quote"))
    ]
    sources = {
        (c.get("source") or c["paper_id"].split(":")[0])
        for c in contra
    }
    goal_met = (
        len(contra) >= targets["min_contradicting"]
        and len(sources) >= targets["min_sources"]
    )
    return {
        "contradicting": len(contra),
        "distinct_sources": len(sources),
        "goal_met": goal_met,
    }


def _propose_disconfirming_queries(claim, classifications, starter_entities, used_queries, max_queries=4):
    support = [c for c in classifications if c["classification"] == "supports"]
    contra = [c for c in classifications if c["classification"] in _DISCONFIRMING]
    evidence = (
        f"Searched so far: {len(classifications)} classified papers "
        f"({len(support)} supporting, {len(contra)} disconfirming).",
        "Supporting titles: " + ("; ".join(c["paper_title"] for c in support[:5]) or "(none)"),
        "Disconfirming titles: " + ("; ".join(c["paper_title"] for c in contra[:5]) or "(none)"),
        "Queries already tried (do not repeat): " + (" | ".join(list(used_queries)[:12]) or "(none)"),
    )
    task = TaskObject(
        title="Disconfirming-evidence query proposal",
        semantic_inputs={
            "claim": claim["statement"],
            "null_hypothesis": claim["null_hypothesis"],
            "starter_entities": ", ".join(starter_entities),
        },
        evidence=evidence,
        constraints=(
            "Target evidence that would FALSIFY the claim: contradicting findings, "
            "negative results, confounders, alternative mechanisms.",
            "Do not repeat any query already tried.",
            f"Return at most {max_queries} queries.",
        ),
        expected_outputs=("queries", "rationale"),
    )
    result = llm_call_json(
        "librarian_hunter",
        LIBRARIAN_HUNTER_SPEC.render_system_prompt(),
        task.render(),
        max_tokens=600,
    )
    out = []
    if isinstance(result, dict):
        for q in (result.get("queries") or [])[:max_queries]:
            qs = q.get("query") if isinstance(q, dict) else q
            if isinstance(qs, str) and qs.strip() and qs.strip() not in used_queries:
                out.append({"query": qs.strip()})
    return out


def _classification_drop_reason(error: str) -> str:
    text = (error or "").lower()
    if "response_format" in text or "json_schema" in text or "json_object" in text or "error code: 400" in text:
        return "api_schema_error"
    if "connection" in text or "unreachable" in text or "refused" in text or "timeout" in text:
        return "model_unreachable"
    if "json" in text or "decode" in text or "parse" in text:
        return "parse_error"
    if "empty" in text or "no content" in text:
        return "empty_response"
    if "quote" in text:
        return "quote_mismatch"
    return "other"


def _classification_summary(
    papers: list[dict],
    classifications: list[dict],
    failed_classifications: list[dict],
) -> dict:
    reasons: dict[str, int] = {}
    for failure in failed_classifications:
        reason = failure.get("drop_reason") or _classification_drop_reason(failure.get("error", ""))
        reasons[reason] = reasons.get(reason, 0) + 1
    retrieved = len(papers)
    classified = len(classifications)
    dropped = len(failed_classifications)
    degraded = bool(retrieved and dropped / retrieved > _CLASSIFIER_DEGRADED_THRESHOLD)
    return {
        "retrieved": retrieved,
        "classified": classified,
        "dropped": dropped,
        "drop_reasons": reasons,
        "classifier_degraded": degraded,
    }


def _build_per_paper_input(claim: dict, paper: dict, flags_section: str = "") -> str:
    cs: dict = {"year": paper.get("year", "?"), "source": paper.get("source", "")}
    if flags_section:
        cs["prior_review_flags"] = flags_section
    evidence = [
        f"Title: {paper.get('title', '')}",
        f"Abstract: {paper.get('abstract', '')[:2000] or '(unavailable)'}",
    ]
    if paper.get("tldr"):
        evidence.append(f"Semantic Scholar TLDR: {paper['tldr']}")
    if paper.get("snippet_text"):
        evidence.append(
            "Semantic Scholar snippet"
            f" ({paper.get('snippet_section') or paper.get('snippet_kind') or 'section unknown'}): "
            f"{paper['snippet_text']}"
        )
    task = TaskObject(
        title="Per-paper classification",
        semantic_inputs={"claim": claim["statement"], "null_hypothesis": claim["null_hypothesis"]},
        evidence=tuple(evidence),
        contextual_state=cs,
        constraints=(
            "Put only an exact abstract sentence in justification_quote.",
            "Put only an exact supplied snippet passage in snippet_quote.",
            "Set quote_source to abstract, snippet, or none.",
        ),
        expected_outputs=(
            "classification", "justification_quote", "snippet_quote", "quote_source", "reasoning"
        ),
    )
    return task.render()
