import sys

from ..tools.llm_client import llm_call_json
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject


FORMALIZER_STAGE1_SPEC = AgentSpec(
    name="scientific hypothesis extractor",
    mission="Separate the falsifiable core from scaffolding and capture any already-completed analysis without inventing details.",
    capabilities=(
        "Extract a concise core hypothesis from a proposal, memo, or write-up.",
        "Separate cited literature, proposed methods, starter data, and exploratory goals from the falsifiable claim.",
        "Detect methods_used and completed_analysis only when the text actually reports them.",
        "Preserve verbatim numbers when the source provides them.",
    ),
    behavioral_constraints=(
        "Do not treat methods or literature review as claims.",
        "Do not invent methods_used or completed_analysis.",
        "Only the core hypothesis is falsified.",
        "Return JSON only.",
    ),
    guarantees=(
        "The output preserves the author's reported analysis state when it exists.",
        "Scaffolding stays separate from the hypothesis under test.",
    ),
    verification_rules=(
        "If completed results are absent, return an empty list.",
        "If a number is present in the input, copy it verbatim where possible.",
    ),
    output_contract=OutputContract(
        summary="Structured stage-1 extraction output.",
        fields=(
            OutputField("core_hypothesis", "One concise paragraph describing the empirical claim."),
            OutputField("cited_literature", "List of user-cited references with relevance notes."),
            OutputField("proposed_methods", "Methods the author plans to run."),
            OutputField("methods_used", "Methods the author already ran."),
            OutputField("completed_analysis", "Completed findings with statistic, test, sample size, and interpretation."),
            OutputField("starter_data", "Brief description of any starter data."),
            OutputField("starter_entities", "Starter gene or entity list."),
            OutputField("domain", "Declared domain label."),
            OutputField("key_entities", "Key entities surfaced from the text."),
        ),
        notes=("Treat this as semantic extraction, not creative writing.",),
    ),
)

FORMALIZER_STAGE2_SPEC = AgentSpec(
    name="scientific hypothesis formalizer",
    mission="Decompose the core hypothesis into atomic claims that can be tested independently.",
    capabilities=(
        "Turn the core hypothesis into minimal testable predictions.",
        "Assign each claim a plain-English statement, entities, relationship, context, mechanism, null hypothesis, and testability.",
    ),
    behavioral_constraints=(
        "Do not broaden the hypothesis beyond the text.",
        "Do not omit null hypotheses.",
        "Return JSON only.",
    ),
    verification_rules=(
        "Each atomic claim should be minimal and independently testable.",
        "Key search terms should support later literature retrieval.",
    ),
    output_contract=OutputContract(
        summary="Structured atomic-claim decomposition.",
        fields=(
            OutputField("atomic_claims", "List of minimal testable predictions."),
            OutputField("key_search_terms", "Search terms for literature retrieval."),
        ),
    ),
)


BIOLOGY_DOMAINS = {"biology", "neuroscience", "genomics", "molecular_biology", "neurobiology"}


