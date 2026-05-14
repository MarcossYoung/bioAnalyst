import sys
from ..tools.llm_client import llm_call_json

STAGE1_SYSTEM = """You are a scientific hypothesis extractor. You receive a research
proposal, memo, or write-up that may be at ANY stage: a fresh idea, a mid-flight
analysis, or a completed study with results. Separate the FALSIFIABLE CORE from
the SCAFFOLDING, and — if present — capture what the author has ALREADY DONE.

The falsifiable core is the specific empirical claim (1-3 sentences, "we hypothesize that...").

Scaffolding includes:
- Prior literature already cited (extract titles)
- Proposed methods — analyses the author PLANS to run (NOT claims to falsify)
- Starter datasets (gene lists, preliminary data)
- Sub-questions and exploratory goals

Already-done work (OPTIONAL — only if the text actually reports it):
- methods_used — analyses the author has ALREADY run (tests, datasets, pipelines)
- completed_analysis — concrete findings already obtained: each item is a result with
  its statistic / test / sample size / and the author's interpretation. Extract the
  numbers verbatim where given. If the text contains no completed results, return [].

Do NOT treat methods or literature review as claims. ONLY the core hypothesis gets falsified.
Do NOT invent methods_used or completed_analysis — leave them empty unless the text states them.

Respond with ONLY valid JSON:
{
  "core_hypothesis": "one concise paragraph",
  "cited_literature": [
    {"title_or_description": "...", "user_stated_relevance": "..."}
  ],
  "proposed_methods": ["method 1", ...],
  "methods_used": ["analysis the author already ran", ...],
  "completed_analysis": [
    {"finding": "...", "statistic": "e.g. r=0.62, p=0.04", "test": "e.g. Spearman correlation",
     "sample_size": "e.g. n=4 species", "interpretation": "what the author concluded"}
  ],
  "starter_data": "brief description",
  "starter_entities": ["SYP", "MFSD2A", ...],
  "domain": "biology|neuroscience|economics|physics|...",
  "key_entities": ["entity A", "entity B", ...]
}"""


STAGE2_SYSTEM = """You are a scientific hypothesis formalizer. Decompose the core 
hypothesis into ATOMIC CLAIMS — minimal testable predictions.

For each atomic claim specify: entity_a, entity_b, relationship, context, mechanism, 
null_hypothesis, testability.

Respond with ONLY valid JSON:
{
  "atomic_claims": [
    {
      "id": "C1",
      "statement": "one-sentence plain-English version",
      "entity_a": "...",
      "entity_b": "...",
      "relationship": "...",
      "context": "...",
      "mechanism": "...",
      "null_hypothesis": "...",
      "testability": "..."
    }
  ],
  "key_search_terms": ["term1", "term2", ...]
}"""


BIOLOGY_DOMAINS = {"biology", "neuroscience", "genomics", "molecular_biology", "neurobiology"}


def formalize_stage1(raw_text: str) -> dict:
    return llm_call_json("formalizer_stage1", STAGE1_SYSTEM, raw_text, max_tokens=2000)


def formalize_stage2(stage1: dict) -> dict:
    stage2_input = (
        f"Core hypothesis: {stage1['core_hypothesis']}\n\n"
        f"Key entities: {', '.join(stage1.get('key_entities', []))}\n"
        f"Starter entities: {', '.join(stage1.get('starter_entities', []))}"
    )
    return llm_call_json("formalizer_stage2", STAGE2_SYSTEM, stage2_input, max_tokens=2000)


def formalize(raw_input: str, interactive: bool = True) -> dict:
    stage1 = formalize_stage1(raw_input)

    # Domain check — biology-first warning
    domain = stage1.get("domain", "unknown").lower()
    if domain not in BIOLOGY_DOMAINS:
        print(f"\n⚠ Warning: detected domain '{domain}'. Nullifier is biology-specialized.",
              file=sys.stderr)
        print("  Literature analysis will run; genomic analysis requires starter entities.\n",
              file=sys.stderr)

    if interactive:
        stage1 = _confirmation_gate(stage1)

    stage2 = formalize_stage2(stage1)
    return {**stage1, **stage2}


def _confirmation_gate(stage1: dict) -> dict:
    print("\n" + "=" * 70, file=sys.stderr)
    print("EXTRACTED CORE HYPOTHESIS:", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(stage1["core_hypothesis"], file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"\nDomain: {stage1.get('domain', 'unknown')}", file=sys.stderr)
    print(f"Key entities: {', '.join(stage1.get('key_entities', []))}", file=sys.stderr)
    print(f"Starter entities: {', '.join(stage1.get('starter_entities', []))}", file=sys.stderr)
    print(f"Cited literature found: {len(stage1.get('cited_literature', []))} references", file=sys.stderr)
    print(f"Proposed methods found: {len(stage1.get('proposed_methods', []))} steps", file=sys.stderr)
    print()

    while True:
        choice = input("Proceed with this hypothesis? [y]es / [e]dit / [a]bort: ").strip().lower()
        if choice in ("y", "yes", ""):
            return stage1
        elif choice in ("a", "abort"):
            print("Aborted.", file=sys.stderr)
            sys.exit(0)
        elif choice in ("e", "edit"):
            print("\nEnter the corrected core hypothesis (end with a blank line):")
            lines = []
            while True:
                line = input()
                if not line:
                    break
                lines.append(line)
            if lines:
                stage1["core_hypothesis"] = " ".join(lines)
                print(f"\nUpdated: {stage1['core_hypothesis']}\n")
                return stage1