# Semantic Architecture Manual

## Directory Grouping, File Naming, and Human–Transformer Readability

## Core Premise

Modern software has two readers:

1. Humans
2. Transformers / LLM agents

Traditional software organization optimized for human developers and machine execution. As LLMs become part of development, maintenance, orchestration, retrieval, and reasoning, the structure of a codebase becomes part of the communication layer.

A file tree is no longer just storage. It is a semantic map.

The goal:

```
Maximum Semantic Recoverability
```

A well-structured system should let a human or transformer answer, before opening the implementation:

```
What is this?
Where does it belong?
What does it do?
What is it related to?
Why does it exist?
```

---

## How This Document Is Organized

This manual contains two kinds of guidance, and they are not equally portable.

**Part I — Universal Principles.** Apply to any codebase. Recoverability, locality, context travel, naming density, the reason-to-change rule. These are the durable claims.

**Part II — Architectural Patterns for Agentic Systems.** A specific structural recommendation for cognition-centric systems (agents, workflows, memory, interfaces). These are not universal — they suit systems where reasoning is the primary product. A CRUD app, embedded system, or data pipeline needs different shapes, even if the universal principles still apply.

**Part III — Refactoring Toward Semantic Architecture.** Most readers will not be greenfielding. This section is about getting there from where you actually are, and when not to bother.

---

# Part I — Universal Principles

## 1. Semantic Recoverability

Meaning should be reconstructable from names, paths, and structure.

Weak:

```
src/
  utils/
  helpers/
  services/
  manager.py
```

Strong:

```
src/
  user_authentication/
    password_reset_workflow.py
    jwt_token_validation.py
    authenticated_user_schema.py
```

The second tells the reader the domain, purpose, object type, and likely relationships. Recoverability is the foundational principle. A transformer does not want clever compression — it wants stable semantic anchors. Humans benefit from the same thing.

---

## 2. Semantic Addressability

Every important object should have a precise semantic address. A path should feel like a sentence.

```
toolbox/
  manual_tools/
    screwdrivers/
      phillips_screwdriver.py
```

is stronger than:

```
tools/
  screwdriver.py
```

In agentic systems:

```
memory/
  reasoning_states/
    bbb_synapse_coevolution_reasoning_state.py
```

is more addressable than:

```
store/
  state.py
```

Each level adds meaning. The path itself becomes a description.

---

## 3. Semantic Density Has a Ceiling

Identifier names should encode what disambiguates them from their neighbors — no more.

Weak:

```python
process()
```

Better:

```python
process_gene_set()
```

Worse, not better:

```python
process_ortholog_conservation_comparison_with_dnds_thresholds()
```

Names that encode information already present in the path are noise. A function inside `ortholog_conservation/` does not need `ortholog_conservation` in its name. A function inside `dnds_analysis/` does not need `dnds` in its name.

The principle:

```
Encode meaning the path doesn't already carry.
Stop when the name disambiguates from its siblings.
```

Past that point, names become harder to read, leak implementation detail, and rot when implementation changes. Long names are not automatically more informative — they are more informative only when each word eliminates a real ambiguity.

---

## 4. Prefer Meaning Over Brevity (Within the Ceiling)

The historical pressure toward short names came from limited typing, small screens, and human-only readers. Modern systems should optimize for comprehension — within reason.

Weak:

```python
calc()
mgr()
tmp()
proc()
util()
```

Strong:

```python
calculate_invoice_total()
track_workflow_performance()
store_reasoning_state()
validate_contradictory_evidence()
```

Storage is cheap. Interpretation is expensive. A few extra words prevent many minutes of confusion. But this is bounded by Section 3 — don't keep adding words past the disambiguation point.

---

## 5. Avoid Generic Buckets

Generic directories are usually semantic dead zones.

Common weak names:

```
utils/
helpers/
common/
misc/
services/
managers/
processors/
```

