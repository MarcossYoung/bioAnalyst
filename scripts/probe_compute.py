"""Small live probe for Nullifier's genomic compute path.

Run from the repo root:
    $env:PYTHONPATH="backend"; python scripts/probe_compute.py
"""

from __future__ import annotations

import statistics
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from nullifier.agents import analyst
from nullifier.tools import dnds, ensembl, gnomad, phylo


GENES = ["SYP", "SYNGAP1", "CAMK2A", "DLG4", "MFSD2A", "SLC2A1", "SPOCK1"]
TARGET_GENES = 1750
STARTER_GENES = len(GENES)


def timed(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[float, Any, Exception | None]:
    t0 = time.perf_counter()
    try:
        out = fn(*args, **kwargs)
        return time.perf_counter() - t0, out, None
    except Exception as exc:
        return time.perf_counter() - t0, None, exc


def mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def fmt_seconds(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"
    if seconds < 60:
        return f"{seconds:.2f} s"
    return f"{seconds / 60:.2f} min"


def one2one_count(orthologs: list[dict]) -> int:
    return sum(
        1
        for ortholog in orthologs or []
        if "one2one" in str(ortholog.get("ortholog_type") or "").lower()
    )


def phase_ensembl() -> tuple[dict, dict, list[str]]:
    cold: dict[str, list[float]] = {"lookup": [], "orthologs": [], "paralogs": []}
    warm: dict[str, list[float]] = {"lookup": [], "orthologs": [], "paralogs": []}
    failures: list[str] = []
    gene_data: dict[str, dict] = {}

    print("Phase A: Ensembl cold/warm endpoint timings")
    for sym in GENES:
        dt, info, err = timed(ensembl.lookup_gene, sym, use_cache=False)
        cold["lookup"].append(dt)
        if err:
            failures.append(f"{sym} lookup cold: {err}")
        info = info or {}

        dt, orthologs, err = timed(ensembl.get_orthologs, sym, use_cache=False)
        cold["orthologs"].append(dt)
        if err:
            failures.append(f"{sym} orthologs cold: {err}")
        orthologs = orthologs or []

        dt, paralogs, err = timed(ensembl.get_paralogs, sym, use_cache=False)
        cold["paralogs"].append(dt)
        if err:
            failures.append(f"{sym} paralogs cold: {err}")
        paralogs = paralogs or []

        dt, _, err = timed(ensembl.lookup_gene, sym, use_cache=True)
        warm["lookup"].append(dt)
        if err:
            failures.append(f"{sym} lookup warm: {err}")

        dt, _, err = timed(ensembl.get_orthologs, sym, use_cache=True)
        warm["orthologs"].append(dt)
        if err:
            failures.append(f"{sym} orthologs warm: {err}")

        dt, _, err = timed(ensembl.get_paralogs, sym, use_cache=True)
        warm["paralogs"].append(dt)
        if err:
            failures.append(f"{sym} paralogs warm: {err}")

        gene_data[sym] = {
            "info": info,
            "orthologs": orthologs,
            "paralogs": paralogs,
            "_homology_source": "symbol",
        }
        print(
            f"  {sym:8} ENSG={info.get('ensembl_id') or 'NA':15} "
            f"orthologs={len(orthologs):3d} one2one={one2one_count(orthologs):3d} "
            f"paralogs={len(paralogs):2d}"
        )

    return gene_data, {"cold": cold, "warm": warm}, failures


def phase_gnomad_phylo(gene_data: dict) -> tuple[dict, list[str]]:
    timings: dict[str, list[float]] = {"gnomad": [], "phylo": []}
    failures: list[str] = []

    print("\nPhase B: gnomAD + phylo timings")
    for sym in GENES:
        ensg = ((gene_data.get(sym) or {}).get("info") or {}).get("ensembl_id")
        dt, constraint, err = timed(gnomad.fetch_constraint, ensg)
        timings["gnomad"].append(dt)
        if err:
            failures.append(f"{sym} gnomAD: {err}")

        dt, age, err = timed(phylo.lookup_phylo_age, sym)
        timings["phylo"].append(dt)
        if err:
            failures.append(f"{sym} phylo: {err}")

        print(
            f"  {sym:8} gnomAD={fmt_seconds(timings['gnomad'][-1]):>9} "
            f"loeuf={(constraint or {}).get('loeuf')} "
            f"phylo={fmt_seconds(timings['phylo'][-1]):>9} "
            f"age={(age or {}).get('taxon_name') or 'NA'}"
        )

    return timings, failures


def phase_rdnds(gene_data: dict) -> tuple[dict, list[str]]:
    counters = defaultdict(int)
    original_codon_align = dnds.codon_align
    original_ng86 = dnds.ng86

    def counted_codon_align(*args: Any, **kwargs: Any) -> Any:
        out = original_codon_align(*args, **kwargs)
        counters["align_attempts"] += 1
        if out:
            counters["aligned_pairs"] += 1
        return out

    def counted_ng86(*args: Any, **kwargs: Any) -> Any:
        out = original_ng86(*args, **kwargs)
        counters["ng86_calls"] += 1
        if isinstance(out, dict) and out.get("dnds") is not None:
            counters["scored_pairs"] += 1
        return out

    failures: list[str] = []
    print("\nPhase C: analyst._fetch_rdnds_data timing")
    dnds.codon_align = counted_codon_align
    dnds.ng86 = counted_ng86
    try:
        dt, rdnds_data, err = timed(analyst._fetch_rdnds_data, gene_data, GENES, use_cache=True)
    finally:
        dnds.codon_align = original_codon_align
        dnds.ng86 = original_ng86

    if err:
        failures.append(f"_fetch_rdnds_data: {err}")
        rdnds_data = {}
    species_scored = sum(len(v or {}) for v in (rdnds_data or {}).values())
    result = {
        "wall_seconds": dt,
        "one2one_orthologs": sum(one2one_count((gene_data.get(sym) or {}).get("orthologs") or []) for sym in GENES),
        "align_attempts": counters["align_attempts"],
        "aligned_pairs": counters["aligned_pairs"],
        "ng86_calls": counters["ng86_calls"],
        "scored_pairs": counters["scored_pairs"],
        "species_values": species_scored,
    }
    print(
        f"  wall={fmt_seconds(dt)} one2one={result['one2one_orthologs']} "
        f"aligned={result['aligned_pairs']} scored={result['scored_pairs']} "
        f"species_values={result['species_values']}"
    )
    return result, failures


def extrapolate(ensembl_timings: dict, gnomad_phylo: dict, rdnds_result: dict) -> dict:
    """Apply measured means to the stated 1,750-gene request model.

    Request model:
      pre-filter: lookup x 1,750
      light fetch: lookup + orthologs for non-starters
      full fetch: lookup + orthologs + paralogs + one extra measured endpoint proxy for starters
      gnomAD + phylo: all genes
    """
    estimates = {}
    non_starters = TARGET_GENES - STARTER_GENES
    for mode in ("cold", "warm"):
        endpoint = {name: mean(values) for name, values in ensembl_timings[mode].items()}
        extra_full_proxy = mean(list(endpoint.values()))
        ensembl_seconds = (
            endpoint["lookup"] * TARGET_GENES
            + (endpoint["lookup"] + endpoint["orthologs"]) * non_starters
            + (endpoint["lookup"] + endpoint["orthologs"] + endpoint["paralogs"] + extra_full_proxy) * STARTER_GENES
        )
        gnomad_phylo_seconds = (mean(gnomad_phylo["gnomad"]) + mean(gnomad_phylo["phylo"])) * TARGET_GENES
        rdnds_seconds = (rdnds_result.get("wall_seconds") or 0.0) * (STARTER_GENES / len(GENES))
        total = ensembl_seconds + gnomad_phylo_seconds + rdnds_seconds
        estimates[mode] = {
            "ensembl_seconds": ensembl_seconds,
            "gnomad_phylo_seconds": gnomad_phylo_seconds,
            "rdnds_seconds": rdnds_seconds,
            "total_seconds": total,
        }
    return estimates


def print_summary(
    ensembl_timings: dict,
    gnomad_phylo: dict,
    rdnds_result: dict,
    estimates: dict,
    failures: list[str],
) -> None:
    print("\nSummary")
    print(f"  measured_at: {datetime.now().isoformat(timespec='seconds')}")
    print("  Ensembl mean latency per endpoint:")
    for endpoint in ("lookup", "orthologs", "paralogs"):
        print(
            f"    {endpoint:9} cold={fmt_seconds(mean(ensembl_timings['cold'][endpoint])):>9} "
            f"warm={fmt_seconds(mean(ensembl_timings['warm'][endpoint])):>9}"
        )
    print(
        f"  gnomAD mean={fmt_seconds(mean(gnomad_phylo['gnomad']))}; "
        f"phylo mean={fmt_seconds(mean(gnomad_phylo['phylo']))}"
    )
    print(
        f"  dN/dS wall={fmt_seconds(rdnds_result.get('wall_seconds') or 0.0)}; "
        f"aligned={rdnds_result.get('aligned_pairs', 0)}; "
        f"scored={rdnds_result.get('scored_pairs', 0)}"
    )
    print("  1,750-gene extrapolation:")
    for mode in ("cold", "warm"):
        est = estimates[mode]
        print(
            f"    {mode:4} total={fmt_seconds(est['total_seconds'])} "
            f"(Ensembl {fmt_seconds(est['ensembl_seconds'])}, "
            f"gnomAD+phylo {fmt_seconds(est['gnomad_phylo_seconds'])}, "
            f"dN/dS {fmt_seconds(est['rdnds_seconds'])})"
        )
    if failures:
        print("  Failures:")
        for failure in failures:
            print(f"    - {failure}")


def main() -> None:
    failures: list[str] = []
    gene_data, ensembl_timings, phase_failures = phase_ensembl()
    failures.extend(phase_failures)

    gnomad_phylo, phase_failures = phase_gnomad_phylo(gene_data)
    failures.extend(phase_failures)

    rdnds_result, phase_failures = phase_rdnds(gene_data)
    failures.extend(phase_failures)

    estimates = extrapolate(ensembl_timings, gnomad_phylo, rdnds_result)
    print_summary(ensembl_timings, gnomad_phylo, rdnds_result, estimates, failures)


if __name__ == "__main__":
    main()
