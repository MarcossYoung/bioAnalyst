from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean, median, stdev

from ..tools.llm_client import llm_call_json
from ..tools import ensembl
from ..tools.gnomad import fetch_constraint
from ..tools.phylo import lookup_phylo_age
from ..tools.genomic_data import build_data, retrievable_summary
from ..tools.rerconverge import run_rerconverge
from ..tools.diagnostics import run_diagnostics, summarize_set_risk
from ..tools.panels import mammal_panel
from ..tools.compute import verify_reported_stats, _data_summary
from ..config.loader import load_config
from ..provenance import make_provenance
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
    """Compute pairwise dN/dS from homology protein alignments + CDS."""
    from ..tools.dnds import codon_align, ng86

    protein_ids = []
    for sym in genes:
        for ortholog in (gene_data.get(sym, {}) or {}).get("orthologs") or []:
            if "one2one" not in str(ortholog.get("ortholog_type") or "").lower():
                continue
            protein_ids.extend([
                ortholog.get("source_protein_id"),
                ortholog.get("target_protein_id"),
            ])
    protein_ids = [pid for pid in dict.fromkeys(protein_ids) if pid]

    cds_by_protein: dict[str, str | None] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(ensembl.resolve_cds_for_protein, pid, use_cache): pid
            for pid in protein_ids
        }
        for fut in as_completed(futures):
            pid = futures[fut]
            try:
                cds_by_protein[pid] = fut.result()
            except Exception:
                cds_by_protein[pid] = None

    fallback_to_r = bool(load_config().get("r", {}).get("pairwise_dnds_fallback", False))
    r_bridge = None
    if fallback_to_r:
        from ..tools import r_bridge as _r_bridge
        r_bridge = _r_bridge

    results: dict = {}
    for sym in genes:
        if on_event:
            on_event(ev.rdnds_gene_started(sym))
        species_values: dict[str, float] = {}
        for ortholog in (gene_data.get(sym, {}) or {}).get("orthologs") or []:
            if "one2one" not in str(ortholog.get("ortholog_type") or "").lower():
                continue
            species = str(ortholog.get("target_species") or "").lower()
            if not species:
                continue
            aligned = codon_align(
                ortholog.get("source_align_seq"),
                ortholog.get("target_align_seq"),
                cds_by_protein.get(ortholog.get("source_protein_id")),
                cds_by_protein.get(ortholog.get("target_protein_id")),
            )
            if not aligned:
                continue
            estimate = ng86(aligned[0], aligned[1])
            if estimate.get("dnds") is not None:
                species_values[species] = float(estimate["dnds"])
        if not species_values and fallback_to_r:
            ensg = ((gene_data.get(sym) or {}).get("info") or {}).get("ensembl_id")
            aligned_tree = ensembl.fetch_gene_tree_aligned(ensg, use_cache=use_cache) if ensg else None
            species_values = r_bridge.pairwise_dnds(
                (aligned_tree or {}).get("sequences") or {},
                reference="Homo_sapiens",
                use_cache=use_cache,
            ) or {}
        results[sym] = species_values or None
        if on_event:
            on_event(ev.rdnds_gene_complete(sym, len(species_values or {})))
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
                ortholog["dnds_source"] = "homology_pal2nal_ng86"
                attached += 1
            elif ortholog.get("dnds") is not None and not ortholog.get("dnds_source"):
                ortholog["dnds_source"] = "ensembl_compara_dn_ds"
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