These names usually mean: *I did not know where this belonged.*

Better:

```
date_formatting/
api_error_handling/
semantic_retrieval/
workflow_validation/
external_integrations/
```

Useful rule:

```
If a folder name could exist in any project, it may be too generic.
```

**Nuance on suffixes like `service`, `manager`, `handler`, `processor`.** These are weak as *standalone* names but acceptable as suffixes inside a meaningful directory. `payment_processing/payment_processor.py` is fine — the directory carries the domain. `services/manager.py` is not — both halves are generic. The issue is generic-on-generic, not the suffix itself.

---

## 6. Group by Cognitive Function, Not File Type

A common mistake is grouping by technical category:

```
agents/
  librarian_agent.py

schemas/
  paper_classification_schema.py

methods/
  evidence_synthesis_methodology.py

tools/
  literature_retrieval.py
```

This scatters one cognitive function across many directories. Better:

```
literature_evidence_review/
  librarian_agent.py
  literature_retrieval_capability.py
  paper_classification_schema.py
  evidence_synthesis_methodology.py
```

The principle:

```
Things that reason together should live together.
```

This is semantic locality.

---

## 7. Semantic Locality

Related concepts should be physically close in the file tree. If changing one part usually requires changing another, they probably belong near each other.

Semantic locality reduces navigation cost, context loss, mistaken assumptions, duplicated logic, and LLM retrieval burden.

A good question:

```
What files would I want the model to see together?
```

Those files should probably live together.

---

## 8. The Locality / Shared-Primitive Tension

Sections 6–7 say *colocate*. The next instinct (promoting shared methods) says *extract*. These pull in opposite directions, and the resolution is concrete, not philosophical.

**The Local / Shared-with-domain / Global model:**

```
local_to_module/        # used only in one module
shared_with_domain/     # used across modules in one domain
global_primitive/       # used across domains
```

**Promotion rules:**

A function stays local until it is *actually* reused. "Might be reused someday" is not a reason to promote. Premature promotion creates abstract folders that hide meaning.

Promote to domain-shared when:
- Two or more modules in the same domain genuinely use the function
- The function's interface has stabilized (no churn in the last few changes)

Promote to global primitive when:
- Modules in two or more different domains use the function
- The function operates on generic types, not domain entities

**Demotion is also legal.** If a shared primitive turns out to only be used by one module after all, move it back. Sharing is not a one-way ratchet.

**The cost of breaking locality:** every promotion increases context travel for the modules that use the primitive. Promote when the duplication cost exceeds the travel cost. For small, stable functions, duplication is often cheaper than abstraction.

---

## 9. Module Contracts (With a Caveat)

A folder representing a cognitive module benefits from a small contract file declaring its purpose, inputs, outputs, and dependencies.

```
literature_evidence_review/
  module_contract.yaml
  librarian_agent.py
  literature_retrieval_capability.py
  paper_classification_schema.py
  evidence_synthesis_methodology.py
```

```yaml
module: literature_evidence_review
purpose: retrieve and classify literature evidence for atomic scientific claims
inputs: [atomic_claims, query_variants]
outputs: [classified_papers, evidence_synthesis]
uses_shared_primitives: [semantic_retrieval, evidence_weighting]
writes_to_memory: [evidence_objects, interpretation_objects]
```

**Caveat: contracts that drift from code are worse than no contracts at all.** A stale contract actively misleads both humans and agents.

Contracts work when one of these is true:
- They are generated from code (docstrings, type signatures, imports)
- They are validated against code by CI (inputs/outputs match real signatures)
- They live close enough to the code that updating them is part of the diff

Contracts fail when they are hand-maintained, separate from the code, and never checked. Decide upfront which mode you're operating in. If you can't sustain validation, prefer a shorter contract with fewer claims that can go out of date.

---

## 10. Predictable Naming Patterns

Consistency matters. Pick patterns and keep them stable.

