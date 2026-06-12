"""Phenotype axes used by secondary phylogenetic association checks."""
from __future__ import annotations

import csv
import math
from pathlib import Path


CORTICAL_NEURON_TRAIT = "cortical_neurons"
CORTICAL_NEURON_LABEL = "Cortical neuron number"
CORTICAL_NEURON_MIN_SPECIES = 20

ASSOCIATION_ONLY_GUARD = (
    "RERconverge rate-phenotype correlations are association-consistent with "
    "shared drivers, not directional or causal co-evolution."
)

PRIMATE_SPECIES = {
    "homo_sapiens",
    "pan_troglodytes",
    "gorilla_gorilla",
    "pongo_abelii",
    "macaca_mulatta",
    "papio_anubis",
    "callithrix_jacchus",
    "microcebus_murinus",
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_CORTICAL_NEURON_PATH = DATA_DIR / "phenotypes" / "cortical_neurons_mammals.tsv"


def load_cortical_neurons(path: str | Path | None = None) -> dict[str, dict]:
    """Load the curated cortical-neuron phenotype table keyed by species."""
    path = Path(path) if path else DEFAULT_CORTICAL_NEURON_PATH
    if not path.exists():
        return {}

    out: dict[str, dict] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = (line for line in fh if line.strip() and not line.startswith("#"))
        reader = csv.DictReader(rows, delimiter="\t")
        for row in reader:
            species = str(row.get("species") or "").strip().lower()
            if not species:
                continue
            raw_value = _float_or_none(row.get("cortical_neurons_millions"))
            log_value = _float_or_none(row.get("log10_cortical_neurons"))
            if log_value is None and raw_value is not None and raw_value > 0:
                log_value = math.log10(raw_value)
            out[species] = {
                "species": species,
                "cortical_neurons_millions": raw_value,
                "log10_cortical_neurons": log_value,
                "source": row.get("source") or "",
                "method": row.get("method") or "",
                "quality": row.get("quality") or "",
                "clade": row.get("clade") or "",
                "note": row.get("note") or "",
            }
    return out


def build_cortical_neuron_axis(
    panel: list[str] | None = None,
    *,
    path: str | Path | None = None,
    min_species: int = CORTICAL_NEURON_MIN_SPECIES,
) -> dict:
    """Build a panel-aligned log10 cortical-neuron axis with coverage flags."""
    records = load_cortical_neurons(path)
    panel = [str(s).lower() for s in (panel or sorted(records))]
    values: list[float | None] = []
    raw_values: list[float | None] = []
    by_species: dict[str, dict] = {}
    missing_species: list[str] = []
    usable_species: list[str] = []
    quality_counts: dict[str, int] = {}

    for species in panel:
        rec = records.get(species) or {}
        value = rec.get("log10_cortical_neurons")
        raw = rec.get("cortical_neurons_millions")
        values.append(value if isinstance(value, float) else None)
        raw_values.append(raw if isinstance(raw, float) else None)
        if value is None:
            missing_species.append(species)
            by_species[species] = {"available": False}
            continue
        usable_species.append(species)
        quality = str(rec.get("quality") or "unspecified")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
        by_species[species] = {**rec, "available": True, "transformation": "log10_millions"}

    primates = [s for s in usable_species if s in PRIMATE_SPECIES]
    non_primates = [s for s in usable_species if s not in PRIMATE_SPECIES]
    usable_n = len(usable_species)
    available = usable_n >= int(min_species)
    reason = None if available else (
        f"only {usable_n} panel species have cortical-neuron counts; need >= {int(min_species)}"
    )

    return {
        "name": CORTICAL_NEURON_TRAIT,
        "label": CORTICAL_NEURON_LABEL,
        "units": "log10(cortical neurons in millions)",
        "raw_units": "millions of cortical neurons",
        "panel": panel,
        "values": values,
        "raw_values": raw_values,
        "by_species": by_species,
        "usable_species": usable_n,
        "usable_species_names": usable_species,
        "missing_species": missing_species,
        "min_species": int(min_species),
        "available": available,
        "underpowered": not available,
        "reason": reason,
        "primate_species": primates,
        "non_primate_species": non_primates,
        "primate_coverage": len(primates),
        "non_primate_coverage": len(non_primates),
        "quality_counts": quality_counts,
        "source_note": (
            "Compiled mammal cortical-neuron fixture derived from Herculano-Houzel "
            "isotropic-fractionator summaries; rows carry quality labels and should "
            "be treated as a curated phenotype axis, not newly inferred values."
        ),
        "citations": [
            "Herculano-Houzel cortical neuron datasets and isotropic fractionator summaries",
        ],
        "overclaim_guard": ASSOCIATION_ONLY_GUARD,
    }


def _float_or_none(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None
