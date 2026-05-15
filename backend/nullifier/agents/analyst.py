from concurrent.futures import ThreadPoolExecutor
from statistics import mean, stdev

from ..tools.llm_client import llm_call_json
from ..tools import ensembl
from .. import events as ev
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject


ANALYST_SPEC = AgentSpec(
    name="genomic data analyst",
    mission="Interpret genomic patterns from Ensembl-derived data and surface only conclusions grounded in the provided numbers.",
    capabilities=(
        "Summarize ortholog, paralog, duplication, and regulatory feature patterns.",
        "Report pairwise regulatory overlap and per-gene outliers.",
        "Cross-reference reported results against Ensembl-retrievable values when completed analyses are present.",
    ),
    behavioral_constraints=(
        "Do not invent numbers.",
        "Do not overstate what Ensembl can verify.",
        "Return JSON only.",
    ),
    guarantees=(
        "The output stays observational rather than pretending to be a phylogenetic comparative analysis.",
    ),
    verification_rules=(
        "dN/dS values are pairwise human-vs-X and not branch-specific.",
        "If reproducibility data is present, flag what is and is not checkable here.",
    ),
    output_contract=OutputContract(
        summary="Genomic interpretation output.",
        fields=(
            OutputField("patterns_observed", "Observed patterns with support polarity and evidence."),
            OutputField("outlier_genes", "Genes that stand out and why."),
            OutputField("regulatory_overlap", "Shared TF motifs, Jaccard index, and interpretation."),
            OutputField("reproducibility_check", "Cross-reference of reported findings against Ensembl values.", required=False),
            OutputField("limitations", "Explicit limitations of the analysis."),
            OutputField("overall_genomic_assessment", "supports, neutral, contradicts, or inconclusive."),
            OutputField("assessment_justification", "Short justification for the overall assessment."),
        ),
    ),
)

ANALYST_SPLIT_SPEC = AgentSpec(
    name="genomic set splitter",
    mission="Split starter entities into two biologically meaningful sets when the hypothesis names one.",
    capabilities=(
        "Separate genes into set_a and set_b from the hypothesis context.",
        "Fail closed to a single set when the split is not confident.",
    ),
    behavioral_constraints=(
        "Do not force a split when the hypothesis does not support one.",
        "Return JSON only.",
    ),
    output_contract=OutputContract(
        summary="Set partition output.",
        fields=(
            OutputField("set_a_label", "Label for the first set."),
            OutputField("set_a", "Genes assigned to set A."),
            OutputField("set_b_label", "Label for the second set."),
            OutputField("set_b", "Genes assigned to set B."),
        ),
    ),
)


def run_analyst(formalized: dict, use_cache: bool = True, on_gene=None, on_event=None) -> dict:
    starter = formalized.get("starter_entities", [])
    if not starter:
        return {"skipped": True, "reason": "No starter entities provided in input."}

    set_a, set_b = _split_into_sets(formalized, starter)

    gene_data = _fetch_all_gene_data(starter, use_cache, on_gene=on_gene, on_event=on_event)

    set_a_stats = _set_statistics(set_a, gene_data) if set_a else None
    set_b_stats = _set_statistics(set_b, gene_data) if set_b else None
    cross_set = _cross_set_analysis(set_a, set_b, gene_data) if (set_a and set_b) else None

    reproducibility = _reproducibility_check(formalized, starter, gene_data)

    task = _analyst_task(
        formalized, set_a, set_b, gene_data, set_a_stats, set_b_stats, cross_set, reproducibility
    )
    interpretation = llm_call_json("analyst", ANALYST_SPEC.render_system_prompt(), task.render(), max_tokens=3500)

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


_NOT_VERIFIABLE_HERE = [
    "branch-specific / lineage-specific dN/dS (requires PAML/codeml on an alignment)",
    "gene constraint scores (pLI / LOEUF - requires gnomAD)",
    "custom statistical tests and their p-values (requires the raw study data)",
    "sample-size adequacy and power (requires the study design)",
    "expression / single-cell results (requires the relevant atlas, not Ensembl gene records)",
]


def _reproducibility_check(formalized: dict, genes: list[str], gene_data: dict) -> dict | None:
    completed = formalized.get("completed_analysis") or []
    if not completed:
        return None

    retrievable = {}
    for g in genes:
        d = gene_data.get(g, {})
        if "_error" in d:
            retrievable[g] = {"available": False, "reason": d["_error"]}
            continue
        dnds_vals = [o["dnds"] for o in d.get("orthologs", []) if o.get("dnds") is not None and o["dnds"] < 10]
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
    task = TaskObject(
        title="Split starter entities into two sets",
        semantic_inputs={"core_hypothesis": formalized.get("core_hypothesis", "")},
        entities=tuple(starter),
        contextual_state={"hypothesis": formalized.get("core_hypothesis", "")},
        expected_outputs=("set_a", "set_b", "set_a_label", "set_b_label"),
    )
    result = llm_call_json(
        "analyst",
        ANALYST_SPLIT_SPEC.render_system_prompt(),
        task.render()
        + "\n\nYou receive a hypothesis and a list of gene symbols. If you cannot confidently split the genes into two sets, put all genes in set_a and leave set_b empty.",
        max_tokens=1000,
    )
    return result.get("set_a", starter), result.get("set_b", [])