```
*_agent.py
*_schema.py
*_workflow.py
*_methodology.py
*_capability.py
*_contract.yaml
*_validator.py
*_repository.py
```

Suffix gives object type. Prefix gives semantic domain.

```
claim_extraction_methodology.py
formalized_claim_schema.py
formalizer_agent.py
hypothesis_confirmation_workflow.py
```

This is highly recoverable for both readers.

---

## 11. Prefer Domain-Specific Names Over Generic Engineering Names

Generic:

```
service
manager
handler
processor
controller
helper
```

Domain-specific:

```
hypothesis_family_builder
counterfactual_memory_store
evidence_weighting_engine
contradiction_validator
workflow_performance_tracker
```

Domain-specific names activate stronger semantic regions in transformer reasoning. A model can infer more from `counterfactual_memory_store.py` than from `manager.py`.

See Section 5 on suffix nuance — `*_handler.py` is fine inside `webhook_processing/`.

---

## 12. Reduce Context Travel

Context travel is the amount of navigation required to understand one behavior. This is the single most useful operational concept in the document — it reframes architectural decisions as measurable cost rather than aesthetic preference.

Bad:

```
agents/librarian.py
tools/literature.py
schemas/paper.py
methods/synthesis.py
stores/evidence.py
```

Better:

```
literature_evidence_review/
  librarian_agent.py
  literature_retrieval_capability.py
  paper_classification_schema.py
  evidence_synthesis_methodology.py
```

LLMs have limited context windows. Humans have limited working memory. Both benefit from reduced context travel. When in doubt between two structural choices, prefer the one with shorter travel for the most common task.

---

## 13. The Reason-to-Change Rule

Group files that change for the same reason. This is the Single Responsibility Principle applied at the directory level rather than the class level.

If changing one cognitive function usually requires editing its agent, schema, methodology, validator, and local prompt — colocate them.

```
hypothesis_formalization/
  formalizer_agent.py
  formalization_schema.py
  claim_extraction_methodology.py
  claim_validation_rules.py
```

If a file changes because of external API behavior, it belongs in integrations. If it changes because of UI layout, it belongs in interfaces. If it changes because of scientific reasoning, it belongs in cognition.

---

## 14. The "Open the Folder" Test

A directory is well-designed if opening it immediately reveals its purpose.

Bad: `services/` — you must inspect files to understand it.
Good: `literature_evidence_review/` — you already know the cognitive function.
Best: a meaningful directory plus a contract file plus consistent suffix patterns inside.

---

## 15. The "Would an Agent Retrieve This?" Test

Before naming or placing a file, ask:

```
Would an LLM agent retrieve this file when solving this task?
```

If yes, place it near the task's other necessary files. If no, separate it. This is the operational version of every other principle in Part I.

A skeptic agent probably needs verdict logic, contradiction detection, reasoning state, counterfactual memory. It probably does not need UI button components.

---

## 16. Anti-Patterns

**Anti-pattern 1: The Junk Drawer**

```
utils/
helpers/
misc/
```

Dumping grounds that absorb anything anyone didn't know where to put.

**Anti-pattern 2: Type-Based Overfragmentation**

```
agents/
schemas/
methods/
validators/
```

Scatters one concept across the whole repo. The opposite failure mode of the Junk Drawer — instead of one bucket for everything, one bucket per type.

**Anti-pattern 3: Premature Shared Abstractions**

```
shared/
  everything.py
```

Sharing is earned by real reuse, not anticipated.

**Anti-pattern 4: Clever Abbreviations**

```
hf_mgr.py
rs_proc.py
ev_util.py
```

Saves characters. Destroys semantic signal.

**Anti-pattern 5: Stale Contracts**

A module_contract.yaml that hasn't matched the code in six months. Worse than no contract — actively misleads both readers.

**Anti-pattern 6: Naming Inconsistency Across the Same Concept**

