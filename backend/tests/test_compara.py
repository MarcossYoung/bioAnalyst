"""Tests for the three Compara metadata wrappers added in Day 4."""
from unittest.mock import MagicMock, patch

import pytest

from nullifier.tools import ensembl as e


def _mock_resp(body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


_FAKE_HOMOLOGY_BODY = {
    "data": [{
        "homologies": [
            {
                "type": "ortholog_one2one",
                "method_link_type": "ENSEMBL_ORTHOLOGUES",
                "dn_ds": 0.5,
                "source": {
                    "protein_id": "ENSP00000001234",
                    "align_seq": "MA",
                },
                "target": {
                    "species": "mus_musculus",
                    "id": "ENSMUSG00000001234",
                    "protein_id": "ENSMUSP00000001234",
                    "align_seq": "MA",
                    "perc_id": 95.0,
                    "perc_pos": 97.0,
                },
            }
        ]
    }]
}

_FAKE_CONDENSED_BODY = {
    "data": [{
        "homologies": [
            {"method_link_type": "ENSEMBL_ORTHOLOGUES"},
            {"method_link_type": "ENSEMBL_ORTHOLOGUES"},
        ]
    }]
}

_FAKE_ZERO_DN_BODY = {
    "data": [{
        "homologies": [
            {
                "type": "ortholog_one2one",
                "method_link_type": "ENSEMBL_ORTHOLOGUES",
                "dn_ds": 0.0,
                "target": {
                    "species": "mus_musculus",
                    "id": "ENSMUSG00000001234",
                    "protein_id": "ENSMUSP00000001234",
                    "perc_id": 95.0,
                    "perc_pos": 97.0,
                },
            }
        ]
    }]
}


# ── fetch_orthologs_by_id ────────────────────────────────────────────────────

def test_fetch_orthologs_by_id_cache_miss_then_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp(_FAKE_HOMOLOGY_BODY)) as mock_get:
        result1 = e.fetch_orthologs_by_id("ENSG00000000001")
        assert len(result1) == 1
        assert result1[0]["target_species"] == "mus_musculus"
        assert result1[0]["dnds"] == pytest.approx(0.5)
        assert result1[0]["dn_ds"] == 0.5
        assert result1[0]["source_protein_id"] == "ENSP00000001234"
        assert result1[0]["source_align_seq"] == "MA"
        assert result1[0]["method_link_type"] == "ENSEMBL_ORTHOLOGUES"
        assert mock_get.call_count == 1

        result2 = e.fetch_orthologs_by_id("ENSG00000000001")
        assert len(result2) == 1
        assert mock_get.call_count == 1  # cache hit, no second call


def test_fetch_orthologs_by_id_returns_empty_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get", side_effect=ConnectionError("down")):
        result = e.fetch_orthologs_by_id("ENSG00000000002")
        assert result == []


