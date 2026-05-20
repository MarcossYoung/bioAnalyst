---
name: semantic-architecture
description: Transform procedural agentic codebases into explicit semantic architectures with 4 distinct layers. Use this skill whenever the user asks to refactor, restructure, or analyze an agentic system, multi-agent pipeline, AI workflow, or Claude Code setup. Trigger when the user shares code containing agents, prompts, orchestration logic, LLM calls, or tool use and asks for architectural improvement, cleanup, or restructuring. Also trigger when the user says things like "make this more structured", "separate concerns", "improve architecture", or "refactor this agent". This skill is about extracting implicit semantics and elevating them — not rewriting code mechanically.
---

# Semantic Architecture Refactoring

Your task: transform procedural agentic codebases into explicit semantic architectures.

You are NOT rewriting code mechanically.

You are identifying:
- implicit reasoning systems
- hidden semantic contracts
- operational intent
- workflow structure
- verification logic
- agent behaviors
- domain semantics

...and elevating them into explicit semantic layers.

Your goal is to separate:
- reasoning
- orchestration
- semantics
- execution

into independent architectural layers.

---

## Target Architecture

Restructure every system into 4 layers:

### Layer 1 — Agent Specifications
Defines:
- mission
- capabilities
- behavioral constraints
- guarantees
- verification rules
- output contracts
- reasoning policies

### Layer 2 — Structured Task Objects
Defines:
- semantic inputs
- entities
- evidence
- contextual state
- expected outputs
- domain objects
- task constraints

### Layer 3 — Reasoning Graphs / Workflows
Defines:
- reasoning stages
- execution order
- decomposition
- branching
- validation flows
- orchestration logic
- recovery behavior

### Layer 4 — Executable Runtime Layer
Contains:
- executable code
- APIs
- databases
- compute
- concurrency
- network calls
- persistence
- infrastructure

---

## Refactoring Rules

1. Extract implicit semantics from procedural code.
2. Convert hidden intent into explicit semantic declarations.
3. Replace large prompt prose with structured semantic objects.
4. Separate reasoning policy from execution logic.
5. Convert orchestration logic into inspectable workflows.
6. Preserve deterministic execution inside the runtime layer.
7. Prefer declarative semantic structures over imperative orchestration.
8. Make agent behavior machine-readable and composable.
9. Expose verification and limitation logic explicitly.
10. Preserve domain meaning over syntactic fidelity.

---

## Output Format

For every system analyzed, produce the following:

### 1. Identification Pass
Before producing any output, identify:
- implicit agent behaviors (what agents do without being told to)
- hidden reasoning protocols (how decisions are actually made)
- semantic contracts (what callers assume about outputs)
- orchestration patterns (how tasks are sequenced and recovered)

### 2. Layered Output

**Layer 1 — Agent Specification**
Produce a structured specification object (YAML, dataclass, or equivalent) declaring:
- `mission`: one-sentence purpose
- `capabilities`: what the agent can do
- `constraints`: what the agent must never do
- `guarantees`: what callers can rely on
- `verification_rules`: how the agent checks its own outputs
- `output_contract`: shape and semantics of outputs
- `reasoning_policy`: how the agent decides, not just what it does

**Layer 2 — Structured Task Objects**
Produce typed input/output structures for each task, including:
- named semantic fields (not raw strings)
- domain entities and their relationships
- evidence or context fields
- expected output schema
- task constraints and preconditions

**Layer 3 — Reasoning Graph**
Produce a workflow definition showing:
- named reasoning stages
- stage dependencies and execution order
- branching conditions
- validation checkpoints
- recovery and fallback paths
- orchestration entry/exit points

**Layer 4 — Runtime Responsibilities**
List what belongs exclusively in the runtime:
- which API calls happen where
- persistence and state management
- concurrency and batching
- infrastructure dependencies
- what must NOT leak into layers 1–3

### 3. Extraction Explanation
After producing the layers, explain:
- what semantics were extracted from procedural code
- what procedural logic necessarily remains in the runtime
- where reasoning was hidden in the original architecture (and why that's a problem)

---

## Important Principles

- Do not collapse semantic reasoning into executable code.
- Do not treat prompts as plain strings.
- Treat prompts as latent semantic policy definitions.
- Treat workflows as reasoning graphs.
- Treat agents as semantic entities with operational contracts.
- Treat execution code as infrastructure, not cognition.

**The objective is not cleaner code. The objective is explicit semantic architecture.**

---

## Common Patterns to Watch For

These are frequent sources of hidden semantics in agentic codebases:

| Procedural Pattern | Hidden Semantic |
|---|---|
| Long f-string prompt | Reasoning policy + output contract |
| `if result contains "error"` | Verification rule |
| Sequential LLM calls | Reasoning graph with implicit dependencies |
| Dict passed between functions | Structured task object |
| Retry loop | Recovery behavior in workflow |
| `system_prompt = "You are..."` | Agent specification |
| Hardcoded tool order | Orchestration policy |
| `assert len(output) > 0` | Output contract |

When you see these, extract the semantic — don't leave it implicit.