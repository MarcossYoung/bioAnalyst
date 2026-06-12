"""Stage-3 branch-rate estimation boundary.

The production estimator lives behind the Stage-0 genomics container.  This
module keeps the Python side small and testable: normalize branch lengths into
relative-rate vectors, coerce container JSON into a stable shape, and fail
closed when the container substrate is unavailable in this checkout.
"""
from __future__ import annotations

import math
from typing import Mapping


BRANCH_RATE_SOURCE = "iqtree_fixed_topology_relative_branch_rates"


def relative_rates(branch_lengths: Mapping[str, float | int | str | None]) -> dict[str, float]:
    """Remove the gene-wide rate by dividing each branch length by the mean."""
    clean: dict[str, float] = {}
    for branch, value in (branch_lengths or {}).items():
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v >= 0:
            clean[str(branch)] = v
    if not clean:
        return {}
    mean = sum(clean.values()) / len(clean)
    if mean <= 0:
        return {branch: 0.0 for branch in clean}
    return {branch: value / mean for branch, value in clean.items()}


def coerce_container_result(gene: str, payload: dict | None, aligner: str = "mafft") -> dict:
    """Normalize the container response into the branch-rate data contract."""
    payload = payload or {}
    status = payload.get("status") or ("computed" if payload.get("branch_lengths") or payload.get("rates") else "unavailable")
    raw_rates = payload.get("rates") or relative_rates(payload.get("branch_lengths") or {})
    rates = relative_rates(raw_rates) if raw_rates else {}
    return {
        "gene": gene,
        "aligner": aligner,
        "status": "computed" if rates and status == "computed" else status,
        "rates": rates,
        "branch_count": len(rates),
        "source": BRANCH_RATE_SOURCE,
        "tool_versions": payload.get("tool_versions") or {},
        "provenance": payload.get("provenance") or {},
        "error": payload.get("error"),
    }


def estimate_branch_rates(
    *,
    gene: str,
    alignment: str | dict | None,
    tree: str | dict | None,
    aligner: str = "mafft",
    use_cache: bool = True,
) -> dict:
    """Call Stage-0 container IQ-TREE branch-length estimation when available.

    This repository snapshot does not include the Stage-0 container module.  In
    that case the function returns ``status=unavailable`` rather than fabricating
    rates. Tests can exercise the downstream ERC layer by passing synthetic
    ``branch_rate_data`` directly into ``genomic_data.build_data``.
    """
    if not alignment or not tree:
        return {
            "gene": gene,
            "aligner": aligner,
            "status": "unavailable",
            "rates": {},
            "branch_count": 0,
            "source": BRANCH_RATE_SOURCE,
            "error": "missing Stage-1 codon MSA or fixed species tree",
        }
    try:
        from .. import genomics_container  # type: ignore
    except Exception as exc:
        return {
            "gene": gene,
            "aligner": aligner,
            "status": "unavailable",
            "rates": {},
            "branch_count": 0,
            "source": BRANCH_RATE_SOURCE,
            "error": f"Stage-0 genomics container unavailable: {type(exc).__name__}",
        }

    payload = genomics_container.run_tool(
        "iqtree_branch_lengths",
        {
            "gene": gene,
            "alignment": alignment,
            "tree": tree,
            "topology": "fixed",
            "aligner": aligner,
        },
        use_cache=use_cache,
    )
    return coerce_container_result(gene, payload, aligner=aligner)


def estimate_aligner_pair(
    *,
    gene: str,
    mafft_alignment: str | dict | None,
    prank_alignment: str | dict | None,
    tree: str | dict | None,
    use_cache: bool = True,
) -> dict:
    """Estimate MAFFT and PRANK branch-rate vectors for aligner sensitivity."""
    return {
        "mafft": estimate_branch_rates(
            gene=gene,
            alignment=mafft_alignment,
            tree=tree,
            aligner="mafft",
            use_cache=use_cache,
        ),
        "prank": estimate_branch_rates(
            gene=gene,
            alignment=prank_alignment,
            tree=tree,
            aligner="prank",
            use_cache=use_cache,
        ),
    }
