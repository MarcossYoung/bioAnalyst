"""Turn raw Ensembl per-gene records + a gene-set expansion into the typed
``data`` dict consumed by ``tools.compute.run_analysis_plan``.

Pure helpers — no LLM, no network, no file I/O.
"""
from statistics import mean

from .panels import mammal_panel
from .diagnostics import (
    FP_RISK_CALIBRATION_STATE,
    FP_RISK_DISCLAIMER,
    FP_RISK_WEIGHTS,
    RISK_TIER_EXCLUDED,
    RISK_TIER_FLAGGED,
    diagnostics_to_dict,
    score_record,
)
from .branch_rates import BRANCH_RATE_SOURCE
from .phenotypes import build_cortical_neuron_axis


METRICS = ("dnds", "ortholog_count", "paralog_count", "duplication_count",
           "regulatory_feature_count", "loeuf", "pli", "phylo_age",
           "omega_foreground", "omega_background", "acceleration_ratio")


def per_gene_metrics(gene_data: dict, gnomad_data: dict | None = None,
                     phylo_data: dict | None = None,
                     paml_data: dict | None = None) -> dict:
    """gene_symbol -> {metric -> value or None}."""
    out: dict = {}
    for g, d in (gene_data or {}).items():
        if not isinstance(d, dict) or "_error" in d:
            out[g] = {m: None for m in METRICS}
            continue
        dnds_vals = [o["dnds"] for o in (d.get("orthologs") or [])
                     if o.get("dnds") is not None and o["dnds"] < 10]
        constraint = (gnomad_data or {}).get(g) or {}
        phylo = (phylo_data or {}).get(g) or {}
        paml = (paml_data or {}).get(g) or {}
        out[g] = {
            "dnds": mean(dnds_vals) if dnds_vals else None,
            "ortholog_count": len(d.get("orthologs") or []),
            "paralog_count": len(d.get("paralogs") or []),
            "duplication_count": (d.get("gene_tree") or {}).get("duplication_count", 0),
            "regulatory_feature_count": len(d.get("regulatory_features") or []),
            "loeuf": constraint.get("loeuf"),
            "pli": constraint.get("pli"),
            "phylo_age": phylo.get("phylostratum"),
            "omega_foreground": paml.get("omega_foreground") if paml.get("status") == "computed" else None,
            "omega_background": paml.get("omega_background") if paml.get("status") == "computed" else None,
            "acceleration_ratio": paml.get("acceleration_ratio") if paml.get("status") == "computed" else None,
        }
    return out


def _all_genes_in_order(expansion: dict) -> list:
    seen: set = set()
    out: list = []
    pools = [expansion.get("starter") or []]
    pools.extend((expansion.get("expanded") or {}).values())
    pools.extend((expansion.get("controls") or {}).values())
    for pool in pools:
        for g in pool:
            key = g.upper()
            if key in seen:
                continue
            seen.add(key)
            out.append(g)
    return out


def _one2one_panel_species(record: dict, panel_set: set[str]) -> set[str]:
    out: set[str] = set()
    for ortholog in (record or {}).get("orthologs") or []:
        species = str(ortholog.get("target_species") or "").lower()
        orth_type = str(ortholog.get("ortholog_type") or "").lower()
        if species in panel_set and "one2one" in orth_type:
            out.add(species)
    return out


def _rate_value(value, *, drop_saturated: bool = True):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0 or v >= 10:
        return None
    if drop_saturated and abs(v - 1.0) < 0.01:
        return None
    return v


def _coerce_branch_rate_mapping(value) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    raw = value.get("rates") if isinstance(value.get("rates"), dict) else value
    out: dict[str, float] = {}
    for branch, rate in (raw or {}).items():
        cleaned = _rate_value(rate, drop_saturated=False)
        if cleaned is not None:
            out[str(branch)] = cleaned
    return out


