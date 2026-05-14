from concurrent.futures import ThreadPoolExecutor
from statistics import mean, stdev
from ..tools.llm_client import llm_call_json
from ..tools import ensembl


ANALYST_SYSTEM = """You are a genomic data analyst. You receive structured genomic data 
about two gene sets (or one gene set + control) and a hypothesis about their relationship. 
Your job is to interpret the genomic patterns relative to the hypothesis.

You see:
- For each gene: ortholog count across mammals, dN/dS distribution, paralog count, 
  duplication events on the gene tree, regulatory features.
- For each gene SET: aggregate statistics (mean dN/dS, ortholog conservation, etc.)
- For pairs of sets: overlap in regulatory features (shared TF binding motifs)

You must:
1. Describe the genomic patterns observed (what's actually in the data).
2. Interpret what these patterns suggest about the hypothesis (supporting/neutral/contradicting).
3. Be explicit about LIMITATIONS: the dN/dS values are pairwise human-vs-X (not branch-specific).
   Regulatory feature overlap is Jaccard-style, not statistically normalized. The Analyst is
   observational, not a rigorous phylogenetic comparative method.
4. Flag specific genes that stand out (e.g., one gene with 10x the dN/dS of others).
5. IF a "REPRODUCIBILITY DATA" section is present, the author has already run analyses and reported
   numbers. For EACH reported finding, say whether you can independently check it against the
   Ensembl-derived values you have, and HONESTLY flag what you cannot verify here (e.g.
   branch-specific dN/dS needs PAML/codeml; gene constraint/LOEUF needs gnomAD; custom statistical
   tests and sample-size adequacy need the raw study data). Put this in "reproducibility_check".

Respond with ONLY valid JSON:
{
  "patterns_observed": [
    {"pattern": "...", "supports_hypothesis": "yes|no|neutral", "evidence": "specific numbers"}
  ],
  "outlier_genes": [
    {"gene": "...", "why_notable": "...", "implication": "..."}
  ],
  "regulatory_overlap": {
    "shared_tf_motifs": ["..."],
    "jaccard_index": 0.0,
    "interpretation": "..."
  },
  "reproducibility_check": [
    {"reported": "the author's finding/statistic", "ensembl_value": "what Ensembl gives, or 'n/a'",
     "verifiable": true, "note": "agrees / disagrees / not checkable here because ..."}
  ],
  "limitations": ["..."],
  "overall_genomic_assessment": "supports|neutral|contradicts|inconclusive",
  "assessment_justification": "2-3 sentences"
}
(Omit "reproducibility_check" — or return [] — when no REPRODUCIBILITY DATA section is given.)"""


def run_analyst(formalized: dict, use_cache: bool = True, on_gene=None) -> dict:
    """Pull Ensembl data for starter entities, compute stats, send to Claude for interpretation.

    on_gene(gene: str, status: "ok"|"error") called after each Ensembl fetch (from worker threads).
    """
    starter = formalized.get("starter_entities", [])
    if not starter:
        return {"skipped": True, "reason": "No starter entities provided in input."}

    set_a, set_b = _split_into_sets(formalized, starter)

    gene_data = _fetch_all_gene_data(starter, use_cache, on_gene=on_gene)

    # Compute aggregate statistics per set
    set_a_stats = _set_statistics(set_a, gene_data) if set_a else None
    set_b_stats = _set_statistics(set_b, gene_data) if set_b else None
    cross_set = _cross_set_analysis(set_a, set_b, gene_data) if (set_a and set_b) else None

    # Reproducibility check — only when the author reported completed analyses
    reproducibility = _reproducibility_check(formalized, starter, gene_data)

    # Send to Claude for interpretation
    user_msg = _build_analyst_input(
        formalized, set_a, set_b, gene_data, set_a_stats, set_b_stats, cross_set, reproducibility
    )
    interpretation = llm_call_json("analyst", ANALYST_SYSTEM, user_msg, max_tokens=3500)

    return {
        "skipped": False,
        "set_a": set_a,
        "set_b": set_b,
        "gene_data": gene_data,
        "set_a_stats": set_a_stats,
        "set_b_stats": set_b_stats,
        "cross_set": cross_set,
        "reproducibility": reproducibility,
        "interpretation": interpretation,
    }


# Categories the Analyst genuinely cannot verify from Ensembl alone.
_NOT_VERIFIABLE_HERE = [
    "branch-specific / lineage-specific dN/dS (requires PAML/codeml on an alignment)",
    "gene constraint scores (pLI / LOEUF — requires gnomAD)",
    "custom statistical tests and their p-values (requires the raw study data)",
    "sample-size adequacy and power (requires the study design)",
    "expression / single-cell results (requires the relevant atlas, not Ensembl gene records)",
]


