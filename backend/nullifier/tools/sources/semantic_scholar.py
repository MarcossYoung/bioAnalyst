import os
import time
import requests

BASE = "https://api.semanticscholar.org/graph/v1"
FIELDS = "paperId,title,abstract,year,venue,citationCount,influentialCitationCount,authors,externalIds"
TIMEOUT = (4, 8)

HEADERS = {}
if os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
    HEADERS["x-api-key"] = os.environ["SEMANTIC_SCHOLAR_API_KEY"]


def search(query: str, max_results: int = 10) -> list[dict]:
    # One short retry on 429; anything else (or a second 429) propagates so
    # federated_search() can record the failure and trip the host breaker.
    for attempt in range(2):
        r = requests.get(f"{BASE}/paper/search", params={
            "query": query, "limit": max_results, "fields": FIELDS,
        }, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(int(r.headers.get("Retry-After", 10)), 20))
            continue
        r.raise_for_status()
        time.sleep(0.4)
        return [_normalize(p) for p in r.json().get("data", []) if p.get("abstract")]
    return []


def _normalize(p: dict) -> dict:
    return {
        "source": "semantic_scholar",
        "id": p.get("paperId", ""),
        "doi": (p.get("externalIds") or {}).get("DOI"),
        "title": p.get("title", ""),
        "abstract": p.get("abstract", ""),
        "year": p.get("year"),
        "venue": p.get("venue"),
        "citation_count": p.get("citationCount"),
        "influential_citation_count": p.get("influentialCitationCount"),
        "authors": [a.get("name", "") for a in (p.get("authors") or [])],
    }