def _branch_rate_panel(branch_rate_data: dict | None, panel: list[str] | None = None) -> list[str]:
    if panel:
        return [str(s).lower() for s in panel]
    seen: set[str] = set()
    out: list[str] = []
    for result in (branch_rate_data or {}).values():
        for branch in _coerce_branch_rate_mapping(result):
            key = str(branch).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def per_gene_rate_vectors(
    gene_data: dict,
    expansion: dict,
    rdnds_data: dict | None = None,
    branch_rate_data: dict | None = None,
    panel: list[str] | None = None,
    diagnostics: dict | None = None,
    min_low_risk_genes: int = 2,
) -> dict:
    """Build panel-aligned per-lineage rate vectors for mirrortree-lite/ERC.

    This is deliberately parallel to scalar ``per_gene_metrics``: it does not
    collapse across branches. When Stage-3 branch rates are supplied, they are
    used directly. Otherwise the v7 NG86 one-to-one ortholog vectors are used as
    a lower-tier mirrortree cross-check.
    """
    using_branch_rates = bool(branch_rate_data)
    panel = _branch_rate_panel(branch_rate_data, panel) if using_branch_rates else [str(s).lower() for s in (panel or mammal_panel())]
    panel_set = set(panel)
    rates: dict[str, list[float | None]] = {}
    coverage: dict[str, dict] = {}
    risk_filter = {
        "calibration_state": FP_RISK_CALIBRATION_STATE,
        "disclaimer": FP_RISK_DISCLAIMER,
        "weights": dict(FP_RISK_WEIGHTS),
        "min_low_risk_genes": int(min_low_risk_genes),
        "genes": {},
        "flagged_genes": [],
        "excluded_genes": [],
    }

    all_genes: list[str] = []
    for pool in (
        [expansion.get("starter") or []]
        + list((expansion.get("expanded") or {}).values())
        + list((expansion.get("controls") or {}).values())
        + list((expansion.get("background") or {}).values())
    ):
        for gene in pool:
            if gene not in all_genes:
                all_genes.append(gene)

    excluded_by_risk: set[str] = set()
    for gene in all_genes:
        record = (diagnostics or {}).get(gene)
        if record is None:
            continue
        scored = score_record(record)
        scored["diagnostics"] = diagnostics_to_dict(record)
        risk_filter["genes"][gene] = scored
        if scored["tier"] == RISK_TIER_EXCLUDED:
            excluded_by_risk.add(gene)
            risk_filter["excluded_genes"].append(gene)
        elif scored["tier"] == RISK_TIER_FLAGGED:
            risk_filter["flagged_genes"].append(gene)

    for gene in all_genes:
        if gene in excluded_by_risk:
            coverage[gene] = {
                "one2one_panel_species": 0,
                "usable_rates": 0,
                "risk_excluded": True,
                "risk": risk_filter["genes"].get(gene),
            }
            continue
        record = gene_data.get(gene) or {}
        if using_branch_rates:
            branch_values = {
                str(k).lower(): v
                for k, v in _coerce_branch_rate_mapping((branch_rate_data or {}).get(gene)).items()
            }
            vector = [_rate_value(branch_values.get(branch), drop_saturated=False) for branch in panel]
            one2one_count = None
        else:
            species_values = {str(k).lower(): v for k, v in ((rdnds_data or {}).get(gene) or {}).items()}
            allowed_species = _one2one_panel_species(record, panel_set)
            vector = [
                _rate_value(species_values.get(species)) if species in allowed_species else None
                for species in panel
            ]
            one2one_count = len(allowed_species)
        rates[gene] = vector
        coverage[gene] = {
            "one2one_panel_species": one2one_count,
            "usable_rates": sum(1 for v in vector if v is not None),
            "risk": risk_filter["genes"].get(gene),
        }

    sets = {"starter": list(expansion.get("starter") or [])}
    for name, genes in (expansion.get("expanded") or {}).items():
        sets[f"expanded.{name}"] = list(genes)
    for name, genes in (expansion.get("controls") or {}).items():
        sets[f"controls.{name}"] = list(genes)
    for name, genes in (expansion.get("background") or {}).items():
        sets[name] = list(genes)

    filtered_sets = {
        name: [g for g in genes if g not in excluded_by_risk]
        for name, genes in sets.items()
    }

    set_usability = {}
    for name, genes in sets.items():
        gene_count = len(genes)
        survivors = filtered_sets.get(name) or []
        genes_with_rates = sum(1 for g in survivors if coverage.get(g, {}).get("usable_rates", 0) > 0)
        usable_rates = sum(int(coverage.get(g, {}).get("usable_rates", 0)) for g in survivors)
        risk_scored = [g for g in genes if g in risk_filter["genes"]]
        excluded_genes = [g for g in genes if g in excluded_by_risk]
        flagged_genes = [g for g in genes if g in risk_filter["flagged_genes"]]
        risk_degraded = bool(risk_scored and len(survivors) < int(min_low_risk_genes))
        rate_degraded = not (len(survivors) > 0 and genes_with_rates >= min(2, len(survivors)) and usable_rates >= 5)
        usable = not risk_degraded and not rate_degraded
        reason = ""
        if risk_degraded:
            reason = "too few genes survive FP-risk filter"
        elif rate_degraded:
            reason = (
                "too few genes/branches have computable branch rates"
                if using_branch_rates
                else "too few genes/species have computable non-saturated dN/dS rates"
            )
        set_usability[name] = {
            "usable": usable,
            "reason": reason,
            "gene_count": gene_count,
            "survivor_count": len(survivors),
            "genes_with_rates": genes_with_rates,
            "usable_rates": usable_rates,
            "dnds_degraded": rate_degraded,
            "rate_degraded": rate_degraded,
            "risk_degraded": risk_degraded,
            "risk_scored_count": len(risk_scored),
            "risk_flagged_genes": flagged_genes,
            "risk_excluded_genes": excluded_genes,
        }
        risk_filter.setdefault("sets", {})[name] = {
            "gene_count": gene_count,
            "survivor_count": len(survivors),
            "flagged_genes": flagged_genes,
            "excluded_genes": excluded_genes,
            "risk_degraded": risk_degraded,
            "min_low_risk_genes": int(min_low_risk_genes),
        }

    return {
        "panel": panel,
        "gene_index": [g for g in all_genes if g not in excluded_by_risk],
        "sets": filtered_sets,
        "original_sets": sets,
        "rates": rates,
        "coverage": coverage,
        "set_usability": set_usability,
        "risk_filter": risk_filter,
        "provenance": {
            "source": BRANCH_RATE_SOURCE if using_branch_rates else "homology_pal2nal_ng86",
            "ortholog_filter": "ortholog_one2one",
            "saturation_filter": "not applicable to model-based branch rates" if using_branch_rates else "drop abs(dnds - 1.0) < 0.01 and dnds >= 10",
            "background_set": "background.random_300",
            "estimator": "per-branch relative rates with gene-wide rate removed" if using_branch_rates else "pairwise NG86 dN/dS",
            "fp_risk": {
                "weights": dict(FP_RISK_WEIGHTS),
                "calibration_state": FP_RISK_CALIBRATION_STATE,
                "null_result_changes_with_aligner": "weight_not_applicable_until_stage_3",
            },
        },
    }