def _reproducibility_check(formalized: dict, genes: list[str], gene_data: dict) -> dict | None:
    """If the author reported completed analyses, surface the Ensembl-derived values
    available for cross-reference. The actual reconciliation is done by the LLM
    (analyst interpretation) and the Skeptic; this just assembles the inputs honestly."""
    completed = formalized.get("completed_analysis") or []
    if not completed:
        return None

    retrievable = {}
    for g in genes:
        d = gene_data.get(g, {})
        if "_error" in d:
            retrievable[g] = {"available": False, "reason": d["_error"]}
            continue
        dnds_vals = [o["dnds"] for o in d.get("orthologs", [])
                     if o.get("dnds") is not None and o["dnds"] < 10]
        retrievable[g] = {
            "available": True,
            "ortholog_count": len(d.get("orthologs", [])),
            "paralog_count": len(d.get("paralogs", [])),
            "duplication_count": (d.get("gene_tree") or {}).get("duplication_count", 0),
            "regulatory_feature_count": len(d.get("regulatory_features", [])),
            "mean_pairwise_dnds": round(mean(dnds_vals), 4) if dnds_vals else None,
        }

    verifiable_count = sum(1 for v in retrievable.values() if v.get("available"))
    return {
        "reported_findings": completed,
        "ensembl_retrievable": retrievable,
        "not_verifiable_here": _NOT_VERIFIABLE_HERE,
        "verifiable_count": verifiable_count,
        "total": len(completed),
    }


def _split_into_sets(formalized: dict, starter: list[str]) -> tuple[list[str], list[str]]:
    """Split starter entities into two sets based on hypothesis context.
    Uses Claude to classify if the hypothesis names two distinct sets; otherwise returns (starter, [])."""
    SPLIT_SYSTEM = """You receive a hypothesis and a list of gene symbols. The hypothesis 
likely names two distinct sets of genes. Classify each gene into 'set_a' or 'set_b' based 
on the hypothesis. If you cannot confidently split into two sets, put all genes in set_a 
and leave set_b empty.

Respond with ONLY valid JSON:
{
  "set_a_label": "...",
  "set_a": ["GENE1", "GENE2", ...],
  "set_b_label": "...",
  "set_b": ["GENE3", ...]
}"""
    user = f"""Hypothesis: {formalized['core_hypothesis']}

Genes to classify: {', '.join(starter)}
"""
    result = llm_call_json("analyst", SPLIT_SYSTEM, user, max_tokens=1000)
    return result.get("set_a", starter), result.get("set_b", [])


def _fetch_all_gene_data(genes: list[str], use_cache: bool, on_gene=None,
                         starter_genes: set[str] | None = None) -> dict:
    """Fetch Ensembl data for a gene list using a tiered strategy.

    Starter genes get a full 6-call fetch. Expanded/control genes get a light
    2-call fetch (lookup + orthologs) since dN/dS and ortholog_count are the
    primary inputs to the compute layer; the remaining fields default to empty.
    """
    starters = {g.upper() for g in (starter_genes or [])}

    def _full(g: str) -> tuple[str, dict]:
        info = ensembl.lookup_gene(g, use_cache)
        if not info:
            if on_gene:
                on_gene(g, "error")
            return g, {"_error": "not found in Ensembl"}
        orthologs = ensembl.get_orthologs(g, use_cache=use_cache)
        paralogs = ensembl.get_paralogs(g, use_cache=use_cache)
        tree = ensembl.get_gene_tree(g, use_cache=use_cache)
        reg = []
        motifs = []
        if info.get("chromosome") and info.get("start") and info.get("end"):
            reg = ensembl.get_regulatory_features(
                info["chromosome"], info["start"], info["end"], use_cache=use_cache
            )
            motifs = ensembl.get_motif_features(
                info["chromosome"], info["start"], info["end"], use_cache=use_cache
            )
        if on_gene:
            on_gene(g, "ok")
        return g, {
            "info": info,
            "orthologs": orthologs,
            "paralogs": paralogs,
            "gene_tree": tree,
            "regulatory_features": reg,
            "motif_features": motifs,
        }

    def _light(g: str) -> tuple[str, dict]:
        """2-call fetch: lookup + orthologs only."""
        info = ensembl.lookup_gene(g, use_cache)
        if not info:
            if on_gene:
                on_gene(g, "error")
            return g, {"_error": "not found in Ensembl"}
        orthologs = ensembl.get_orthologs(g, use_cache=use_cache)
        if on_gene:
            on_gene(g, "ok")
        return g, {
            "info": info,
            "orthologs": orthologs,
            "paralogs": [],
            "gene_tree": None,
            "regulatory_features": [],
            "motif_features": [],
        }

    def _dispatch(g: str) -> tuple[str, dict]:
        return _full(g) if g.upper() in starters else _light(g)

    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for g, data in ex.map(_dispatch, genes):
            out[g] = data
    return out


