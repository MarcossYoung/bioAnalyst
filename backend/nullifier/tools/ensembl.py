import requests
import sqlite3
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from ..config.loader import load_config

_cfg = None
_lock = threading.Lock()
_last_request_time = 0


def _get_cfg() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = load_config()["ensembl"]
    return _cfg


def _rate_limit():
    """Stay under Ensembl's 15 req/sec."""
    global _last_request_time
    cfg = _get_cfg()
    min_interval = 1.0 / cfg["rate_limit_per_second"]
    with _lock:
        now = time.time()
        elapsed = now - _last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_request_time = time.time()


def _init_cache() -> sqlite3.Connection:
    cfg = _get_cfg()
    path = Path(cfg["cache_path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _cache_get(key: str, use_cache: bool) -> dict | None:
    if not use_cache:
        return None
    cfg = _get_cfg()
    conn = _init_cache()
    row = conn.execute("SELECT value, cached_at FROM cache WHERE key = ?", (key,)).fetchone()
    conn.close()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.utcnow() - cached_at > timedelta(days=cfg["cache_ttl_days"]):
        return None
    return json.loads(row[0])


def _cache_set(key: str, value: dict):
    conn = _init_cache()
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, value, cached_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def _request(path: str, params: dict = None, use_cache: bool = True) -> dict | None:
    cfg = _get_cfg()
    cache_key = f"{path}?{json.dumps(params or {}, sort_keys=True)}"
    cached = _cache_get(cache_key, use_cache)
    if cached is not None:
        return cached

    _rate_limit()
    try:
        url = f"{cfg['base_url']}{path}"
        r = requests.get(url, params=params or {},
                         headers={"Accept": "application/json"}, timeout=20)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "5"))
            time.sleep(retry_after)
            r = requests.get(url, params=params or {},
                             headers={"Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        _cache_set(cache_key, data)
        return data
    except Exception as e:
        print(f"[ensembl] {path} failed: {e}")
        return None


# === Public API: 5 endpoints ===

def lookup_gene(symbol: str, use_cache: bool = True) -> dict | None:
    """GET /lookup/symbol/human/{symbol}
    Returns Ensembl ID, biotype, location, description."""
    data = _request(f"/lookup/symbol/human/{symbol}", {"expand": 0}, use_cache)
    if not data:
        return None
    return {
        "symbol": symbol,
        "ensembl_id": data.get("id"),
        "biotype": data.get("biotype"),
        "chromosome": data.get("seq_region_name"),
        "start": data.get("start"),
        "end": data.get("end"),
        "strand": data.get("strand"),
        "description": data.get("description"),
    }


def get_orthologs(symbol: str, target_taxon: int = 40674,  # Mammalia
                  use_cache: bool = True) -> list[dict]:
    """GET /homology/symbol/human/{symbol}?type=orthologues
    Returns list of orthologs across mammals with pre-computed dN/dS."""
    data = _request(
        f"/homology/symbol/human/{symbol}",
        {"type": "orthologues", "target_taxon": target_taxon, "format": "full"},
        use_cache,
    )
    if not data or not data.get("data"):
        return []
    homologies = data["data"][0].get("homologies", [])
    out = []
    for h in homologies:
        target = h.get("target", {})
        out.append({
            "target_species": target.get("species"),
            "target_id": target.get("id"),
            "target_protein_id": target.get("protein_id"),
            "ortholog_type": h.get("type"),
            "perc_id": target.get("perc_id"),
            "perc_pos": target.get("perc_pos"),
            "dn": h.get("dn"),
            "ds": h.get("ds"),
            "dnds": (h["dn"] / h["ds"]) if (h.get("dn") and h.get("ds") and h["ds"] > 0) else None,
            "method_link_type": h.get("method_link_type"),
        })
    return out


def get_paralogs(symbol: str, use_cache: bool = True) -> list[dict]:
    """GET /homology/symbol/human/{symbol}?type=paralogues
    Returns paralog relationships within the same species (gene family expansion)."""
    data = _request(
        f"/homology/symbol/human/{symbol}",
        {"type": "paralogues", "format": "full"},
        use_cache,
    )
    if not data or not data.get("data"):
        return []
    homologies = data["data"][0].get("homologies", [])
    out = []
    for h in homologies:
        target = h.get("target", {})
        out.append({
            "paralog_id": target.get("id"),
            "paralog_protein_id": target.get("protein_id"),
            "paralog_type": h.get("type"),
            "perc_id": target.get("perc_id"),
            "dn": h.get("dn"),
            "ds": h.get("ds"),
            "taxonomy_level": h.get("taxonomy_level"),
        })
    return out


def get_gene_tree(symbol: str, use_cache: bool = True) -> dict | None:
    """GET /genetree/member/symbol/human/{symbol}
    Returns phylogenetic gene tree (JSON) with duplication events annotated.
    Downstream code walks the tree and summarizes duplication events."""
    data = _request(
        f"/genetree/member/symbol/human/{symbol}",
        {},
        use_cache,
    )
    if not data:
        return None
    # Walk the tree, count duplication nodes, extract taxonomy levels.
    duplications = []

    def walk(node):
        if not isinstance(node, dict):
            return
        events = node.get("events") or {}
        if events.get("type") == "duplication":
            duplications.append({
                "taxonomy_level": (node.get("taxonomy") or {}).get("scientific_name"),
                "confidence": events.get("confidence_score") or events.get("duplication_confidence_score"),
            })
        for child in (node.get("children") or []):
            walk(child)

    walk(data.get("tree") or data)
    return {
        "tree_id": data.get("id"),
        "duplications": duplications,
        "duplication_count": len(duplications),
    }


def get_regulatory_features(chromosome: str, start: int, end: int,
                            flank: int = 5000, use_cache: bool = True) -> list[dict]:
    """GET /overlap/region/human/{region}?feature=regulatory
    Returns regulatory features in a gene's neighborhood (±flank bp)."""
    region = f"{chromosome}:{max(1, start - flank)}-{end + flank}"
    data = _request(
        f"/overlap/region/human/{region}",
        {"feature": "regulatory"},
        use_cache,
    )
    if not data:
        return []
    return [
        {
            "id": f.get("id"),
            "feature_type": f.get("feature_type"),
            "description": f.get("description"),
            "bound_start": f.get("bound_start"),
            "bound_end": f.get("bound_end"),
        }
        for f in data
    ]


def get_motif_features(chromosome: str, start: int, end: int,
                       flank: int = 5000, use_cache: bool = True) -> list[dict]:
    """GET /overlap/region/human/{region}?feature=motif
    Returns transcription factor binding motifs (subset of regulatory features)."""
    region = f"{chromosome}:{max(1, start - flank)}-{end + flank}"
    data = _request(
        f"/overlap/region/human/{region}",
        {"feature": "motif"},
        use_cache,
    )
    if not data:
        return []
    return [
        {
            "binding_matrix_stable_id": f.get("binding_matrix_stable_id"),
            "transcription_factor_complex": f.get("transcription_factor_complex"),
            "score": f.get("score"),
            "start": f.get("start"),
            "end": f.get("end"),
        }
        for f in data
    ]