def _text_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _reference_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _completed_analysis_list(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise ValueError("Formalizer stage1 completed_analysis must be a list of findings")
    out: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            out.append(item)
        else:
            raise ValueError("Formalizer stage1 completed_analysis items must be JSON objects")
    return out


def _normalize_stage1_output(stage1: dict) -> dict:
    if not isinstance(stage1, dict):
        raise ValueError("Formalizer stage1 must return a JSON object")

    out = dict(stage1)
    core = out.get("core_hypothesis")
    if not isinstance(core, str) or not core.strip():
        raise ValueError("Formalizer stage1 must include a non-empty core_hypothesis string")
    out["core_hypothesis"] = core.strip()
    out["domain"] = str(out.get("domain", "unknown") or "unknown").strip() or "unknown"
    out["starter_data"] = str(out.get("starter_data", "") or "").strip()
    out["cited_literature"] = _reference_list(out.get("cited_literature"))
    out["proposed_methods"] = _text_list(out.get("proposed_methods"))
    out["methods_used"] = _text_list(out.get("methods_used"))
    out["starter_entities"] = _text_list(out.get("starter_entities"))
    out["key_entities"] = _text_list(out.get("key_entities"))
    out["completed_analysis"] = _completed_analysis_list(out.get("completed_analysis"))
    return out


def _normalize_stage2_output(stage2: dict) -> dict:
    if not isinstance(stage2, dict):
        raise ValueError("Formalizer stage2 must return a JSON object")

    out = dict(stage2)
    claims = out.get("atomic_claims")
    if not isinstance(claims, list):
        raise ValueError("Formalizer stage2 must include atomic_claims as a list")

    normalized_claims: list[dict] = []
    for idx, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError("Formalizer stage2 atomic_claims items must be JSON objects")
        statement = claim.get("statement")
        null_hypothesis = claim.get("null_hypothesis")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"Formalizer stage2 atomic claim {idx + 1} is missing statement")
        if not isinstance(null_hypothesis, str) or not null_hypothesis.strip():
            raise ValueError(f"Formalizer stage2 atomic claim {idx + 1} is missing null_hypothesis")
        normalized_claim = dict(claim)
        normalized_claim["id"] = str(claim.get("id") or f"claim_{idx + 1}")
        normalized_claim["statement"] = statement.strip()
        normalized_claim["null_hypothesis"] = null_hypothesis.strip()
        normalized_claims.append(normalized_claim)

    out["atomic_claims"] = normalized_claims
    out["key_search_terms"] = _text_list(out.get("key_search_terms"))
    return out


def formalize_stage1(raw_text: str) -> dict:
    task = TaskObject(
        title="Stage 1 hypothesis extraction",
        semantic_inputs={"raw_text": raw_text},
        expected_outputs=(
            "core_hypothesis",
            "cited_literature",
            "proposed_methods",
            "methods_used",
            "completed_analysis",
            "starter_data",
            "starter_entities",
            "domain",
            "key_entities",
        ),
    )
    return _normalize_stage1_output(llm_call_json(
        "formalizer_stage1",
        FORMALIZER_STAGE1_SPEC.render_system_prompt(),
        task.render(),
        max_tokens=2000,
    ))


def formalize_stage2(stage1: dict) -> dict:
    task = TaskObject(
        title="Stage 2 atomic-claim decomposition",
        semantic_inputs={"core_hypothesis": stage1["core_hypothesis"]},
        entities=tuple(stage1.get("key_entities", []) or []) + tuple(stage1.get("starter_entities", []) or []),
        contextual_state={
            "core_hypothesis": stage1.get("core_hypothesis", ""),
            "key_entities": ", ".join(stage1.get("key_entities", [])),
            "starter_entities": ", ".join(stage1.get("starter_entities", [])),
        },
        expected_outputs=("atomic_claims", "key_search_terms"),
    )
    return _normalize_stage2_output(llm_call_json(
        "formalizer_stage2",
        FORMALIZER_STAGE2_SPEC.render_system_prompt(),
        task.render(),
        max_tokens=2000,
    ))


def formalize(raw_input: str, interactive: bool = True) -> dict:
    stage1 = formalize_stage1(raw_input)

    domain = stage1.get("domain", "unknown").lower()
    if domain not in BIOLOGY_DOMAINS:
        print(
            f"\n⚠ Warning: detected domain '{domain}'. Nullifier is biology-specialized.",
            file=sys.stderr,
        )
        print(
            "  Literature analysis will run; genomic analysis requires starter entities.\n",
            file=sys.stderr,
        )

    if interactive:
        stage1 = _confirmation_gate(stage1)

    stage1 = _normalize_stage1_output(stage1)
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
        choice = input("Proceed with this hypothesis? yes / edit / abort: ").strip().lower()
        if choice in ("", "yes"):
            return stage1
        if choice in ("abort", "no"):
            print("Aborted.", file=sys.stderr)
            sys.exit(0)
        if choice == "edit":
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
