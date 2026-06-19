"""Tests for tools.gnomad — cache round-trip and failure handling."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from nullifier.tools import gnomad as g


_FAKE_CONSTRAINT = {
    "oe_lof_upper": 0.42,
    "pli": 0.98,
    "syn_z": 0.1,
    "mis_z": 2.3,
    "obs_lof": 3,
    "exp_lof": 12.5,
}

_FAKE_RESPONSE_BODY = {
    "data": {
        "gene": {
            "gnomad_constraint": _FAKE_CONSTRAINT,
        }
    }
}


def _make_mock_response(body: dict, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path, monkeypatch):
    """Redirect cache to a temp db and reset module state between tests."""
    monkeypatch.setattr(g, "_CACHE_PATH", tmp_path / "gnomad_cache.db")
    monkeypatch.setattr(g, "_conn", None)
    monkeypatch.setattr(g, "_last_request", 0.0)
    yield
    if g._conn is not None:
        g._conn.close()
        g._conn = None


def test_fetch_constraint_live_miss_then_cache_hit():
    with patch("nullifier.tools.gnomad.requests.post",
               return_value=_make_mock_response(_FAKE_RESPONSE_BODY)) as mock_post:
        result1 = g.fetch_constraint("ENSG00000000001")
        assert result1 is not None
        assert result1["loeuf"] == pytest.approx(0.42)
        assert result1["pli"] == pytest.approx(0.98)
        assert result1["_source"] == "gnomad"
        assert result1["_genome_build"] == "GRCh38"
        assert "_cached_at" in result1
        mock_post.assert_called_once()

        result2 = g.fetch_constraint("ENSG00000000001")
        assert result2 is not None
        assert result2["loeuf"] == pytest.approx(0.42)
        mock_post.assert_called_once()  # no second network call


def test_fetch_constraint_returns_none_on_network_error():
    with patch("nullifier.tools.gnomad.requests.post", side_effect=ConnectionError("down")):
        assert g.fetch_constraint("ENSG00000000002") is None


def test_fetch_constraint_returns_none_on_empty_constraint():
    body = {"data": {"gene": {"gnomad_constraint": None}}}
    with patch("nullifier.tools.gnomad.requests.post",
               return_value=_make_mock_response(body)):
        assert g.fetch_constraint("ENSG00000000003") is None


def test_fetch_constraint_returns_none_on_empty_ensg():
    assert g.fetch_constraint("") is None
    assert g.fetch_constraint(None) is None


def test_fetch_constraint_provenance_fields_present():
    with patch("nullifier.tools.gnomad.requests.post",
               return_value=_make_mock_response(_FAKE_RESPONSE_BODY)):
        result = g.fetch_constraint("ENSG00000000004", genome="GRCh37")
    assert result["_source"] == "gnomad"
    assert result["_genome_build"] == "GRCh37"
    assert result["_cached_at"]


def test_cache_respects_ttl(monkeypatch):
    with patch("nullifier.tools.gnomad.requests.post",
               return_value=_make_mock_response(_FAKE_RESPONSE_BODY)) as mock_post:
        g.fetch_constraint("ENSG00000000005")
        assert mock_post.call_count == 1

    # Manually expire the cache entry
    conn = g._get_conn()
    stale = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=g._CACHE_TTL_DAYS + 1)).isoformat()
    conn.execute("UPDATE constraint_cache SET cached_at=?", (stale,))
    conn.commit()

    with patch("nullifier.tools.gnomad.requests.post",
               return_value=_make_mock_response(_FAKE_RESPONSE_BODY)) as mock_post2:
        g.fetch_constraint("ENSG00000000005")
        assert mock_post2.call_count == 1  # re-fetched after TTL expiry