Using `cognition_engine/`, `cognitive_modules/`, and `cognition/` interchangeably. Pick one and hold it.

---

# Part II — Architectural Patterns for Agentic Systems

The principles in Part I apply to any codebase. The recommendations below are specific to systems where reasoning is the primary product — agents, workflows, semantic memory, retrieval pipelines. For CRUD apps, data pipelines, embedded systems, or libraries, the universal principles still hold but the structural shape will be different.

## 17. Separate System Layers by Meaning

Agentic systems typically benefit from these top-level boundaries:

```
cognition_engine/        # reasoning
semantic_memory/         # persistent state
external_integrations/   # API clients, LLM providers, databases
deterministic_execution/ # compute, transformations, validators
user_interfaces/         # web, CLI
configuration/
```

The UI consumes outputs. It does not participate in reasoning. Keeping them separate prevents semantic contamination.

## 18. Directory Names Should Describe Roles

| Weak | Stronger |
|---|---|
| `store/` | `semantic_memory/` |
| `review/` | `evidence_validation/` |
| `report/` | `output_surfaces/` |
| `tools/` | `external_integrations/` or `cognitive_capabilities/` |

Names should describe the *role* the directory plays in the system.

## 19. Workflows Coordinate; Modules Reason

Useful distinctions:

```
Cognitive module    = performs one reasoning function
Workflow            = coordinates multiple modules
Shared primitive    = reusable reasoning operation
Integration         = talks to outside systems
Memory              = stores persistent semantic state
Interface           = presents or receives human interaction
```

Workflows orchestrate clearly named stages. They should not hide all logic inside a generic `run()`:

```
workflows/
  outlier_hypothesis_generation/
    workflow_contract.yaml
    retrieve_prior_reasoning_stage.py
    generate_candidate_hypotheses_stage.py
    skeptic_precheck_stage.py
```

## 20. Memory as a First-Class Layer

For agentic systems, memory is not a persistence afterthought.

Avoid:

```
store/
  runs.py
```

Prefer:

```
semantic_memory/
  hypothesis_families/
  reasoning_states/
  evidence_objects/
  interpretation_objects/
  counterfactuals/
  workflow_performance/
```

Memory directory names should describe the *kind of semantic state* stored, not the storage mechanism.

## 21. Recommended Pattern for Agentic Systems

This is a starting shape, not a mandate. Adjust to the system's actual cognitive function set.

```
project/
  cognition_engine/
    cognitive_modules/
      module_name/
        module_contract.yaml
        module_agent.py
        module_schema.py
        module_methodology.py
        module_validation.py

    shared_primitives/
      semantic_retrieval/
      confidence_scoring/
      contradiction_detection/

    workflows/
      workflow_name/
        workflow_contract.yaml
        workflow_stages.py

  semantic_memory/
    hypothesis_families/
    reasoning_states/
    evidence_objects/
    interpretations/
    counterfactuals/

  external_integrations/
    llm_providers/
    databases/
    third_party_apis/

  deterministic_execution/
    compute/
    transformations/
    validators/

  user_interfaces/
    web_interface/
    cli_interface/

  configuration/
  examples/
  documentation/
```

This separates reasoning, memory, workflows, integrations, deterministic execution, and interfaces, while preserving semantic locality inside each module.

---

# Part III — Refactoring Toward Semantic Architecture

Most readers are not greenfielding. They have an existing codebase, technical debt, and limited time. This section is about getting there from where you are, and when not to bother.

## 22. Refactor Has a Cost

Every refactor carries:

- **Direct cost** — the engineering hours
- **Risk cost** — bugs introduced by moving working code
- **Coordination cost** — open branches, in-flight work, stale references in documentation and tickets
- **Cognitive cost** — your collaborators (human and agent) must relearn paths

The benefit must clear all four. Many architectural problems are real but not worth fixing yet.

## 23. When Refactoring Pays

Refactor when one or more of these is true:

