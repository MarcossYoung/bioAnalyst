"""Tests for tools.phylo — phylostratigraphy lookup."""
import pytest

import nullifier.tools.phylo as phylo
from nullifier.tools.genomic_data import build_data, METRICS
from nullifier.tools.compute import _data_summary

# ── fixtures ────────────────────────────────────────────────────────────────

_FAKE_TABLE = {
    "ACTB": {"phylostratum": 1, "taxon_name": "Cellular_organisms"},
    "NRXN1": {"phylostratum": 6, "taxon_name": "Eumetazoa"},
    "BRCA1": {"phylostratum": 7, "taxon_name": "Vertebrata"},
}


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level singleton between tests."""
    original = phylo._table
    yield
    phylo._table = original


@pytest.fixture()
def patched_table():
    phylo._table = _FAKE_TABLE.copy()


# ── lookup tests ─────────────────────────────────────────────────────────────

def test_lookup_known_gene(patched_table):
    result = phylo.lookup_phylo_age("ACTB")
    assert result is not None
    assert result["phylostratum"] == 1
    assert result["taxon_name"] == "Cellular_organisms"
    assert result["_source"] == "phylostratigraphy"
    assert result["_version"] == "liebeskind_2016"


def test_lookup_missing_gene(patched_table):
    assert phylo.lookup_phylo_age("NOTAREAL") is None


def test_lookup_empty_symbol(patched_table):
    assert phylo.lookup_phylo_age("") is None


def test_lookup_case_insensitive(patched_table):
    result = phylo.lookup_phylo_age("actb")
    assert result is not None
    assert result["phylostratum"] == 1


def test_missing_data_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(phylo, "_table", None)
    monkeypatch.setattr(phylo, "_DATA_PATH", tmp_path / "nonexistent.tsv")
    result = phylo.lookup_phylo_age("ACTB")
    assert result is None
    assert phylo._table == {}


# ── genomic_data integration ──────────────────────────────────────────────────

def _minimal_gene_data(genes):
    return {g: {"orthologs": [], "paralogs": [], "gene_tree": {}, "regulatory_features": []}
            for g in genes}


def test_build_data_populates_phylo_age(patched_table):
    gene_data = _minimal_gene_data(["ACTB", "NRXN1", "BRCA1"])
    expansion = {
        "starter": ["ACTB"],
        "expanded": {"syngo": ["NRXN1"]},
        "controls": {"housekeeping": ["BRCA1"]},
    }
    phylo_data = {g: phylo.lookup_phylo_age(g) for g in gene_data}
    data = build_data(gene_data, expansion, phylo_data=phylo_data)

    assert "phylo_age" in METRICS
    assert data["groups"]["starter"]["phylo_age"] == [1]
    assert data["groups"]["expanded.syngo"]["phylo_age"] == [6]
    assert data["groups"]["controls.housekeeping"]["phylo_age"] == [7]

    prov = data["provenance"]["phylo"]
    assert prov is not None
    assert prov["genes_with_age"] == 3
    assert prov["total_genes"] == 3
    assert prov["version"] == "liebeskind_2016"


def test_data_summary_includes_phylo_coverage(patched_table):
    gene_data = _minimal_gene_data(["ACTB", "NRXN1"])
    expansion = {
        "starter": ["ACTB"],
        "expanded": {},
        "controls": {"ctrl": ["NRXN1"]},
    }
    phylo_data = {g: phylo.lookup_phylo_age(g) for g in gene_data}
    data = build_data(gene_data, expansion, phylo_data=phylo_data)
    summary = _data_summary(data)

    assert "phylo_coverage" in summary
    assert summary["phylo_coverage"]["genes_with_age"] == 2
    assert summary["phylo_coverage"]["total_genes"] == 2
