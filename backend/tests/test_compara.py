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
                "dn": 0.05,
                "ds": 0.1,
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

_FAKE_CONDENSED_BODY = {
    "data": [{
        "homologies": [
            {"method_link_type": "ENSEMBL_ORTHOLOGUES"},
            {"method_link_type": "ENSEMBL_ORTHOLOGUES"},
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


# ── fetch_compara_metadata ───────────────────────────────────────────────────

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
