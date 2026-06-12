Explore OpenAI o3-mini or DeepSeek-R1 for the Skeptic
Gene Set Classifier (Bio Context): Consider Llama 3.1 8B Instruct
Robustness Reading: This is pure, repetitive summarization. Phi-3.5 Mini (3.8B)
Keep Claude 3.5 Sonnet as the Default: For coding, statistical planning (Methodologist), and pure logic (Formalizer),

Where I'd look for remaining hidden semantics
llm_client.py — routing logic between Claude and LM Studio probably contains implicit reasoning policy (when to use which model, fallback behavior). That should be a config/spec decision, not buried in the client.
provenance.py — provenance construction that touches reasoning outputs risks being a de facto Layer 2 task object built procedurally. Worth checking if it's assembling structured state that should be declared upstream.
For the memory agent
Given this structure, the natural insertion point is between pipeline.py and the agents — a memory middleware that compresses completed stage outputs before they get passed downstream. store/runs.py already handles persistence, so the memory agent's job is semantic compression, not storage.

Add “Exploration vs Exploitation Balancing”
==============================================

Your retrieval system is already excellent.

But eventually:

systems over-optimize toward known successful paths

You may eventually want controlled:

novelty injection
low-confidence retrieval sampling
dormant counterfactual resurfacing

Otherwise cognition becomes:

conservative
self-reinforcing
stagnant