def _fetch_all_gene_data(
    genes: list[str],
    use_cache: bool,
    on_gene=None,
    starter_genes: set[str] | None = None,
    on_event=None,
) -> dict:
    starters = {g.upper() for g in (starter_genes or [])}

    def _full(g: str) -> tuple[str, dict]:
        info = ensembl.lookup_gene(g, use_cache)
        if not info:
            if on_gene:
                on_gene(g, "error")
            return g, {"_error": "not found in Ensembl"}
        resolved_from = info.get("_resolved_from")
        if resolved_from and on_event:
            on_event(ev.analyst_symbol_resolved(resolved_from, info["symbol"]))
        canonical = info["symbol"]
        orthologs = ensembl.get_orthologs(canonical, use_cache=use_cache)
        paralogs = ensembl.get_paralogs(canonical, use_cache=use_cache)
        tree = ensembl.get_gene_tree(canonical, use_cache=use_cache)
        reg = []
        motifs = []
        if info.get("chromosome") and info.get("start") and info.get("end"):
            reg = ensembl.get_regulatory_features(info["chromosome"], info["start"], info["end"], use_cache=use_cache)
            motifs = ensembl.get_motif_features(info["chromosome"], info["start"], info["end"], use_cache=use_cache)
        if on_gene:
            on_gene(g, "ok")
        return g, {
            "info": info,
            "resolved_from": resolved_from,
            "orthologs": orthologs,
            "paralogs": paralogs,
            "gene_tree": tree,
            "regulatory_features": reg,
            "motif_features": motifs,
        }

    def _light(g: str) -> tuple[str, dict]:
        info = ensembl.lookup_gene(g, use_cache)
        if not info:
            if on_gene:
                on_gene(g, "error")
            return g, {"_error": "not found in Ensembl"}
        resolved_from = info.get("_resolved_from")
        if resolved_from and on_event:
            on_event(ev.analyst_symbol_resolved(resolved_from, info["symbol"]))
        canonical = info["symbol"]
        orthologs = ensembl.get_orthologs(canonical, use_cache=use_cache)
        if on_gene:
            on_gene(g, "ok")
        return g, {
            "info": info,
            "resolved_from": resolved_from,
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
    valid = [g for g in genes if "_error" not in gene_data.get(g, {})]
    if not valid:
        return {"valid_gene_count": 0}

    ortholog_counts = [len(gene_data[g]["orthologs"]) for g in valid]
    paralog_counts = [len(gene_data[g]["paralogs"]) for g in valid]
    duplication_counts = [(gene_data[g]["gene_tree"] or {}).get("duplication_count", 0) for g in valid]

    dnds_values = []
    for g in valid:
        for o in gene_data[g]["orthologs"]:
            if o.get("dnds") is not None and o["dnds"] < 10:
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


def _analyst_task(
    formalized,
    set_a,
    set_b,
    gene_data,
    set_a_stats,
    set_b_stats,
    cross_set,
    reproducibility=None,
):
    evidence = [
        f"Per-gene data:\n{_format_per_gene(gene_data)}",
        f"Set A ({len(set_a)} genes) aggregate stats:\n{set_a_stats}",
        f"Set B ({len(set_b)} genes) aggregate stats:\n{set_b_stats}" if set_b else "Set B: (none)",
        f"Cross-set regulatory overlap:\n{cross_set}" if cross_set else "Cross-set overlap: (none)",
    ]

    context: dict = {
        "set_a": f"{len(set_a)} genes: {', '.join(set_a)}",
        "set_b": f"{len(set_b)} genes: {', '.join(set_b)}" if set_b else "(none)",
    }
    if reproducibility:
        repro_lines = ["Reported findings:"]
        for i, finding in enumerate(reproducibility["reported_findings"], 1):
            entry = f"  {i}. {finding.get('finding', '')}"
            if finding.get("statistic"):
                entry += f"  [statistic: {finding['statistic']}]"
            if finding.get("test"):
                entry += f"  [test: {finding['test']}]"
            if finding.get("sample_size"):
                entry += f"  [n: {finding['sample_size']}]"
            if finding.get("interpretation"):
                entry += f"\n     author's interpretation: {finding['interpretation']}"
            repro_lines.append(entry)
        repro_lines.append("Ensembl-derivable values:")
        for g, m in reproducibility["ensembl_retrievable"].items():
            repro_lines.append(f"  {g}: {m}")
        repro_lines.append("Not verifiable from Ensembl here:")
        for nv in reproducibility["not_verifiable_here"]:
            repro_lines.append(f"  - {nv}")
        context["reproducibility_data"] = "\n".join(repro_lines)

    return TaskObject(
        title="Genomic data interpretation",
        semantic_inputs={"core_hypothesis": formalized["core_hypothesis"]},
        entities=tuple(set_a + (set_b or [])),
        evidence=tuple(evidence),
        contextual_state=context,
        expected_outputs=tuple(field.name for field in ANALYST_SPEC.output_contract.fields),
    )


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