def test_fetch_orthologs_by_id_uses_fallback_base_url(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "fallback_base_urls": ["https://grch37.rest.ensembl.org"],
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch(
        "nullifier.tools.ensembl.requests.get",
        side_effect=[ConnectionError("down"), _mock_resp(_FAKE_HOMOLOGY_BODY)],
    ) as mock_get:
        result = e.fetch_orthologs_by_id("ENSG00000000002")

    assert len(result) == 1
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[0].args[0] == "https://rest.ensembl.org/homology/id/human/ENSG00000000002"
    assert mock_get.call_args_list[1].args[0] == "https://grch37.rest.ensembl.org/homology/id/human/ENSG00000000002"


def test_fetch_orthologs_by_id_empty_data(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp({"data": []})):
        result = e.fetch_orthologs_by_id("ENSG00000000003")
        assert result == []


# ── fetch_cds_sequence ───────────────────────────────────────────────────────

def test_fetch_orthologs_by_id_preserves_zero_dn_dnds(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp(_FAKE_ZERO_DN_BODY)):
        result = e.fetch_orthologs_by_id("ENSG00000000009")
        assert result[0]["dn_ds"] == 0.0
        assert result[0]["dnds"] == 0.0


def test_get_orthologs_preserves_zero_dn_dnds(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp(_FAKE_ZERO_DN_BODY)):
        result = e.get_orthologs("SHANK3")
        assert result[0]["dn_ds"] == 0.0
        assert result[0]["dnds"] == 0.0


def test_fetch_cds_sequence_returns_string(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp({"seq": "ATGCCCGGG"})):
        result = e.fetch_cds_sequence("ENSG00000000004")
        assert result == "ATGCCCGGG"


def test_fetch_cds_sequence_returns_none_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get", side_effect=ConnectionError("down")):
        result = e.fetch_cds_sequence("ENSG00000000005")
        assert result is None


def test_resolve_cds_for_protein_uses_parent_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch(
        "nullifier.tools.ensembl.requests.get",
        side_effect=[
            _mock_resp({"id": "ENSP0001", "Parent": "ENST0001"}),
            _mock_resp({"seq": "ATGCCCTAA"}),
        ],
    ) as mock_get:
        result = e.resolve_cds_for_protein("ENSP0001")

    assert result == "ATGCCCTAA"
    assert mock_get.call_args_list[0].args[0] == "https://rest.ensembl.org/lookup/id/ENSP0001"
    assert mock_get.call_args_list[1].args[0] == "https://rest.ensembl.org/sequence/id/ENST0001"


# ── fetch_compara_metadata ───────────────────────────────────────────────────

def test_fetch_gene_tree_aligned_uses_species_qualified_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    body = {
        "newick": "(Homo_sapiens,Pan_troglodytes);",
        "tree": {
            "children": [
                {
                    "taxonomy": {"scientific_name": "Homo sapiens"},
                    "sequence": {"mol_seq": {"seq": "ATG---AAA"}},
                },
                {
                    "taxonomy": {"scientific_name": "Pan troglodytes"},
                    "sequence": {"mol_seq": {"seq": "ATGCCC---"}},
                },
            ]
        },
    }
    with patch("nullifier.tools.ensembl.requests.get", return_value=_mock_resp(body)) as mock_get:
        result = e.fetch_gene_tree_aligned("ENSG00000000005")

    assert mock_get.call_args.args[0] == "https://rest.ensembl.org/genetree/member/id/human/ENSG00000000005"
    assert mock_get.call_args.kwargs["params"]["sequence"] == "cdna"
    assert result == {
        "newick": "(Homo_sapiens,Pan_troglodytes);",
        "sequences": {
            "Homo_sapiens": "ATG---AAA",
            "Pan_troglodytes": "ATGCCC---",
        },
    }


def test_fetch_compara_metadata_in_compara(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp(_FAKE_CONDENSED_BODY)):
        meta = e.fetch_compara_metadata("ENSG00000000006")
        assert meta["in_compara"] is True
        assert meta["species_count"] == 2
        assert "ENSEMBL_ORTHOLOGUES" in meta["method_link_types"]


def test_fetch_compara_metadata_not_in_compara(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get",
               return_value=_mock_resp({"data": []})):
        meta = e.fetch_compara_metadata("ENSG00000000007")
        assert meta["in_compara"] is False
        assert meta["species_count"] == 0
        assert meta["method_link_types"] == []


def test_fetch_compara_metadata_returns_not_in_compara_on_error(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    with patch("nullifier.tools.ensembl.requests.get", side_effect=ConnectionError("down")):
        meta = e.fetch_compara_metadata("ENSG00000000008")
        assert meta["in_compara"] is False


def test_fetch_compara_methods_flattens_grouped_response(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    body = {
        "Homology.homology": ["ENSEMBL_ORTHOLOGUES", "ENSEMBL_PARALOGUES"],
        "GenomicAlignTree.tree_alignment": ["EPO_EXTENDED"],
    }
    with patch("nullifier.tools.ensembl.requests.get", return_value=_mock_resp(body)) as mock_get:
        methods = e.fetch_compara_methods(class_filter="Homology", compara="vertebrates")

    assert methods == [
        {"category": "Homology.homology", "method": "ENSEMBL_ORTHOLOGUES"},
        {"category": "Homology.homology", "method": "ENSEMBL_PARALOGUES"},
        {"category": "GenomicAlignTree.tree_alignment", "method": "EPO_EXTENDED"},
    ]
    assert mock_get.call_args.kwargs["params"] == {
        "class": "Homology",
        "compara": "vertebrates",
    }


def test_fetch_compara_species_sets_accepts_compara_param(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    body = [{"method": "EPO", "name": "10 primates EPO"}]
    with patch("nullifier.tools.ensembl.requests.get", return_value=_mock_resp(body)) as mock_get:
        species_sets = e.fetch_compara_species_sets(compara="vertebrates")

    assert species_sets == body
    assert mock_get.call_args.kwargs["params"] == {"compara": "vertebrates"}


def test_fetch_comparas_unwraps_current_api_response(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "_cfg", {
        "base_url": "https://rest.ensembl.org",
        "rate_limit_per_second": 1000,
        "cache_path": str(tmp_path / "test_cache.db"),
        "cache_ttl_days": 30,
    })
    body = {"comparas": [{"name": "vertebrates", "release": 115}]}
    with patch("nullifier.tools.ensembl.requests.get", return_value=_mock_resp(body)):
        assert e.fetch_comparas() == body["comparas"]
