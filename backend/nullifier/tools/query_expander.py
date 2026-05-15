from .llm_client import llm_call_json
from ..agents.semantic import normalize_atomic_claim

QUERY_EXPANDER_SYSTEM = """You are a scientific literature search expert. Given an atomic
claim and context, generate 5-8 diverse query variants for literature databases.

Cover these angles:
- Direct terms from the claim
- Synonyms and alternative phrasings
- Mechanistic terms (if a mechanism is proposed)
- Negative/contradictory framings (to find papers that REFUTE the claim)
- Specific entity names from starter data (if provided)
- Adjacent concepts (for confounders)

Respond with ONLY valid JSON:
{
  "queries": [
    {"query": "...", "intent": "direct|synonym|mechanism|contradiction|entity-specific|adjacent"},
    ...
  ]
}"""


def expand_queries(claim: dict, starter_entities: list[str] = None) -> list[dict]:
    starter_entities = starter_entities or []
    claim = normalize_atomic_claim(claim)
    entity_a = (
        claim.get("entity_a")
        or claim.get("subject")
        or claim.get("entity")
        or _first_entity(claim.get("entities"))
        or claim.get("statement", "")
    )
    entity_b = (
        claim.get("entity_b")
        or claim.get("object")
        or claim.get("target")
        or _second_entity(claim.get("entities"))
        or claim.get("null_hypothesis", "")
    )
    user = f"""Claim: {claim['statement']}
Null hypothesis: {claim['null_hypothesis']}
Entity A: {entity_a}
Entity B: {entity_b}
Context: {claim.get('context', '')}
Mechanism: {claim.get('mechanism', '')}
Starter entities to anchor queries: {', '.join(starter_entities[:20])}
"""
    result = llm_call_json("query_expander", QUERY_EXPANDER_SYSTEM, user, max_tokens=1000)
    return result.get("queries", [])


def _first_entity(value) -> str:
    if isinstance(value, (list, tuple)) and value:
        first = value[0]
        return str(first).strip() if first is not None else ""
    if isinstance(value, str):
        return value.strip()
    return ""


def _second_entity(value) -> str:
    if isinstance(value, (list, tuple)) and len(value) > 1:
        second = value[1]
        return str(second).strip() if second is not None else ""
    return ""