- **Context travel is hurting daily work** — common tasks require jumping across 5+ files in scattered directories
- **Agent retrieval is failing visibly** — LLMs miss obviously-relevant files because they're in semantically misleading locations
- **The codebase is about to grow significantly** — adding new modules in the current structure will compound the problem
- **Onboarding cost is mounting** — new contributors (human or agent) repeatedly ask the same orientation questions
- **The current structure encodes obsolete assumptions** — the directory names describe what the system used to do, not what it does now

If none of these is true, the refactor is probably aesthetic. Leave it.

## 24. When Not to Refactor

Do not refactor when:

- The code works and is stable, and the structure is merely ugly
- You are mid-feature and the refactor would block delivery
- The team lacks consensus on the target structure (refactor will revert)
- The system is about to be deprecated or replaced
- You have not validated that the new structure actually improves the failing case

The last point matters most. Restructuring a codebase that *feels* badly organized but is actually fine produces churn without benefit.

## 25. Incremental Migration Path

When the refactor is worth doing, do it in this order:

**Step 1 — Establish target structure with the smallest possible scaffold.** Create the new top-level directories empty. Decide on naming conventions before moving anything. Write the conventions down.

**Step 2 — Move one cognitive module end-to-end.** Pick the module with the highest current pain (most context travel, most agent retrieval failure). Move it completely — agent, schema, methodology, validator. Update imports. Verify it works.

**Step 3 — Stop. Observe.** Use the new module for at least a week before continuing. The first migrated module almost always reveals naming or boundary mistakes. Fix those before propagating.

**Step 4 — Migrate by pain, not by alphabet.** Move modules in descending order of current pain. Modules that work fine in the old structure can wait.

**Step 5 — Tolerate a hybrid state.** A half-migrated codebase is uncomfortable but not broken. Forcing complete migration in one pass is where most refactors fail.

**Step 6 — Update contracts and documentation last.** Don't write the new module_contract.yaml until the module has stabilized in its new location. Contracts written for moving targets become Anti-pattern 5.

## 26. Refactor Triggers Worth Watching

Specific signals that a refactor is approaching worthwhile:

- A new agent or module fits naturally in the proposed new structure but awkwardly in the current one — the next feature is pushing toward the refactor anyway
- An LLM agent has repeatedly retrieved the wrong files for the same task, and prompt fixes haven't solved it
- You find yourself describing the codebase to others using a mental model that doesn't match the actual directory layout — the names have lost recoverability

## 27. Refactor Anti-Patterns

**The Big Bang.** Renaming everything in one PR. Will be reverted within weeks.

**The Aesthetic Refactor.** Restructuring because the current layout offends sensibility, not because it causes measurable problems.

**The Architecture-First Refactor.** Designing the perfect new structure in detail before moving any code. The structure won't survive contact with the actual migration.

**The Abandoned Refactor.** Starting the migration, getting blocked by an unrelated priority, and leaving the codebase in a hybrid state forever. Either commit to finishing or revert.

---

# Final Principles

```
Name things by what they mean, not just what they are.
Group by cognitive function, not file type.
Promote shared methods only after real reuse appears.
Make directory paths act like semantic addresses.
Reduce context travel.
Prefer explicitness over compression — within reason.
Keep UI, execution, integration, memory, and reasoning boundaries clear.
Use contracts that stay synchronized with code, or don't use them.
Semantic locality improves both human understanding and transformer reasoning.
A good file system is a map of meaning.
Refactor when context travel hurts, not when structure offends.
```

---

# Final Summary

The future of software architecture is not only about where files live. It is about how meaning is distributed across a system.

A strong semantic architecture lets humans and transformers recover intent quickly, navigate relationships reliably, and reason about the system with minimal ambiguity. The file tree becomes part of the cognitive interface.

The best architectures are not merely organized. They are self-explaining — and they got that way incrementally, by fixing real pain, not by chasing perfection.
