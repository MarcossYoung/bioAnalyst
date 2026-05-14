import time
import requests

# Routes through Europe PMC's preprint index (SRC:PPR covers bioRxiv/medRxiv).
BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
TIMEOUT = (4, 8)


def search(query: str, max_results: int = 10) -> list[dict]:
    r = requests.get(f"{BASE}/search", params={
        "query": f"{query} AND SRC:PPR", "format": "json",
        "pageSize": max_results, "resultType": "core",
    }, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(0.3)
    results = r.json().get("resultList", {}).get("result", [])
    return [_normalize(p) for p in results if p.get("abstractText")]


def _normalize(p: dict) -> dict:
    return {
        "source": "biorxiv",
        "id": p.get("id", ""),
        "doi": p.get("doi"),
        "title": p.get("title", ""),
        "abstract": p.get("abstractText", ""),
        "year": int(p.get("pubYear")) if str(p.get("pubYear", "")).isdigit() else None,
        "venue": "bioRxiv/medRxiv preprint",
        "citation_count": p.get("citedByCount"),
        "influential_citation_count": None,
        "authors": (p.get("authorString") or "").split(", ") if p.get("authorString") else [],
    }
