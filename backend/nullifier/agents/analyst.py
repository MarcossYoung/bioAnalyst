from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean, stdev

from ..tools.llm_client import llm_call_json
from ..tools import ensembl
from ..tools.gnomad import fetch_constraint
from ..tools.phylo import lookup_phylo_age
from ..tools.genomic_data import build_data, retrievable_summary
from ..tools.compute import verify_reported_stats, _data_summary
from ..config.loader import load_config
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
    on_event=None,
) -> dict:
    from ..tools import paml
    results = {}
    for sym in starter_genes:
        if on_event:
            on_event(ev.paml_gene_started(sym, foreground))
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
        result = results[sym]
        if on_event and result.get("status") == "timeout":
            on_event(ev.paml_gene_timeout(sym))
        elif on_event and result.get("status") == "computed":
            on_event(ev.paml_gene_complete(
                sym,
                result.get("omega_foreground"),
                result.get("omega_background"),
                result.get("lrt_pvalue"),
            ))
    return results


def _fetch_rdnds_data(
    gene_data: dict,
    genes: list[str],
    use_cache: bool = True,
    on_event=None,
) -> dict:
    """Fetch Compara alignments in parallel, then run seqinr::kaks serially."""
    from ..tools import r_bridge

    jobs = {}
    for sym in genes:
        d = gene_data.get(sym, {})
        if "_error" in d:
            continue
        ensg = (d.get("info") or {}).get("ensembl_id")
        if ensg:
            jobs[sym] = ensg

    alignments: dict[str, dict | None] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(ensembl.fetch_gene_tree_aligned, ensg, use_cache): sym
            for sym, ensg in jobs.items()
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                alignments[sym] = fut.result()
            except Exception:
                alignments[sym] = None

    results: dict = {}
    for sym in genes:
        aligned = alignments.get(sym)
        if not aligned:
            results[sym] = None
            continue
        if on_event:
            on_event(ev.rdnds_gene_started(sym))
        dnds = r_bridge.pairwise_dnds(
            aligned.get("sequences") or {},
            reference="Homo_sapiens",
            use_cache=use_cache,
        )
        results[sym] = dnds
        if on_event:
            on_event(ev.rdnds_gene_complete(sym, len(dnds or {})))
    return results


def _attach_rdnds_to_orthologs(gene_data: dict, rdnds_data: dict) -> int:
    attached = 0
    for gene, species_values in (rdnds_data or {}).items():
        if not species_values:
            continue
        record = gene_data.get(gene) or {}
        for ortholog in record.get("orthologs") or []:
            target = str(ortholog.get("target_species") or "").lower()
            if ortholog.get("dnds") is None and target in species_values:
                ortholog["dnds"] = species_values[target]
                ortholog["dnds_source"] = "r_seqinr_kaks"
                attached += 1
            elif ortholog.get("dnds") is not None and not ortholog.get("dnds_source"):
                ortholog["dnds_source"] = "ensembl_compara"
    return attached


