Here's a full end-to-end trace. I'll invent a plausible synapse_bbb.txt consistent with the docs (a co-evolution hypothesis), run it through every stage with the genetics made concrete, then show the same run resolving to STRONG, FALSIFIED, and RESULTS-PROBLEMATIC. At each stage I'll mark [SEAM] where the tool assumes something a generic version wouldn't.

The input (synapse_bbb.txt)

"Synaptic and blood-brain-barrier genes co-evolved under shared selective pressure. Because the synapse depends on a tightly regulated extracellular environment maintained by the BBB, I expect synaptic genes (SynGO) and BBB genes to show comparable evolutionary constraint, more similar to each other than either is to matched control genes. Starter entities: DLG4, GRIN1, SHANK3, CLDN5, OCLN, SLC2A1."

Note it's free-form prose with starter genes and no reported statistics — so this run will not trigger the RESULTS-PROBLEMATIC path (I'll add that variant at the end).

Stage 1 — Formalizer (Claude)
Extracts the falsifiable core and decomposes it into atomic claims, each with a null hypothesis:

C1: Synaptic genes show stronger purifying selection (lower dN/dS) than matched controls. H₀: dN/dS(synaptic) = dN/dS(control).
C2: BBB genes show stronger purifying selection than matched controls. H₀: dN/dS(BBB) = dN/dS(control).
C3: Synaptic and BBB constraint distributions are more similar to each other than to controls. H₀: the two sets are drawn from the same distribution as controls / no greater mutual similarity.

It detects no completed analysis (no p-values in the text), flags starter_entities = [DLG4, GRIN1, SHANK3, CLDN5, OCLN, SLC2A1], and presents this for confirmation. You'd see this in the ConfirmModal and could edit/drop a claim before the expensive retrieval runs.
=

Stage 2 — Librarian (Gemma per-paper, Claude synthesis)
For each atomic claim, the Query Expander generates 5–8 variants and hits all four sources. For C1 you'd get queries like "synaptic gene evolutionary conservation dN/dS," "postsynaptic density purifying selection," "SynGO constraint primate." Each retrieved paper is classified by local Gemma as supports / contradicts / tangential / confounder with a verbatim abstract sentence.
A plausible evidence picture:

C1 (synaptic conserved): strongly supported. This is well-established — postsynaptic scaffolds and glutamate receptors are textbook conserved. Several supports.
C2 (BBB conserved): thinner. Tight-junction proteins are conserved, but "BBB genes as a set under shared constraint" is less studied. Mix of supports and tangential.
C3 (shared/co-evolution): likely sparse or absent. The specific co-evolution claim may have little or no direct literature → this is where novelty_flag fires.

The Claude synthesizer rolls each claim's classifications into evidence_strength + gap + novelty_flag.


Stage 3 — Gene-set assembly (gene_sets.py)
Takes your 6 starter genes and expands against canonical sets:

set_a (synaptic): your synaptic starters anchor an expansion to the SynGO membership → ~tens to a few hundred genes.
set_b (BBB): CLDN5/OCLN/SLC2A1 anchor the BBB set.
controls: matched random genes (matched for length, GC, expression breadth — whatever the matching logic uses). This is the load-bearing piece. Without a matched control set, "synaptic genes have low dN/dS" is uninterpretable, because lots of genes have low dN/dS.

Gemma scores set relevance, heuristic fallback if LM Studio is down.


Stage 4 — Ensembl fetch + genomic_data builder
For every gene in all three sets, ensembl.py pulls orthologs + dN/dS, paralogs, gene tree, regulatory features, motifs (cached 30 days). genomic_data.py assembles the typed GenomicData object the compute layer consumes.
The dN/dS is the actual evidence: for each gene, the ratio of amino-acid-changing to silent substitutions against orthologs. Low ratio = purifying selection = "evolution is protecting this gene."
[SEAM — the metric paradigm.] dN/dS is cross-species constraint. For human-disease genetics — especially neurodevelopmental, your father's world — the field-standard metric is LOEUF/pLI (gnomAD), which measures within-human-population intolerance to loss-of-function. A generic tool wants Ensembl to be one QuantitativeLens implementation among several (gnomAD-constraint lens, GTEx-expression lens, enrichment lens), all emitting a common evidence shape. This is the seam that coincides with your analyst_result shim — more on that below.

