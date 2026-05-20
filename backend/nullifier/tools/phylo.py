"""Gene phylostratigraphy lookup — Liebeskind et al. 2016 consensus ages.

Maps HGNC gene symbols to phylostratum (int, 1=oldest) and taxonomic level.
Returns None when a gene is absent from the dataset — never throws.
"""
import csv
from pathlib import Path

_DATA_PATH = Path(__file__).parent.parent / "data" / "liebeskind2016_gene_ages.tsv"
_DATASET_VERSION = "liebeskind_2016"

_table: dict | None = None  # lazy singleton; keys are uppercase symbols


def _load() -> dict:
    global _table
    if _table is not None:
        return _table
    if not _DATA_PATH.exists():
        _table = {}
        return _table
    out: dict = {}
    with open(_DATA_PATH, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            sym = (row.get("symbol") or "").strip().upper()
            try:
                ps = int(row.get("phylostratum", ""))
            except ValueError:
                continue
            taxon = (row.get("taxon_name") or "").strip()
            if sym:
                out[sym] = {"phylostratum": ps, "taxon_name": taxon}
    _table = out
    return _table


def lookup_phylo_age(symbol: str) -> dict | None:
    """Return {phylostratum, taxon_name, _source, _version} or None."""
    if not symbol:
        return None
    row = _load().get(symbol.upper())
    if row is None:
        return None
    return {**row, "_source": "phylostratigraphy", "_version": _DATASET_VERSION}
