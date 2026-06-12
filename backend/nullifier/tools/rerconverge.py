"""Stage-4 RERconverge container boundary.

RERconverge is intentionally secondary in this pipeline: ERC carries the
verdict for V-gene coordinated-rate claims, while rate/phenotype association is
reported as corroborative and guarded against causal language.
"""
from __future__ import annotations

from .phenotypes import (
    ASSOCIATION_ONLY_GUARD,
    CORTICAL_NEURON_MIN_SPECIES,
    CORTICAL_NEURON_TRAIT,
)


RERCONVERGE_SOURCE = "rerconverge_container"


def coerce_container_result(payload: dict | None) -> dict:
    """Normalize container JSON into the compute-layer RERconverge contract."""
    payload = payload or {}
    status = payload.get("status") or (
        "computed" if payload.get("set_results") or payload.get("control_results") else "unavailable"
    )
    set_results = _coerce_result_map(payload.get("set_results") or payload.get("sets"))
    control_results = _coerce_result_map(payload.get("control_results") or payload.get("controls"))
    primate_out = _coerce_result_map(payload.get("primate_out_results") or payload.get("primate_out"))
    underpowered = bool(payload.get("underpowered", False))
    primate_confounded = payload.get("primate_confounded")
    if primate_confounded is not None:
        primate_confounded = bool(primate_confounded)

    return {
        "status": "computed" if status == "computed" and set_results else status,
        "trait": payload.get("trait") or CORTICAL_NEURON_TRAIT,
        "set_results": set_results,
        "control_results": control_results,
        "primate_out_results": primate_out,
        "underpowered": underpowered,
        "primate_confounded": primate_confounded,
        "secondary": True,
        "method": payload.get("method") or "RERconverge rate-phenotype correlation",
        "source": payload.get("source") or RERCONVERGE_SOURCE,
        "tool_versions": payload.get("tool_versions") or {},
        "provenance": payload.get("provenance") or {},
        "error": payload.get("error"),
        "overclaim_guard": ASSOCIATION_ONLY_GUARD,
    }


def run_rerconverge(
    *,
    rate_vectors: dict | None,
    trait_axis: dict | None = None,
    sets: list[str] | None = None,
    controls: list[str] | None = None,
    min_species: int = CORTICAL_NEURON_MIN_SPECIES,
    use_cache: bool = True,
) -> dict:
    """Call Stage-0 container RERconverge when available; otherwise fail closed."""
    rate_vectors = rate_vectors or {}
    trait_axis = trait_axis or {}
    if not rate_vectors.get("rates") or not rate_vectors.get("sets"):
        return _unavailable("rate_vectors data unavailable", trait_axis=trait_axis)
    if not trait_axis.get("available"):
        return _unavailable(
            trait_axis.get("reason") or "cortical-neuron phenotype axis unavailable",
            trait_axis=trait_axis,
            underpowered=bool(trait_axis.get("underpowered", True)),
        )
    try:
        from .. import genomics_container  # type: ignore
    except Exception as exc:
        return _unavailable(
            f"Stage-0 genomics container unavailable: {type(exc).__name__}",
            trait_axis=trait_axis,
        )

    payload = genomics_container.run_tool(
        "rerconverge",
        {
            "rate_vectors": rate_vectors,
            "trait_axis": trait_axis,
            "sets": sets or [],
            "controls": controls or [],
            "trait": trait_axis.get("name") or CORTICAL_NEURON_TRAIT,
            "min_species": int(min_species),
            "require_primate_out": True,
        },
        use_cache=use_cache,
    )
    return coerce_container_result(payload)


def _unavailable(reason: str, *, trait_axis: dict | None = None, underpowered: bool = False) -> dict:
    trait_axis = trait_axis or {}
    return {
        "status": "unavailable",
        "trait": trait_axis.get("name") or CORTICAL_NEURON_TRAIT,
        "set_results": {},
        "control_results": {},
        "primate_out_results": {},
        "underpowered": bool(underpowered),
        "primate_confounded": None,
        "secondary": True,
        "method": "RERconverge rate-phenotype correlation",
        "source": RERCONVERGE_SOURCE,
        "tool_versions": {},
        "provenance": {
            "usable_species": trait_axis.get("usable_species"),
            "min_species": trait_axis.get("min_species"),
            "primate_coverage": trait_axis.get("primate_coverage"),
            "non_primate_coverage": trait_axis.get("non_primate_coverage"),
        },
        "error": reason,
        "overclaim_guard": ASSOCIATION_ONLY_GUARD,
    }


def _coerce_result_map(value) -> dict:
    if not isinstance(value, dict):
        return {}
    out: dict[str, dict] = {}
    for name, result in value.items():
        if not isinstance(result, dict):
            continue
        out[str(name)] = {
            **result,
            "set": result.get("set") or str(name),
            "r": _float_or_none(result.get("r", result.get("correlation"))),
            "p_value": _float_or_none(result.get("p_value", result.get("p"))),
            "n": _int_or_none(result.get("n", result.get("species_count"))),
        }
    return out


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