def build_data(gene_data: dict, expansion: dict, exclude: set | None = None,
               gnomad_data: dict | None = None, phylo_data: dict | None = None,
               paml_data: dict | None = None, rdnds_data: dict | None = None,
               branch_rate_data: dict | None = None,
               panel: list[str] | None = None, diagnostics: dict | None = None,
               min_low_risk_genes: int = 2,
               phenotype_axes: dict | None = None,
               rerconverge_data: dict | None = None) -> dict:
    """Build the ``data`` dict shape ``compute.run_analysis_plan`` expects.

    groups: one per starter / expanded.<set> / controls.<set>, each carrying every
    metric in METRICS as an aligned per-gene list (None for genes Ensembl missed).
    variables: same metrics, but as a single aligned vector across ``gene_index``
    (the union of all set genes), so xy-tests (spearman/pearson) can run across genes.
    """
    excl = {g.upper() for g in (exclude or set())}
    per = per_gene_metrics(gene_data, gnomad_data, phylo_data, paml_data)

    def _filtered(genes):
        return [g for g in (genes or []) if g.upper() not in excl]

    groups: dict = {}

    def _add(name: str, genes):
        kept = _filtered(genes)
        if not kept:
            return
        groups[name] = {m: [per.get(g, {}).get(m) for g in kept] for m in METRICS}

    _add("starter", expansion.get("starter") or [])
    for name, genes in (expansion.get("expanded") or {}).items():
        _add(f"expanded.{name}", genes)
    for name, genes in (expansion.get("controls") or {}).items():
        _add(f"controls.{name}", genes)

    gene_index = [g for g in _all_genes_in_order(expansion) if g.upper() not in excl]
    variables = {m: [per.get(g, {}).get(m) for g in gene_index] for m in METRICS}

    gnomad_n = sum(1 for v in (gnomad_data or {}).values()
                   if v and v.get("loeuf") is not None)
    phylo_n = sum(1 for v in (phylo_data or {}).values()
                  if v and v.get("phylostratum") is not None)
    paml_n = sum(1 for v in (paml_data or {}).values()
                 if v and v.get("status") == "computed")

    _src_counts: dict[str, int] = {"symbol": 0, "ensg_fallback": 0, "not_in_compara": 0, "no_mammal_orthologs": 0}
    _dnds_source_counts: dict[str, int] = {}
    for d in (gene_data or {}).values():
        src = (d or {}).get("_homology_source", "symbol")
        if src in _src_counts:
            _src_counts[src] += 1
        for ortholog in (d or {}).get("orthologs") or []:
            dnds = ortholog.get("dnds")
            if dnds is not None and dnds < 10:
                source = ortholog.get("dnds_source") or "unknown"
                _dnds_source_counts[source] = _dnds_source_counts.get(source, 0) + 1

    rate_vectors = per_gene_rate_vectors(
        gene_data,
        expansion,
        rdnds_data=rdnds_data,
        branch_rate_data=branch_rate_data,
        panel=panel,
        diagnostics=diagnostics,
        min_low_risk_genes=min_low_risk_genes,
    )
    phenotype_panel = list(rate_vectors.get("panel") or panel or [])
    phenotypes = phenotype_axes or {
        "cortical_neurons": build_cortical_neuron_axis(phenotype_panel)
    }
    cortical_axis = (phenotypes or {}).get("cortical_neurons") or {}

    return {
        "groups": groups,
        "variables": variables,
        "gene_index": gene_index,
        "rate_vectors": rate_vectors,
        "phenotypes": phenotypes,
        "rerconverge": rerconverge_data or {},
        "tables": {},
        "provenance": {
            "gnomad": {
                "source": "gnomad",
                "genome_build": "GRCh38",
                "genes_with_loeuf": gnomad_n,
                "total_genes": len(gene_index),
            } if gnomad_data else None,
            "phylo": {
                "source": "phylostratigraphy",
                "version": "liebeskind_2016",
                "genes_with_age": phylo_n,
                "total_genes": len(gene_index),
            } if phylo_data else None,
            "compara": {
                "source": "ensembl_compara",
                "genes_with_orthologs": _src_counts["symbol"] + _src_counts["ensg_fallback"],
                "genes_via_ensg_fallback": _src_counts["ensg_fallback"],
                "genes_not_in_compara": _src_counts["not_in_compara"],
                "dnds_source_counts": _dnds_source_counts,
                "total_genes": len(gene_index),
            },
            "paml": {
                "source": "paml_codeml",
                "genes_computed": paml_n,
                "total_genes": len(paml_data or {}),
            } if paml_data else None,
            "rate_vectors": {
                "source": (rate_vectors.get("provenance") or {}).get("source"),
                "panel_species": len(rate_vectors.get("panel") or []),
                "genes_with_usable_rates": sum(
                    1 for c in (rate_vectors.get("coverage") or {}).values()
                    if c.get("usable_rates", 0) > 0
                ),
                "background_genes": len(
                    (rate_vectors.get("sets") or {}).get("background.random_300", [])
                ),
                "fp_risk": {
                    "weights": dict(FP_RISK_WEIGHTS),
                    "calibration_state": FP_RISK_CALIBRATION_STATE,
                    "disclaimer": FP_RISK_DISCLAIMER,
                    "flagged_genes": (rate_vectors.get("risk_filter") or {}).get("flagged_genes", []),
                    "excluded_genes": (rate_vectors.get("risk_filter") or {}).get("excluded_genes", []),
                    "null_result_changes_with_aligner": "weight_not_applicable_until_stage_3",
                },
            } if (rdnds_data or branch_rate_data) else None,
            "phenotypes": {
                "cortical_neurons": {
                    "source": "Herculano-Houzel compiled isotropic-fractionator fixture",
                    "usable_species": cortical_axis.get("usable_species"),
                    "min_species": cortical_axis.get("min_species"),
                    "underpowered": cortical_axis.get("underpowered"),
                    "primate_coverage": cortical_axis.get("primate_coverage"),
                    "non_primate_coverage": cortical_axis.get("non_primate_coverage"),
                    "quality_counts": cortical_axis.get("quality_counts", {}),
                    "citations": cortical_axis.get("citations", []),
                    "overclaim_guard": cortical_axis.get("overclaim_guard"),
                }
            },
            "rerconverge": {
                "status": (rerconverge_data or {}).get("status"),
                "secondary": True,
                "trait": (rerconverge_data or {}).get("trait"),
                "underpowered": (rerconverge_data or {}).get("underpowered"),
                "primate_confounded": (rerconverge_data or {}).get("primate_confounded"),
                "overclaim_guard": (rerconverge_data or {}).get("overclaim_guard"),
            } if rerconverge_data else None,
            "fp_risk": {
                "weights": dict(FP_RISK_WEIGHTS),
                "calibration_state": FP_RISK_CALIBRATION_STATE,
                "disclaimer": FP_RISK_DISCLAIMER,
                "flagged_genes": (rate_vectors.get("risk_filter") or {}).get("flagged_genes", []),
                "excluded_genes": (rate_vectors.get("risk_filter") or {}).get("excluded_genes", []),
                "null_result_changes_with_aligner": "weight_not_applicable_until_stage_3",
            },
        },
    }


def retrievable_summary(gene_data: dict) -> dict:
    """Compact per-gene retrievability snapshot for the reproducibility check.

    Matches the shape ``verify_reported_stats`` and the Skeptic both expect:
    one entry per gene with ``available`` plus the simple Ensembl-derived counts.
    """
    out: dict = {}
    for g, d in (gene_data or {}).items():
        if not isinstance(d, dict) or "_error" in d:
            out[g] = {"available": False,
                      "reason": (d or {}).get("_error", "missing")}
            continue
        dnds_vals = [o["dnds"] for o in (d.get("orthologs") or [])
                     if o.get("dnds") is not None and o["dnds"] < 10]
        out[g] = {
            "available": True,
            "ortholog_count": len(d.get("orthologs") or []),
            "paralog_count": len(d.get("paralogs") or []),
            "duplication_count": (d.get("gene_tree") or {}).get("duplication_count", 0),
            "regulatory_feature_count": len(d.get("regulatory_features") or []),
            "mean_pairwise_dnds": round(mean(dnds_vals), 4) if dnds_vals else None,
        }
    return out
