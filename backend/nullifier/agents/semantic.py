from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _bullets(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else "- (none)"


def _kv_lines(items: Mapping[str, Any]) -> str:
    if not items:
        return "  (none)"
    return "\n".join(f"  - {key}: {value}" for key, value in items.items())


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
