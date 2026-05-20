from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean, stdev

from ..tools.llm_client import llm_call_json
from ..tools import ensembl
from ..tools.gnomad import fetch_constraint
from ..tools.phylo import lookup_phylo_age
from ..tools.genomic_data import build_data, retrievable_summary
from ..tools.compute import verify_reported_stats, _data_summary
from .. import events as ev
from .semantic import AgentSpec, OutputContract, OutputField, TaskObject


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


def _fetch_gnomad_data(gene_data: dict) -> dict:
    jobs = {sym: (d or {}).get("ensembl_id") for sym, d in gene_data.items()}
    results: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(fetch_constraint, ensg): sym
                   for sym, ensg in jobs.items() if ensg}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return results


def _fetch_phylo_data(gene_data: dict) -> dict:
    return {sym: lookup_phylo_age(sym) for sym in gene_data}


def _fetch_paml_data(
    gene_data: dict,
    starter_genes: list[str],
    foreground: str = "primates",
    use_cache: bool = True,
) -> dict:
    from ..tools import paml
    results = {}
    for sym in starter_genes:
        d = gene_data.get(sym, {})
        if "_error" in d:
            results[sym] = {"status": "error", "note": d["_error"]}
            continue
        ensg = (d.get("info") or {}).get("ensembl_id")
        if not ensg:
            results[sym] = {"status": "error", "note": "no ensembl_id"}
            continue
        aligned = ensembl.fetch_gene_tree_aligned(ensg, use_cache=use_cache)
        if not aligned:
            results[sym] = {"status": "no_compara_alignment", "gene": sym}
            continue
        results[sym] = paml.run_branch_model(
            ensg, sym, aligned, foreground=foreground, use_cache=use_cache
        )
    return results


def run_analyst(
    all_targets: list,
    expansion: dict,
    formalized: dict,
    starter_entities: list,
    completed_analysis: list,
    use_cache: bool = True,
    on_event=None,
) -> dict:
    """Fetch all genomic data and build the typed data dict.

    Returns {gene_data, data, data_summary, gnomad_data, phylo_data,
             set_a, set_b, set_a_stats, set_b_stats, cross_set, reproducibility}.
    Emits events via on_event callback throughout.
    """
    def _emit(e):
        if on_event:
            on_event(e)

    _emit(ev.analyst_started(len(all_targets)))

    gene_data = _fetch_all_gene_data(
        all_targets, use_cache=use_cache,
        on_gene=lambda g, s: _emit(ev.analyst_gene_fetched(g, s)),
        starter_genes=set(expansion.get("starter", [])),
        on_event=_emit,
    )

    gnomad_data = _fetch_gnomad_data(gene_data)
    _emit(ev.analyst_gnomad_fetched(
        sum(1 for v in gnomad_data.values() if v and v.get("loeuf") is not None),
        len(gene_data),
    ))

    phylo_data = _fetch_phylo_data(gene_data)
    _emit(ev.analyst_phylo_loaded(
        sum(1 for v in phylo_data.values() if v and v.get("phylostratum") is not None),
        len(gene_data),
    ))

    paml_data = _fetch_paml_data(gene_data, list(starter_entities), use_cache=use_cache)
    _emit(ev.analyst_paml_complete(
        sum(1 for v in paml_data.values() if v.get("status") == "computed"),
        len(starter_entities),
    ))

    data = build_data(gene_data, expansion, gnomad_data=gnomad_data, phylo_data=phylo_data)

    set_a, set_b = _split_into_sets(formalized, starter_entities)
    set_a_stats = _set_statistics(set_a, gene_data) if set_a else None
    set_b_stats = _set_statistics(set_b, gene_data) if set_b else None
    cross_set = _cross_set_analysis(set_a, set_b, gene_data) if (set_a and set_b) else None

    reproducibility = None
    if completed_analysis:
        _emit(ev.analyst_reproducibility_check_start(len(completed_analysis)))
        reproducibility = verify_reported_stats(
            completed_analysis,
            retrievable_summary(gene_data),
        )
        _emit(ev.analyst_reproducibility_check_complete(
            reproducibility.get("verifiable_count", 0),
            reproducibility.get("total", len(completed_analysis)),
        ))

    return {
        "gene_data": gene_data,
        "data": data,
        "data_summary": _data_summary(data),
        "gnomad_data": gnomad_data,
        "phylo_data": phylo_data,
        "paml_data": paml_data,
        "set_a": set_a,
        "set_b": set_b,
        "set_a_stats": set_a_stats,
        "set_b_stats": set_b_stats,
        "cross_set": cross_set,
        "reproducibility": reproducibility,
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
        _homology_source = "symbol"
        if not orthologs and info.get("ensembl_id"):
            ensg_id = info["ensembl_id"]
            orthologs = ensembl.fetch_orthologs_by_id(ensg_id, use_cache=use_cache)
            if orthologs:
                _homology_source = "ensg_fallback"
            else:
                meta = ensembl.fetch_compara_metadata(ensg_id, use_cache=use_cache)
                _homology_source = "not_in_compara" if not (meta or {}).get("in_compara") else "no_mammal_orthologs"
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
            "_homology_source": _homology_source,
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
        _homology_source = "symbol"
        if not orthologs and info.get("ensembl_id"):
            ensg_id = info["ensembl_id"]
            orthologs = ensembl.fetch_orthologs_by_id(ensg_id, use_cache=use_cache)
            if orthologs:
                _homology_source = "ensg_fallback"
            else:
                meta = ensembl.fetch_compara_metadata(ensg_id, use_cache=use_cache)
                _homology_source = "not_in_compara" if not (meta or {}).get("in_compara") else "no_mammal_orthologs"
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
            "_homology_source": _homology_source,
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
