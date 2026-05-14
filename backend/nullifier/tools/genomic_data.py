"""Turn raw Ensembl per-gene records + a gene-set expansion into the typed
``data`` dict consumed by ``tools.compute.run_analysis_plan``.

Pure helpers — no LLM, no network, no file I/O.
"""
from statistics import mean


METRICS = ("dnds", "ortholog_count", "paralog_count", "duplication_count",
           "regulatory_feature_count")


def per_gene_metrics(gene_data: dict) -> dict:
    """gene_symbol -> {metric -> value or None}."""
    out: dict = {}
    for g, d in (gene_data or {}).items():
        if not isinstance(d, dict) or "_error" in d:
            out[g] = {m: None for m in METRICS}
            continue
        dnds_vals = [o["dnds"] for o in (d.get("orthologs") or [])
                     if o.get("dnds") is not None and o["dnds"] < 10]
        out[g] = {
            "dnds": mean(dnds_vals) if dnds_vals else None,
            "ortholog_count": len(d.get("orthologs") or []),
            "paralog_count": len(d.get("paralogs") or []),
            "duplication_count": (d.get("gene_tree") or {}).get("duplication_count", 0),
            "regulatory_feature_count": len(d.get("regulatory_features") or []),
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


def build_data(gene_data: dict, expansion: dict, exclude: set | None = None) -> dict:
    """Build the ``data`` dict shape ``compute.run_analysis_plan`` expects.

    groups: one per starter / expanded.<set> / controls.<set>, each carrying every
    metric in METRICS as an aligned per-gene list (None for genes Ensembl missed).
    variables: same metrics, but as a single aligned vector across ``gene_index``
    (the union of all set genes), so xy-tests (spearman/pearson) can run across genes.
    """
    excl = {g.upper() for g in (exclude or set())}
    per = per_gene_metrics(gene_data)

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

    return {
        "groups": groups,
        "variables": variables,
        "gene_index": gene_index,
        "tables": {},
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
