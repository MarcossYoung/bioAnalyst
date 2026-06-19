"""gnomAD GraphQL client with SQLite cache.

Fetches per-gene constraint scores (LOEUF, pLI, syn_z, mis_z) from the
gnomAD API. Cached in ~/.nullifier/gnomad_cache.db with a 30-day TTL.
Returns None on any failure — never throws.

Provenance: every returned dict carries _source, _genome_build, _cached_at.
"""
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_GNOMAD_API = "https://gnomad.broadinstitute.org/api"
_CACHE_PATH = Path.home() / ".nullifier" / "gnomad_cache.db"
_CACHE_TTL_DAYS = 30
_RATE_PER_SEC = 3.0

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()
_last_request: float = 0.0

_GQL = """
query GeneConstraint($geneId: String!, $referenceGenome: ReferenceGenomeId!) {
  gene(gene_id: $geneId, reference_genome: $referenceGenome) {
    gnomad_constraint {
      oe_lof_upper
      pli
      syn_z
      mis_z
      obs_lof
      exp_lof
    }
  }
}
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(_CACHE_PATH), check_same_thread=False)
        c.execute("""
            CREATE TABLE IF NOT EXISTS constraint_cache (
                ensg_id  TEXT NOT NULL,
                genome   TEXT NOT NULL,
                value    TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                PRIMARY KEY (ensg_id, genome)
            )
        """)
        c.commit()
        _conn = c
    return _conn


def _rate_limit() -> None:
    global _last_request
    min_interval = 1.0 / _RATE_PER_SEC
    with _lock:
        elapsed = time.time() - _last_request
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_request = time.time()


def _cache_get(ensg_id: str, genome: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT value, cached_at FROM constraint_cache WHERE ensg_id=? AND genome=?",
        (ensg_id, genome),
    ).fetchone()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.now(timezone.utc).replace(tzinfo=None) - cached_at > timedelta(days=_CACHE_TTL_DAYS):
        return None
    return json.loads(row[0])


def _cache_set(ensg_id: str, genome: str, value: dict) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO constraint_cache (ensg_id, genome, value, cached_at) VALUES (?,?,?,?)",
        (ensg_id, genome, json.dumps(value), datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
    )
    conn.commit()


def fetch_constraint(ensg_id: str, genome: str = "GRCh38") -> dict | None:
    """Return constraint metrics for one gene, or None on failure.

    Keys: loeuf, pli, syn_z, mis_z, obs_lof, exp_lof,
          _source, _genome_build, _cached_at.
    """
    if not ensg_id:
        return None
    try:
        cached = _cache_get(ensg_id, genome)
        if cached is not None:
            return cached

        _rate_limit()
        resp = requests.post(
            _GNOMAD_API,
            json={"query": _GQL, "variables": {"geneId": ensg_id, "referenceGenome": genome}},
            timeout=(5, 15),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        body = resp.json()

        gene = (body.get("data") or {}).get("gene") or {}
        c = (gene.get("gnomad_constraint") or {})
        if not c:
            return None

        result = {
            "loeuf":    c.get("oe_lof_upper"),
            "pli":      c.get("pli"),
            "syn_z":    c.get("syn_z"),
            "mis_z":    c.get("mis_z"),
            "obs_lof":  c.get("obs_lof"),
            "exp_lof":  c.get("exp_lof"),
            "_source":       "gnomad",
            "_genome_build": genome,
            "_cached_at":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
        _cache_set(ensg_id, genome, result)
        return result

    except Exception:
        return None