Stage 5 — Methodologist (Claude)
Reads the hypothesis + a summary of the genomic data and writes a test plan. For this hypothesis it would select:

Mann-Whitney U for C1: synaptic dN/dS vs control dN/dS (rank-based, because dN/dS is right-skewed, not normal).
Mann-Whitney U for C2: BBB vs control.
Kruskal-Wallis across all three sets for C3, or a distributional comparison testing whether A and B are mutually closer than to controls.
Benjamini-Hochberg correction across the family of tests.
Effect sizes (rank-biserial / Cliff's delta) and bootstrap CIs, because a biologist will not trust a p-value without an effect size.

It picks which tests; it computes nothing.


Stage 6 — Compute + Robustness (compute.py, no LLM)
Executes deterministically via scipy/statsmodels. Returns typed TestResult objects: statistic, raw p, corrected p, effect size, CI. Then the leave-one-out robustness pass: drop each gene, re-run, see if significance survives.
This is the trust layer. A real geneticist's eye goes straight to: corrected p, effect size, and whether LOO holds.

Stage 7 — Interpreter → Skeptic (Claude)
Interpreter reads typed results in plain language (outlier genes, limitations). Skeptic independently re-checks all evidence — literature and compute — scores seven dimensions, lists alternative explanations, names a decisive experiment, and issues the verdict.

Now the same run, three ways:
→ STRONG

C1: synaptic dN/dS median ≈ 0.08 vs control ≈ 0.22, Mann-Whitney p_adj < 0.001, large effect (Cliff's δ ≈ 0.6), robust — no single gene flips it. C2: BBB similarly low, p_adj < 0.01. C3: both sets cluster apart from controls. Literature independently supports C1 and C2. No credible contradictions.

What makes it STRONG: independent supporting lines (literature and statistics agree), large effects, LOO-robust. The Skeptic's job here is to try to break it and fail.
→ FALSIFIED

C3 is the load-bearing claim. Suppose: synaptic genes ARE constrained (C1 holds), BBB genes are NOT distinguishable from controls (C2 fails, p_adj = 0.4), and the literature turns up a contradicts paper showing BBB endothelial genes evolve under relaxed constraint with lineage-specific turnover.

The Skeptic issues FALSIFIED on the co-evolution claim as stated — C1 being true doesn't rescue C3. The decisive-experiment field might say: "test whether BBB constraint covaries with synaptic constraint within species lineages, not just at the set level." This is the most valuable kind of output — it kills a wrong idea before a grad student spends a year on it.
(Contrast: if C3 had simply returned no literature at all rather than contradicting evidence, the verdict would be NOVEL-UNTESTED, not FALSIFIED — uninvestigated ≠ disproven.)
→ RESULTS-PROBLEMATIC (the variant where the input includes completed analyses)

Now imagine the input said: "We found synaptic genes have lower dN/dS than BBB genes (t-test, p = 0.03, n = 18)."

The Skeptic's critique panel would flag, with HIGH severity:

Statistics: a t-test on dN/dS is wrong — the distribution is skewed, should be Mann-Whitney. Reviewer-in-a-box catch.
Multiple testing: if multiple comparisons were run and only p = 0.03 reported, that's uncorrected → likely noise.
Power: n = 18 is small; the result may hinge on one or two genes (this is exactly what LOO is designed to expose).
Reproducibility: the Analyst tries to cross-check the reported dN/dS values against Ensembl-retrievable ones and flags any it can't verify.

The UI then renders two verdict cards: left = the hypothesis verdict (from the falsifiability score), right = the critique breakdown.