def _limit_targets(all_targets: list, expansion: dict) -> list:
    cfg = load_config().get("ensembl", {})
    limit = int(cfg.get("max_genes_for_full_analysis", 500))
    if limit <= 0 or len(all_targets) <= limit:
        return all_targets
    starters = list(expansion.get("starter") or [])
    slots = max(limit - len(starters), 0)
    pools = []
    pools.extend((expansion.get("expanded") or {}).values())
    pools.extend((expansion.get("controls") or {}).values())
    per_pool = max(1, slots // max(len(pools), 1)) if slots else 0
    selected = list(starters)
    for pool in pools:
        for gene in list(pool)[:per_pool]:
            if gene not in selected and len(selected) < limit:
                selected.append(gene)
    for gene in all_targets:
        if len(selected) >= limit:
            break
        if gene not in selected:
            selected.append(gene)
    return selected


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

    all_targets = _limit_targets(list(all_targets), expansion)
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

    paml_data = _fetch_paml_data(gene_data, list(starter_entities), use_cache=use_cache, on_event=_emit)
    _emit(ev.analyst_paml_complete(
        sum(1 for v in paml_data.values() if v.get("status") == "computed"),
        len(starter_entities),
    ))

    rdnds_data = _fetch_rdnds_data(gene_data, all_targets, use_cache=use_cache, on_event=_emit)
    rdnds_attached = _attach_rdnds_to_orthologs(gene_data, rdnds_data)
    _emit(ev.analyst_rdnds_complete(
        sum(1 for v in rdnds_data.values() if v),
        len(all_targets),
        rdnds_attached,
    ))

    data = build_data(
        gene_data,
        expansion,
        gnomad_data=gnomad_data,
        phylo_data=phylo_data,
        paml_data=paml_data,
        rdnds_data=rdnds_data,
    )

    set_a, set_b = _split_into_sets(formalized, starter_entities)
    set_a_stats = _set_statistics(set_a, gene_data, paml_data) if set_a else None
    set_b_stats = _set_statistics(set_b, gene_data, paml_data) if set_b else None
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
        "rdnds_data": rdnds_data,
        "set_a": set_a,
        "set_b": set_b,
        "set_a_stats": set_a_stats,
        "set_b_stats": set_b_stats,
        "dnds_saturation": _combine_saturation_flags(set_a_stats, set_b_stats),
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
    ensg_inputs = [g for g in genes if isinstance(g, str) and g.upper().startswith("ENSG")]
    id_lookup = ensembl.lookup_genes_by_id_batch(
        ensg_inputs,
        use_cache=use_cache,
        on_progress=(lambda n, total: on_event(ev.ensembl_batch_progress(n, total)) if on_event else None),
    ) if ensg_inputs else {}
    id_orthologs = ensembl.fetch_orthologs_by_id_batch(
        ensg_inputs,
        use_cache=use_cache,
        on_progress=(lambda n, total: on_event(ev.ensembl_batch_progress(n, total)) if on_event else None),
    ) if ensg_inputs else {}

    def _full(g: str) -> tuple[str, dict]:
        if g in id_lookup:
            raw = id_lookup[g]
            info = _record_from_lookup_id(g, raw)
        else:
            info = ensembl.lookup_gene(g, use_cache)
        if not info:
            if on_gene:
                on_gene(g, "error")
            return g, {"_error": "not found in Ensembl"}
        resolved_from = info.get("_resolved_from")
        if resolved_from and on_event:
            on_event(ev.analyst_symbol_resolved(resolved_from, info["symbol"]))
        canonical = info["symbol"]
        orthologs = id_orthologs.get(g) if g in id_orthologs else ensembl.get_orthologs(canonical, use_cache=use_cache)
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
        if g in id_lookup:
            raw = id_lookup[g]
            info = _record_from_lookup_id(g, raw)
        else:
            info = ensembl.lookup_gene(g, use_cache)
        if not info:
            if on_gene:
                on_gene(g, "error")
            return g, {"_error": "not found in Ensembl"}
        resolved_from = info.get("_resolved_from")
        if resolved_from and on_event:
            on_event(ev.analyst_symbol_resolved(resolved_from, info["symbol"]))
        canonical = info["symbol"]
        orthologs = id_orthologs.get(g) if g in id_orthologs else ensembl.get_orthologs(canonical, use_cache=use_cache)
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


def _record_from_lookup_id(query: str, data: dict) -> dict:
    return {
        "symbol": data.get("display_name") or data.get("external_name") or query,
        "ensembl_id": data.get("id") or query,
        "biotype": data.get("biotype"),
        "chromosome": data.get("seq_region_name"),
        "start": data.get("start"),
        "end": data.get("end"),
        "strand": data.get("strand"),
        "description": data.get("description"),
    }


def _set_statistics(genes: list[str], gene_data: dict, paml_data: dict | None = None) -> dict:
    valid = [g for g in genes if "_error" not in gene_data.get(g, {})]
    if not valid:
        return {"valid_gene_count": 0}

    ortholog_counts = [len(gene_data[g]["orthologs"]) for g in valid]
    paralog_counts = [len(gene_data[g]["paralogs"]) for g in valid]
    duplication_counts = [(gene_data[g]["gene_tree"] or {}).get("duplication_count", 0) for g in valid]

    dnds_values = []
    omega_values = []
    acceleration_ratios = []
    dnds_diag = {
        "genes_with_orthologs": 0,
        "genes_with_dnds": 0,
        "orthologs_total": 0,
        "orthologs_with_dnds": 0,
        "orthologs_missing_dn": 0,
        "orthologs_missing_ds": 0,
        "orthologs_invalid_ds": 0,
        "orthologs_filtered_high": 0,
        "dnds_source_counts": {},
    }
    for g in valid:
        gene_has_dnds = False
        orthologs = gene_data[g]["orthologs"]
        if orthologs:
            dnds_diag["genes_with_orthologs"] += 1
        dnds_diag["orthologs_total"] += len(orthologs)
        for o in gene_data[g]["orthologs"]:
            ds = o.get("ds")
            if o.get("dn") is None:
                dnds_diag["orthologs_missing_dn"] += 1
            if ds is None:
                dnds_diag["orthologs_missing_ds"] += 1
            elif ds <= 0:
                dnds_diag["orthologs_invalid_ds"] += 1
            dnds = o.get("dnds")
            if dnds is None:
                continue
            if dnds >= 10:
                dnds_diag["orthologs_filtered_high"] += 1
                continue
            dnds_values.append(dnds)
            dnds_diag["orthologs_with_dnds"] += 1
            source = o.get("dnds_source") or "ensembl_compara"
            dnds_diag["dnds_source_counts"][source] = dnds_diag["dnds_source_counts"].get(source, 0) + 1
            gene_has_dnds = True
        if gene_has_dnds:
            dnds_diag["genes_with_dnds"] += 1
        paml_result = (paml_data or {}).get(g) or {}
        if paml_result.get("status") == "computed":
            if paml_result.get("omega_foreground") is not None:
                omega_values.append(paml_result["omega_foreground"])
            if paml_result.get("acceleration_ratio") is not None:
                acceleration_ratios.append(paml_result["acceleration_ratio"])

    dnds_saturation_fraction = (
        sum(1 for v in dnds_values if abs(float(v) - 1.0) < 0.01) / len(dnds_values)
        if dnds_values else 0.0
    )
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
        "dnds_saturation_fraction": dnds_saturation_fraction,
        "dnds_saturation_flag": bool(dnds_values and dnds_saturation_fraction > 0.5),
        "dnds_diagnostics": dnds_diag,
        "omega_foreground_n": len(omega_values),
        "omega_foreground_mean": mean(omega_values) if omega_values else None,
        "acceleration_ratio_n": len(acceleration_ratios),
        "acceleration_ratio_mean": mean(acceleration_ratios) if acceleration_ratios else None,
        "foreground_label": next(
            (v.get("foreground_label") or v.get("foreground_group")
             for v in (paml_data or {}).values()
             if isinstance(v, dict) and v.get("status") == "computed"),
            None,
        ),
    }


def _combine_saturation_flags(*stats: dict | None) -> dict:
    saturated = [s for s in stats if s and s.get("dnds_saturation_flag")]
    fractions = [
        float(s.get("dnds_saturation_fraction", 0.0))
        for s in stats
        if s and s.get("dnds_n", 0)
    ]
    max_fraction = max(fractions) if fractions else 0.0
    return {
        "flag": bool(saturated),
        "max_fraction": max_fraction,
        "threshold": 0.5,
        "reason": "Most usable dN/dS values are pinned near 1.0; treat genomic axis as low-confidence/untestable."
        if saturated else "",
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
