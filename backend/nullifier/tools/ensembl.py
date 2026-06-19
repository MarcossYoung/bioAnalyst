import requests
import sqlite3
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any
from ..config.loader import load_config

HGNC_BASE = "https://rest.genenames.org"
_HGNC_HEADERS = {"Accept": "application/json"}
ALIAS_NOT_FOUND = "__none__"  # sentinel cached when HGNC has no canonical mapping

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aliases (
            retired TEXT PRIMARY KEY,
            canonical TEXT NOT NULL,
            cached_at TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _alias_get(retired: str) -> str | None:
    """Returns canonical symbol, ALIAS_NOT_FOUND sentinel for negative cache, or None if uncached."""
    cfg = _get_cfg()
    conn = _init_cache()
    row = conn.execute(
        "SELECT canonical, cached_at FROM aliases WHERE retired = ?", (retired,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.now(timezone.utc).replace(tzinfo=None) - cached_at > timedelta(days=cfg["cache_ttl_days"]):
        return None
    return row[0]


def _alias_set(retired: str, canonical: str):
    conn = _init_cache()
    conn.execute(
        "INSERT OR REPLACE INTO aliases (retired, canonical, cached_at) VALUES (?, ?, ?)",
        (retired, canonical, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    )
    conn.commit()
    conn.close()


def _hgnc_canonical(symbol: str) -> str | None:
    """Resolve a retired/alias HGNC symbol to its current canonical symbol.

    Tries prev_symbol first (retired -> current) then alias_symbol. Returns None
    if HGNC has no mapping or is unreachable; the caller decides whether to negative-cache.
    """
    for field in ("prev_symbol", "alias_symbol"):
        try:
            r = requests.get(
                f"{HGNC_BASE}/fetch/{field}/{symbol}",
                headers=_HGNC_HEADERS,
                timeout=(4, 8),
            )
            r.raise_for_status()
            docs = r.json().get("response", {}).get("docs", [])
            for doc in docs:
                canonical = doc.get("symbol")
                if canonical and canonical != symbol:
                    return canonical
        except Exception as e:
            print(f"[hgnc] {field} lookup for {symbol} failed: {e}", file=sys.stderr)
            continue
    return None


def _cache_get(key: str, use_cache: bool) -> Any | None:
    if not use_cache:
        return None
    cfg = _get_cfg()
    conn = _init_cache()
    row = conn.execute("SELECT value, cached_at FROM cache WHERE key = ?", (key,)).fetchone()
    conn.close()
    if not row:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if datetime.now(timezone.utc).replace(tzinfo=None) - cached_at > timedelta(days=cfg["cache_ttl_days"]):
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError as e:
        print(f"[ensembl] ignoring malformed cache entry for {key}: {e}", file=sys.stderr)
        return None


def _cache_set(key: str, value: Any):
    conn = _init_cache()
    conn.execute(
        "INSERT OR REPLACE INTO cache (key, value, cached_at) VALUES (?, ?, ?)",
        (key, json.dumps(value), datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    )
    conn.commit()
    conn.close()


def _ensembl_base_urls(cfg: dict) -> list[str]:
    urls = [cfg["base_url"]]
    for url in cfg.get("fallback_base_urls", []):
        if url and url not in urls:
            urls.append(url)
    return urls


def _request(path: str, params: dict = None, use_cache: bool = True) -> Any | None:
    cfg = _get_cfg()
    cache_key = f"{path}?{json.dumps(params or {}, sort_keys=True)}"
    cached = _cache_get(cache_key, use_cache)
    if cached is not None:
        return cached

    for base_url in _ensembl_base_urls(cfg):
        _rate_limit()
        try:
            url = f"{base_url}{path}"
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
            if base_url != cfg["base_url"]:
                print(f"[ensembl] {path} resolved via fallback {base_url}", file=sys.stderr)
            return data
        except Exception as e:
            print(f"[ensembl] {base_url}{path} failed: {e}", file=sys.stderr)
    return None


def _post(path: str, payload: dict, params: dict = None, use_cache: bool = True) -> Any | None:
    cfg = _get_cfg()
    cache_key = f"POST {path}?{json.dumps(params or {}, sort_keys=True)}:{json.dumps(payload, sort_keys=True)}"
    cached = _cache_get(cache_key, use_cache)
    if cached is not None:
        return cached

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    for base_url in _ensembl_base_urls(cfg):
        _rate_limit()
        try:
            url = f"{base_url}{path}"
            r = requests.post(url, params=params or {}, json=payload, headers=headers, timeout=30)
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", "5"))
                time.sleep(retry_after)
                r = requests.post(url, params=params or {}, json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
            _cache_set(cache_key, data)
            if base_url != cfg["base_url"]:
                print(f"[ensembl] POST {path} resolved via fallback {base_url}", file=sys.stderr)
            return data
        except Exception as e:
            print(f"[ensembl] POST {base_url}{path} failed: {e}", file=sys.stderr)
    return None


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# === Public API: 5 endpoints ===

def lookup_gene(symbol: str, use_cache: bool = True) -> dict | None:
    """GET /lookup/symbol/human/{symbol}
    Returns Ensembl ID, biotype, location, description.

    Falls back through HGNC's prev_symbol/alias_symbol resolver when Ensembl can't
    find the symbol — handles retired HGNC symbols (e.g. PGCP -> CNDP1). The
    resulting dict carries the canonical symbol; ``_resolved_from`` records the
    original input when a substitution happened.
    """
    data = _request(f"/lookup/symbol/human/{symbol}", {"expand": 0}, use_cache)
    if data:
        return _build_gene_record(symbol, data)

    canonical = _alias_get(symbol) if use_cache else None
    if canonical == ALIAS_NOT_FOUND:
        return None
    if canonical is None:
        canonical = _hgnc_canonical(symbol)
        if canonical is None:
            if use_cache:
                _alias_set(symbol, ALIAS_NOT_FOUND)
            return None
        _alias_set(symbol, canonical)

    data = _request(f"/lookup/symbol/human/{canonical}", {"expand": 0}, use_cache)
    if not data:
        return None
    print(f"[hgnc] resolved {symbol} -> {canonical}", file=sys.stderr)
    record = _build_gene_record(canonical, data)
    record["_resolved_from"] = symbol
    return record


def _build_gene_record(symbol: str, data: dict) -> dict:
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


def lookup_genes_by_id_batch(
    ensembl_ids: list[str],
    use_cache: bool = True,
    batch_size: int | None = None,
    on_progress=None,
) -> dict[str, dict]:
    """Batch POST /lookup/id for up to 1,000 Ensembl IDs per request."""
    ids = [x for x in dict.fromkeys(ensembl_ids or []) if x]
    if not ids:
        return {}
    size = batch_size or int(_get_cfg().get("batch_size", 1000))
    out: dict[str, dict] = {}
    fetched = 0
    for chunk in _chunks(ids, size):
        data = _post("/lookup/id", {"ids": chunk}, {"expand": 0}, use_cache)
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if isinstance(v, dict)})
        fetched += len(chunk)
        if on_progress:
            on_progress(min(fetched, len(ids)), len(ids))
    return out


def fetch_orthologs_by_id_batch(
    ensembl_ids: list[str],
    target_taxon: int = 40674,
    use_cache: bool = True,
    batch_size: int | None = None,
    fmt: str = "full",
    on_progress=None,
) -> dict[str, list[dict]]:
    """Batch POST /homology/id for multiple Ensembl IDs."""
    ids = [x for x in dict.fromkeys(ensembl_ids or []) if x]
    if not ids:
        return {}
    size = batch_size or int(_get_cfg().get("batch_size", 1000))
    out: dict[str, list[dict]] = {}
    fetched = 0
    for chunk in _chunks(ids, size):
        data = _post(
            "/homology/id",
            {"ids": chunk},
            {"type": "orthologues", "target_taxon": target_taxon, "format": fmt},
            use_cache,
        )
        entries = data.get("data", []) if isinstance(data, dict) else []
        for entry in entries:
            source_id = entry.get("id")
            homologies = entry.get("homologies", [])
            if fmt == "condensed":
                # Condensed homologies have no nested `target`; species/id/etc.
                # are top-level. _parse_ortholog only understands the full format
                # (target.species) and would yield target_species=None here.
                parsed = [{
                    "target_species": h.get("species"),
                    "target_id": h.get("id"),
                    "target_protein_id": h.get("protein_id"),
                    "ortholog_type": h.get("type"),
                    "method_link_type": h.get("method_link_type"),
                } for h in homologies]
            else:
                parsed = [_parse_ortholog(h) for h in homologies]
            if source_id:
                out[source_id] = parsed
        fetched += len(chunk)
        if on_progress:
            on_progress(min(fetched, len(ids)), len(ids))
    return out


def _dnds(homology: dict) -> float | None:
    value = homology.get("dn_ds")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _parse_ortholog(h: dict) -> dict:
    source = h.get("source", {}) or {}
    target = h.get("target", {}) or {}
    dnds = _dnds(h)
    out = {
        "target_species": target.get("species"),
        "target_id": target.get("id"),
        "source_protein_id": source.get("protein_id"),
        "target_protein_id": target.get("protein_id"),
        "source_align_seq": source.get("align_seq"),
        "target_align_seq": target.get("align_seq"),
        "ortholog_type": h.get("type"),
        "perc_id": target.get("perc_id"),
        "perc_pos": target.get("perc_pos"),
        "dn_ds": h.get("dn_ds"),
        "dnds": dnds,
        "method_link_type": h.get("method_link_type"),
    }
    if dnds is not None:
        out["dnds_source"] = "ensembl_compara_dn_ds"
    return out


def screen_panel_coverage_by_id_batch(
    ensembl_ids: list[str],
    panel: list[str],
    use_cache: bool = True,
    batch_size: int | None = None,
    on_progress=None,
) -> dict[str, dict]:
    """Cheap batch screen for whether genes have orthologs in panel species.

    Ensembl has no batch (POST) homology endpoint — POST /homology/id returns
    404 — so this fans out per-gene condensed GET /homology/id/human/{id} calls
    across a thread pool. Each call goes through _request, which is rate-limited
    (shared lock) and cached. Condensed format avoids alignment payloads. The
    result is intentionally coarse: downstream code still applies the precise
    one-to-one ortholog filter before rate-vector analyses.
    """
    ids = [x for x in dict.fromkeys(ensembl_ids or []) if x]
    if not ids:
        return {}
    panel_set = {str(s).lower() for s in (panel or []) if s}
    out: dict[str, dict] = {
        ensg: {"species_count": 0, "panel_species": set()}
        for ensg in ids
    }
    workers = max(1, min(8, len(ids)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_panel_species_for_id, ensg, use_cache): ensg
            for ensg in ids
        }
        fetched = 0
        for fut in as_completed(futures):
            ensg = futures[fut]
            try:
                species = fut.result()
            except Exception as e:
                print(f"[ensembl] panel coverage for {ensg} failed: {e}", file=sys.stderr)
            else:
                panel_species = species & panel_set
                out[ensg] = {
                    "species_count": len(panel_species),
                    "panel_species": panel_species,
                }
            fetched += 1
            if on_progress:
                on_progress(fetched, len(ids))
    return out


def _panel_species_for_id(ensg_id: str, use_cache: bool = True) -> set[str]:
    """Set of ortholog species (lower-cased) for one human Ensembl gene id,
    from the condensed homology endpoint. Returns an empty set on failure."""
    data = _request(
        f"/homology/id/human/{ensg_id}",
        {"type": "orthologues", "format": "condensed"},
        use_cache,
    )
    if not data or not data.get("data"):
        return set()
    homologies = data["data"][0].get("homologies", [])
    return {
        str(h.get("species") or "").lower()
        for h in homologies
        if h.get("species")
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
        out.append(_parse_ortholog(h))
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


# === Compara metadata endpoints (Day 4) ===

def fetch_orthologs_by_id(ensg_id: str, target_taxon: int = 40674,
                           use_cache: bool = True) -> list[dict]:
    """GET /homology/id/human/{ensg_id} — ENSG-based ortholog lookup.
    Same return shape as get_orthologs; used as fallback when symbol lookup fails."""
    data = _request(
        f"/homology/id/human/{ensg_id}",
        {"type": "orthologues", "target_taxon": target_taxon, "format": "full"},
        use_cache,
    )
    if not data or not data.get("data"):
        return []
    homologies = data["data"][0].get("homologies", [])
    out = []
    for h in homologies:
        out.append(_parse_ortholog(h))
    return out


def fetch_cds_sequence(ensembl_id: str, use_cache: bool = True) -> str | None:
    """GET /sequence/id/{ensembl_id}?type=cds.
    Returns raw nucleotide string or None on failure."""
    data = _request(
        f"/sequence/id/{ensembl_id}",
        {"type": "cds", "content_type": "application/json"},
        use_cache,
    )
    if not data:
        return None
    return data.get("seq") or data.get("sequence")


def resolve_cds_for_protein(protein_id: str, use_cache: bool = True) -> str | None:
    """Resolve an ENSP/ortholog protein ID to its parent transcript CDS."""
    if not protein_id:
        return None
    data = _request(f"/lookup/id/{protein_id}", {"expand": 0}, use_cache)
    if not isinstance(data, dict):
        return None
    transcript_id = data.get("Parent")
    if not transcript_id:
        return None
    return fetch_cds_sequence(transcript_id, use_cache=use_cache)


def fetch_gene_tree_aligned(ensembl_id: str, use_cache: bool = True) -> dict | None:
    """GET /genetree/member/id/human/{ensembl_id} — Compara aligned cDNA + Newick tree for PAML.

    Returns {sequences: {species: aligned_cdna}, newick: str}, or None when the gene
    has no Compara family or the alignment is empty.
    Prunes to Mammalia (taxon 40674).
    """
    data = _request(
        f"/genetree/member/id/human/{ensembl_id}",
        {"sequence": "cdna", "aligned": 1, "nh_format": "simple",
         "compara": "multi", "prune_taxon": 40674},
        use_cache=use_cache,
    )
    if not data:
        return None
    sequences: dict = {}

    def _walk(node: dict) -> None:
        children = node.get("children") or []
        if not children:  # leaf
            sp = (node.get("taxonomy") or {}).get("scientific_name", "").replace(" ", "_")
            seq = (node.get("sequence") or {}).get("mol_seq", {}).get("seq")
            if sp and seq:
                sequences[sp] = seq
        for c in children:
            _walk(c)

    _walk(data.get("tree") or {})
    newick = data.get("newick", "")
    if not sequences:
        return None
    return {"sequences": sequences, "newick": newick}


def fetch_compara_metadata(ensg_id: str, use_cache: bool = True) -> dict:
    """GET /homology/id/human/{ensg_id}?format=condensed — Compara membership metadata.
    Returns {in_compara, species_count, method_link_types}.
    Used for failure diagnosis when ortholog fetch returns empty."""
    data = _request(
        f"/homology/id/human/{ensg_id}",
        {"type": "orthologues", "format": "condensed"},
        use_cache,
    )
    if not data or not data.get("data"):
        return {"in_compara": False, "species_count": 0, "method_link_types": []}
    homologies = data["data"][0].get("homologies", [])
    method_types = list({h.get("method_link_type") for h in homologies if h.get("method_link_type")})
    return {
        "in_compara": True,
        "species_count": len(homologies),
        "method_link_types": method_types,
    }


def fetch_compara_methods(
    use_cache: bool = True,
    class_filter: str | None = None,
    compara: str | None = None,
) -> list[dict]:
    """GET /info/compara/methods/ — available Compara alignment/homology methods."""
    params = {}
    if class_filter:
        params["class"] = class_filter
    if compara:
        params["compara"] = compara
    data = _request("/info/compara/methods/", params, use_cache)
    if isinstance(data, dict):
        return [
            {"category": category, "method": method}
            for category, methods in data.items()
            for method in (methods if isinstance(methods, list) else [])
        ]
    return data if isinstance(data, list) else []


def fetch_compara_species_sets(
    method: str = "EPO",
    use_cache: bool = True,
    compara: str | None = None,
) -> list[dict]:
    """GET /info/compara/species_sets/{method} — species in a named alignment."""
    params = {"compara": compara} if compara else {}
    data = _request(f"/info/compara/species_sets/{method}", params, use_cache)
    return data if isinstance(data, list) else []


def fetch_comparas(use_cache: bool = True) -> list[dict]:
    """GET /info/comparas — all available Compara databases."""
    data = _request("/info/comparas", {}, use_cache)
    if isinstance(data, dict) and isinstance(data.get("comparas"), list):
        return data["comparas"]
    return data if isinstance(data, list) else []


def fetch_gene_tree_by_id(tree_id: str, use_cache: bool = True) -> dict | None:
    """GET /genetree/id/{tree_id} — fetch gene tree by Compara stable tree ID.
    Returns same shape as fetch_gene_tree_aligned; use when tree_id is already known."""
    data = _request(
        f"/genetree/id/{tree_id}",
        {"sequence": "cdna", "aligned": 1, "nh_format": "simple",
         "compara": "multi", "prune_taxon": 40674},
        use_cache=use_cache,
    )
    if not data:
        return None
    sequences: dict = {}

    def _walk(node: dict) -> None:
        children = node.get("children") or []
        if not children:
            sp = (node.get("taxonomy") or {}).get("scientific_name", "").replace(" ", "_")
            seq = (node.get("sequence") or {}).get("mol_seq", {}).get("seq")
            if sp and seq:
                sequences[sp] = seq
        for c in children:
            _walk(c)

    _walk(data.get("tree") or {})
    newick = data.get("newick", "")
    if not sequences:
        return None
    return {"sequences": sequences, "newick": newick}