def _unique_genes(genes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for gene in genes or []:
        if not isinstance(gene, str) or not gene.strip():
            continue
        key = gene.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(gene)
    return out


def _syngo_ensembl_by_symbol(use_cache: bool = True) -> dict[str, str]:
    try:
        from ..tools.gene_sets import load_syngo
        syngo = load_syngo(use_cache=use_cache)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for record in syngo.get("genes") or []:
        symbol = record.get("hgnc_symbol")
        ensg = record.get("ensembl_id")
        if symbol and ensg:
            out[str(symbol).upper()] = str(ensg)
    return out


def _resolve_ensembl_ids_for_screen(
    targets: list[str],
    use_cache: bool,
    on_event=None,
) -> dict[str, str]:
    syngo_ids = _syngo_ensembl_by_symbol(use_cache=use_cache)
    resolved: dict[str, str] = {}
    for gene in targets:
        upper = gene.upper()
        if upper.startswith("ENSG"):
            resolved[gene] = gene
        elif upper in syngo_ids:
            resolved[gene] = syngo_ids[upper]

    for gene in targets:
        if gene in resolved:
            continue
        info = ensembl.lookup_gene(gene, use_cache=use_cache)
        if not info or not info.get("ensembl_id"):
            continue
        resolved[gene] = info["ensembl_id"]
        resolved_from = info.get("_resolved_from")
        if resolved_from and on_event:
            on_event(ev.analyst_symbol_resolved(resolved_from, info["symbol"]))
    return resolved


def _screen_comparable(
    targets: list[str],
    expansion: dict,
    panel: list[str],
    min_panel_species: int,
    use_cache: bool,
    on_event=None,
) -> tuple[list[str], dict]:
    targets = _unique_genes(targets)
    starters = {g.upper() for g in (expansion.get("starter") or [])}
    target_to_ensg = _resolve_ensembl_ids_for_screen(targets, use_cache, on_event=on_event)
    ensg_to_targets: dict[str, list[str]] = {}
    for gene, ensg in target_to_ensg.items():
        ensg_to_targets.setdefault(ensg, []).append(gene)

    coverage_by_id = ensembl.screen_panel_coverage_by_id_batch(
        list(ensg_to_targets.keys()),
        panel,
        use_cache=use_cache,
    )

    kept: list[str] = []
    genes: list[dict] = []
    for gene in targets:
        ensg = target_to_ensg.get(gene)
        coverage = coverage_by_id.get(ensg) if ensg else None
        panel_species = sorted(str(s) for s in ((coverage or {}).get("panel_species") or set()))
        species_count = int((coverage or {}).get("species_count") or len(panel_species))
        is_starter = gene.upper() in starters
        keep = is_starter or species_count >= min_panel_species
        if keep:
            kept.append(gene)
        genes.append({
            "gene": gene,
            "ensembl_id": ensg,
            "species_count": species_count,
            "panel_species": panel_species,
            "starter": is_starter,
            "kept": keep,
            "reason": "starter" if is_starter else (
                "meets_threshold" if keep else "below_panel_coverage_threshold"
            ),
        })

    report = {
        "enabled": True,
        "total": len(targets),
        "kept": len(kept),
        "dropped": len(targets) - len(kept),
        "threshold": min_panel_species,
        "panel_size": len(panel or []),
        "genes": genes,
        "provenance": make_provenance(
            source="analyst.comparability_screen",
            triggered_by=["gene_sets.all_genes", "tools.panels.mammal_panel"],
            evidence_refs=["Ensembl Compara condensed homology"],
            method=(
                "Resolve target genes to Ensembl IDs; batch-query condensed ortholog "
                f"coverage; keep non-starters with at least {min_panel_species} panel species."
            ),
            confidence=0.85,
            inputs={
                "target_count": len(targets),
                "panel_size": len(panel or []),
                "min_panel_species": min_panel_species,
            },
        ),
    }
    return kept, report


def _filter_expansion_to_targets(expansion: dict, kept_targets: list[str]) -> dict:
    kept = {g.upper() for g in kept_targets}

    def _filter_pool(pool):
        return [g for g in (pool or []) if isinstance(g, str) and g.upper() in kept]

    filtered = dict(expansion)
    filtered["starter"] = _filter_pool(expansion.get("starter") or [])
    filtered["expanded"] = {
        name: _filter_pool(genes)
        for name, genes in (expansion.get("expanded") or {}).items()
    }
    filtered["controls"] = {
        name: _filter_pool(genes)
        for name, genes in (expansion.get("controls") or {}).items()
    }
    filtered["background"] = {
        name: _filter_pool(genes)
        for name, genes in (expansion.get("background") or {}).items()
    }
    filtered["total_expanded"] = sum(len(v) for v in filtered["expanded"].values())
    filtered["total_controls"] = sum(len(v) for v in filtered["controls"].values())
    return filtered


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

    all_targets = _unique_genes(list(all_targets))
    cfg = load_config()
    analyst_cfg = cfg.get("analyst", {})
    if analyst_cfg.get("comparability_screen", True):
        panel = mammal_panel()
        min_panel_species = int(analyst_cfg.get("min_panel_species", 6))
        all_targets, screen_report = _screen_comparable(
            all_targets,
            expansion,
            panel,
            min_panel_species,
            use_cache,
            on_event=_emit,
        )
        filtered_expansion = _filter_expansion_to_targets(expansion, all_targets)
        filtered_expansion["comparability_screen"] = screen_report
        expansion.clear()
        expansion.update(filtered_expansion)
        _emit(ev.analyst_comparability_screen(
            screen_report["total"],
            screen_report["kept"],
            screen_report["dropped"],
            screen_report["threshold"],
        ))

    limited_targets = _limit_targets(all_targets, expansion)
    if len(limited_targets) != len(all_targets):
        filtered_expansion = _filter_expansion_to_targets(expansion, limited_targets)
        expansion.clear()
        expansion.update(filtered_expansion)
    all_targets = limited_targets
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

    min_low_risk_genes = int(analyst_cfg.get("min_low_risk_genes", 2))
    diagnostics = run_diagnostics(gene_data, panel=mammal_panel(), on_event=_emit)

    data = build_data(
        gene_data,
        expansion,
        gnomad_data=gnomad_data,
        phylo_data=phylo_data,
        paml_data=paml_data,
        rdnds_data=rdnds_data,
        diagnostics=diagnostics,
        min_low_risk_genes=min_low_risk_genes,
    )
    if "phenotype_association" in _claim_constructs(formalized):
        phenotype_axis = (data.get("phenotypes") or {}).get("cortical_neurons") or {}
        rerconverge_data = run_rerconverge(
            rate_vectors=data.get("rate_vectors") or {},
            trait_axis=phenotype_axis,
            sets=_phenotype_sets(expansion),
            controls=[f"controls.{name}" for name in sorted((expansion.get("controls") or {}))],
            min_species=int(phenotype_axis.get("min_species") or 20),
            use_cache=use_cache,
        )
        data["rerconverge"] = rerconverge_data
        data.setdefault("provenance", {})["rerconverge"] = {
            "status": rerconverge_data.get("status"),
            "secondary": True,
            "trait": rerconverge_data.get("trait"),
            "underpowered": rerconverge_data.get("underpowered"),
            "primate_confounded": rerconverge_data.get("primate_confounded"),
            "overclaim_guard": rerconverge_data.get("overclaim_guard"),
        }
    risk_filter = (data.get("rate_vectors") or {}).get("risk_filter") or {}
    for gene, scored in (risk_filter.get("genes") or {}).items():
        _emit(ev.diagnostics_risk_scored(
            gene,
            scored.get("risk"),
            scored.get("tier"),
            scored.get("reasons") or [],
        ))
    for set_name, summary in (risk_filter.get("sets") or {}).items():
        _emit(ev.diagnostics_risk_survival_summary(set_name, summary))

    set_a, set_b = _split_into_sets(formalized, starter_entities)
    set_a_stats = _set_statistics(set_a, gene_data, paml_data, diagnostics, min_low_risk_genes) if set_a else None
    set_b_stats = _set_statistics(set_b, gene_data, paml_data, diagnostics, min_low_risk_genes) if set_b else None
    dnds_saturation = _set_usability(set_a_stats, set_b_stats)
    cross_set = _cross_set_analysis(set_a, set_b, gene_data, dnds_saturation) if (set_a and set_b) else None

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
        "diagnostics": diagnostics,
        "risk_filter": risk_filter,
        "set_a": set_a,
        "set_b": set_b,
        "set_a_stats": set_a_stats,
        "set_b_stats": set_b_stats,
        "dnds_saturation": dnds_saturation,
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


def _claim_constructs(formalized: dict) -> set[str]:
    claims = (formalized or {}).get("atomic_claims") or []
    constructs = {
        str((claim or {}).get("construct") or "set_difference")
        for claim in claims
        if isinstance(claim, dict)
    }
    return constructs or {"set_difference"}


def _phenotype_sets(expansion: dict) -> list[str]:
    out = ["starter"]
    names = list((expansion or {}).get("expanded") or {})
    bbb = [name for name in names if "bbb" in name.lower()]
    chosen = sorted(bbb or names)[0] if names else None
    if chosen:
        out.append(f"expanded.{chosen}")
    return out


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


def _set_statistics(
    genes: list[str],
    gene_data: dict,
    paml_data: dict | None = None,
    diagnostics: dict | None = None,
    min_low_risk_genes: int = 2,
) -> dict:
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
        "orthologs_without_computable_dnds": 0,
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
            dnds = o.get("dnds")
            if dnds is None:
                dnds_diag["orthologs_without_computable_dnds"] += 1
                continue
            if dnds >= 10:
                dnds_diag["orthologs_filtered_high"] += 1
                continue
            dnds_values.append(dnds)
            dnds_diag["orthologs_with_dnds"] += 1
            source = o.get("dnds_source") or "unknown"
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
    dnds_coverage_fraction = (
        len(dnds_values) / dnds_diag["orthologs_total"]
        if dnds_diag["orthologs_total"] else 0.0
    )
    dnds_low_coverage = dnds_diag["orthologs_total"] > 0 and len(dnds_values) < max(3, len(valid))
    dnds_degenerate_low = bool(
        dnds_values
        and median(dnds_values) < 0.05
        and dnds_coverage_fraction < 0.25
    )
    dnds_degraded = bool(
        (dnds_values and dnds_saturation_fraction > 0.5)
        or dnds_low_coverage
        or dnds_degenerate_low
    )
    if dnds_saturation_fraction > 0.5:
        unusable_reason = "Most computable dN/dS values are pinned near 1.0."
    elif dnds_low_coverage:
        unusable_reason = "Too few orthologs have computable dN/dS for this set."
    elif dnds_degenerate_low:
        unusable_reason = "dN/dS values are degenerate-low with poor coverage."
    else:
        unusable_reason = ""
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
        "dnds_coverage_fraction": dnds_coverage_fraction,
        "dnds_low_coverage": dnds_low_coverage,
        "dnds_degenerate_low": dnds_degenerate_low,
        "dnds_degraded": dnds_degraded,
        "dnds_usable": not dnds_degraded,
        "dnds_unusable_reason": unusable_reason,
        "dnds_diagnostics": dnds_diag,
        "risk_summary": summarize_set_risk(valid, diagnostics, min_low_risk_genes),
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


def _set_usability(set_a_stats: dict | None, set_b_stats: dict | None) -> dict:
    stats = [("set_a", set_a_stats), ("set_b", set_b_stats)]
    dnds_degraded = [
        {"set": name, "reason": s.get("dnds_unusable_reason") or "dN/dS set is degraded"}
        for name, s in stats
        if s and s.get("dnds_degraded")
    ]
    risk_degraded = [
        {"set": name, "reason": "risk filter left too few scorable genes"}
        for name, s in stats
        if s and (s.get("risk_summary") or {}).get("risk_degraded")
    ]
    degraded = risk_degraded + dnds_degraded
    fractions = [
        float(s.get("dnds_saturation_fraction", 0.0))
        for _, s in stats
        if s and s.get("dnds_n", 0)
    ]
    max_fraction = max(fractions) if fractions else 0.0
    reason = "; ".join(f"{d['set']}: {d['reason']}" for d in degraded)
    return {
        "flag": bool(degraded),
        "max_fraction": max_fraction,
        "threshold": 0.5,
        "reason": reason,
        "sets": {
            name: {
                "usable": bool(
                    s
                    and not s.get("dnds_degraded")
                    and not (s.get("risk_summary") or {}).get("risk_degraded")
                ),
                "reason": (
                    "risk filter left too few scorable genes"
                    if (s or {}).get("risk_summary", {}).get("risk_degraded")
                    else (s or {}).get("dnds_unusable_reason", "")
                ),
                "dnds_n": (s or {}).get("dnds_n", 0),
                "dnds_saturation_fraction": (s or {}).get("dnds_saturation_fraction", 0.0),
                "dnds_degraded": bool((s or {}).get("dnds_degraded")),
                "risk_degraded": bool((s or {}).get("risk_summary", {}).get("risk_degraded")),
                "risk_summary": (s or {}).get("risk_summary"),
            }
            for name, s in stats
        },
        "cross_set_allowed": not degraded,
    }


def _combine_saturation_flags(*stats: dict | None) -> dict:
    first = stats[0] if len(stats) > 0 else None
    second = stats[1] if len(stats) > 1 else None
    return _set_usability(first, second)


def _cross_set_analysis(set_a: list[str], set_b: list[str], gene_data: dict, usability: dict | None = None) -> dict:
    if usability and not usability.get("cross_set_allowed", True):
        return {
            "skipped": True,
            "skip_reason": usability.get("reason") or "one or more sets have degraded dN/dS data",
        }

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
