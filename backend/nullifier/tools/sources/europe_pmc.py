import time
import requests

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"

# (connect, read). A useful literature search responds in 1-3s; don't sit on a
# stalled connection. federated_search() catches whatever this raises.
TIMEOUT = (4, 8)


def search(query: str, max_results: int = 10) -> list[dict]:
    r = requests.get(f"{BASE}/search", params={
        "query": query, "format": "json", "pageSize": max_results,
        "resultType": "core",
    }, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(0.3)
    results = r.json().get("resultList", {}).get("result", [])
    return [_normalize(p) for p in results if p.get("abstractText")]


def _normalize(p: dict) -> dict:
    return {
        "source": "europe_pmc",
        "id": p.get("id", ""),
        "doi": p.get("doi"),
        "title": p.get("title", ""),
        "abstract": p.get("abstractText", ""),
        "year": int(p.get("pubYear")) if str(p.get("pubYear", "")).isdigit() else None,
        "venue": p.get("journalTitle"),
        "citation_count": p.get("citedByCount"),
        "influential_citation_count": None,
        "authors": (p.get("authorString") or "").split(", ") if p.get("authorString") else [],
    }