def _set_statistics(genes: list[str], gene_data: dict) -> dict:
    """Aggregate stats across a gene set."""
    valid = [g for g in genes if "_error" not in gene_data.get(g, {})]
    if not valid:
        return {"valid_gene_count": 0}

    ortholog_counts = [len(gene_data[g]["orthologs"]) for g in valid]
    paralog_counts = [len(gene_data[g]["paralogs"]) for g in valid]
    duplication_counts = [
        (gene_data[g]["gene_tree"] or {}).get("duplication_count", 0)
        for g in valid
    ]

    # Pool dN/dS values across all orthologs of all genes in the set
    dnds_values = []
    for g in valid:
        for o in gene_data[g]["orthologs"]:
            if o.get("dnds") is not None and o["dnds"] < 10:  # filter outliers
                dnds_values.append(o["dnds"])

    return {
        "valid_gene_count": len(valid),
        "missing_genes": [g for g in genes if g not in valid],
        "mean_ortholog_count": mean(ortholog_counts) if ortholog_counts else 0,
        "mean_paralog_count": mean(paralog_counts) if paralog_counts else 0,
        "mean_duplication_count": mean(duplication_counts) if duplication_counts else 0,
        "dnds_n": len(dnds_values),
        "dnds_mean": mean(dnds_values) if dnds_values else None,
        "dnds_stdev": stdev(dnds_values) if len(dnds_values) > 1 else None,
        "dnds_max": max(dnds_values) if dnds_values else None,
    }


def _cross_set_analysis(set_a: list[str], set_b: list[str], gene_data: dict) -> dict:
    """Compare regulatory feature overlap between two gene sets (Jaccard on TF motifs)."""
    def _tfs(genes: list[str]) -> set:
        tfs = set()
        for g in genes:
            for m in gene_data.get(g, {}).get("motif_features", []) or []:
                if m.get("transcription_factor_complex"):
                    tfs.add(m["transcription_factor_complex"])
        return tfs

    tfs_a = _tfs(set_a)
    tfs_b = _tfs(set_b)
    union = tfs_a | tfs_b
    intersection = tfs_a & tfs_b
    jaccard = len(intersection) / len(union) if union else 0
    return {
        "set_a_tf_count": len(tfs_a),
        "set_b_tf_count": len(tfs_b),
        "shared_tfs": sorted(intersection),
        "jaccard_index": jaccard,
    }


def _build_analyst_input(formalized, set_a, set_b, gene_data, set_a_stats, set_b_stats, cross_set,
                         reproducibility=None):
    repro_section = ""
    if reproducibility:
        lines = ["\nREPRODUCIBILITY DATA (the author reported completed analyses — cross-reference these):"]
        lines.append("Reported findings:")
        for i, f in enumerate(reproducibility["reported_findings"], 1):
            lines.append(
                f"  {i}. {f.get('finding', '')}"
                + (f"  [statistic: {f['statistic']}]" if f.get("statistic") else "")
                + (f"  [test: {f['test']}]" if f.get("test") else "")
                + (f"  [n: {f['sample_size']}]" if f.get("sample_size") else "")
                + (f"\n     author's interpretation: {f['interpretation']}" if f.get("interpretation") else "")
            )
        lines.append("Ensembl-derived values available for these genes:")
        for g, m in reproducibility["ensembl_retrievable"].items():
            lines.append(f"  {g}: {m}")
        lines.append("CANNOT be verified from Ensembl here (be honest about this in reproducibility_check):")
        for nv in reproducibility["not_verifiable_here"]:
            lines.append(f"  - {nv}")
        repro_section = "\n".join(lines) + "\n"

    return f"""HYPOTHESIS: {formalized['core_hypothesis']}

SET A ({len(set_a)} genes): {', '.join(set_a)}
{f"SET B ({len(set_b)} genes): {', '.join(set_b)}" if set_b else "(no second set identified)"}

PER-GENE GENOMIC DATA SUMMARY:
{_format_per_gene(gene_data)}

SET A AGGREGATE STATS:
{set_a_stats}

SET B AGGREGATE STATS:
{set_b_stats if set_b_stats else "(no set B)"}

CROSS-SET REGULATORY OVERLAP:
{cross_set if cross_set else "(no second set to compare)"}
{repro_section}"""


def _format_per_gene(gene_data: dict) -> str:
    lines = []
    for g, d in gene_data.items():
        if "_error" in d:
            lines.append(f"  {g}: NOT FOUND ({d['_error']})")
            continue
        n_orth = len(d["orthologs"])
        n_par = len(d["paralogs"])
        n_dup = (d["gene_tree"] or {}).get("duplication_count", 0)
        n_reg = len(d["regulatory_features"])
        dnds_vals = [o["dnds"] for o in d["orthologs"] if o.get("dnds") is not None and o["dnds"] < 10]
        dnds_mean = f"{mean(dnds_vals):.3f}" if dnds_vals else "n/a"
        lines.append(
            f"  {g}: orthologs={n_orth}, paralogs={n_par}, duplications={n_dup}, "
            f"regulatory_features={n_reg}, mean_dN/dS={dnds_mean}"
        )
    return "\n".join(lines)