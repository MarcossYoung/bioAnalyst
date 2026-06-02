from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


ALLOWED_CLAIM_CONSTRUCTS = {"set_difference", "cross_lineage_rate_correlation", "phenotype_association"}


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (none)"


def _kv_lines(items: Mapping[str, Any]) -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"  - {key}: {value}" for key, value in items.items())


def normalize_cited_reference(ref: Any) -> dict[str, Any]:
    """Return a reference dict with a canonical citation title field.

    Accepts the common variants we have seen in prompts and persisted runs:
    - title_or_description
    - title
    - description
    - title_or_decription (typo)
    """
    if not isinstance(ref, dict):
        return {"title_or_description": str(ref)}
    title = (
        ref.get("title_or_description")
        or ref.get("title_or_decription")
        or ref.get("title")
        or ref.get("description")
        or ref.get("paper_title")
        or ref.get("value")
        or ""
    )
    return {**ref, "title_or_description": title}


def normalize_atomic_claim(claim: Any, index: int | None = None) -> dict[str, Any]:
    """Return a canonical atomic-claim dict.

    Accepts both the current structured claim objects and older string-ish shapes
    so downstream stages can keep working while we transition schemas.
    """
    claim_id = f"claim_{(index + 1) if index is not None else 1}"
    if isinstance(claim, dict):
        statement = (
            claim.get("statement")
            or claim.get("claim")
            or claim.get("text")
            or claim.get("value")
            or ""
        )
        null_hypothesis = (
            claim.get("null_hypothesis")
            or claim.get("h0")
            or claim.get("null")
            or (f"Not: {statement}" if statement else "")
        )
        entities = claim.get("entities")
        if not isinstance(entities, (list, tuple)):
            entities = []
        context = claim.get("context", "")
        mechanism = claim.get("mechanism", "")
        construct = str(claim.get("construct") or "").strip()
        if construct not in ALLOWED_CLAIM_CONSTRUCTS:
            construct = "set_difference"
        return {
            **claim,
            "id": str(claim.get("id") or claim_id),
            "statement": str(statement).strip(),
            "null_hypothesis": str(null_hypothesis).strip(),
            "entities": [str(e).strip() for e in entities if str(e).strip()],
            "context": str(context).strip(),
            "mechanism": str(mechanism).strip(),
            "construct": construct,
        }

    statement = str(claim).strip()
    return {
        "id": claim_id,
        "statement": statement,
        "null_hypothesis": f"Not: {statement}" if statement else "",
        "entities": [],
        "context": "",
        "mechanism": "",
        "construct": "set_difference",
    }


def normalize_atomic_claims(claims: Any) -> list[dict[str, Any]]:
    if not isinstance(claims, list):
        return []
    return [normalize_atomic_claim(claim, idx) for idx, claim in enumerate(claims)]


@dataclass(frozen=True)
class OutputField:
    name: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class OutputContract:
    summary: str
    fields: tuple[OutputField, ...]
    notes: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [self.summary, "Return ONLY valid JSON with these fields:"]
        for field_def in self.fields:
            suffix = " (required)" if field_def.required else " (optional)"
            lines.append(f"- {field_def.name}{suffix}: {field_def.description}")
        if self.notes:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in self.notes)
        return "\n".join(lines)


@dataclass(frozen=True)
class AgentSpec:
    name: str
    mission: str
    capabilities: tuple[str, ...] = ()
    behavioral_constraints: tuple[str, ...] = ()
    guarantees: tuple[str, ...] = ()
    verification_rules: tuple[str, ...] = ()
    reasoning_policies: tuple[str, ...] = ()
    output_contract: OutputContract | None = None

    def render_system_prompt(self) -> str:
        parts = [
            f"You are {self.name}.",
            "",
            "MISSION",
            self.mission,
        ]
        if self.capabilities:
            parts.extend(["", "CAPABILITIES", _bullets(self.capabilities)])
        if self.behavioral_constraints:
            parts.extend(["", "BEHAVIORAL CONSTRAINTS", _bullets(self.behavioral_constraints)])
        if self.guarantees:
            parts.extend(["", "GUARANTEES", _bullets(self.guarantees)])
        if self.verification_rules:
            parts.extend(["", "VERIFICATION RULES", _bullets(self.verification_rules)])
        if self.reasoning_policies:
            parts.extend(["", "REASONING POLICIES", _bullets(self.reasoning_policies)])
        if self.output_contract:
            parts.extend(["", "OUTPUT CONTRACT", self.output_contract.render()])
        return "\n".join(parts)


@dataclass(frozen=True)
class TaskObject:
    title: str
    semantic_inputs: Mapping[str, Any] = field(default_factory=dict)
    entities: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    contextual_state: Mapping[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()

    def render(self) -> str:
        parts = [f"TASK: {self.title}"]
        if self.semantic_inputs:
            parts.extend(["SEMANTIC INPUTS", _kv_lines(self.semantic_inputs)])
        if self.entities:
            parts.extend(["ENTITIES", _bullets(self.entities)])
        if self.evidence:
            parts.extend(["EVIDENCE", _bullets(self.evidence)])
        if self.contextual_state:
            parts.extend(["CONTEXT", _kv_lines(self.contextual_state)])
        if self.constraints:
            parts.extend(["CONSTRAINTS", _bullets(self.constraints)])
        if self.expected_outputs:
            parts.extend(["EXPECTED OUTPUTS", _bullets(self.expected_outputs)])
        return "\n".join(parts)


@dataclass(frozen=True)
class WorkflowStep:
    name: str
    purpose: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


PIPELINE_WORKFLOW: tuple[WorkflowStep, ...] = (
    WorkflowStep(
        name="formalize",
        purpose="Extract the falsifiable core, scaffolding, and already-completed analysis.",
        outputs=("stage1", "stage2", "formalized"),
    ),
    WorkflowStep(
        name="librarian",
        purpose="Retrieve and synthesize literature evidence per atomic claim.",
        inputs=("formalized",),
        outputs=("evidence",),
        depends_on=("formalize",),
    ),
    WorkflowStep(
        name="analyst",
        purpose="Expand starter genes, fetch genomic data, and interpret patterns.",
        inputs=("formalized",),
        outputs=("gene_sets", "gene_data", "analyst_result"),
        depends_on=("formalize", "librarian"),
    ),
    WorkflowStep(
        name="methodologist",
        purpose="Select deterministic statistical tests for the prepared data.",
        inputs=("formalized", "gene_set_expansion", "data_summary"),
        outputs=("plan",),
        depends_on=("analyst",),
    ),
    WorkflowStep(
        name="compute",
        purpose="Execute the plan deterministically and emit typed results.",
        inputs=("plan", "data"),
        outputs=("compute_results", "robustness"),
        depends_on=("methodologist",),
    ),
    WorkflowStep(
        name="interpreter",
        purpose="Translate typed compute results into a calibrated narrative.",
        inputs=("formalized", "expansion", "compute_results", "gene_data"),
        outputs=("interpretation",),
        depends_on=("compute",),
    ),
    WorkflowStep(
        name="skeptic",
        purpose="Stress-test the evidence and issue the final verdict.",
        inputs=("formalized", "evidence", "analyst_result"),
        outputs=("verdict",),
        depends_on=("librarian", "interpreter"),
    ),
)
