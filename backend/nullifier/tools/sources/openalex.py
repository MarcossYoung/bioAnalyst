import time
import requests

BASE = "https://api.openalex.org"
UA = {"User-Agent": "nullifier/0.1 (mailto:research@example.com)"}
TIMEOUT = (4, 8)


def search(query: str, max_results: int = 10) -> list[dict]:
    r = requests.get(f"{BASE}/works", params={
        "search": query, "per-page": max_results,
    }, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(0.3)
    results = r.json().get("results", [])
    return [_normalize(w) for w in results if _has_abstract(w)]


def _has_abstract(w: dict) -> bool:
    return bool(w.get("abstract_inverted_index"))


def _reconstruct_abstract(inverted: dict) -> str:
    if not inverted:
        return ""
    positions = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def _normalize(w: dict) -> dict:
    venue = (w.get("primary_location") or {}).get("source") or {}
    return {
        "source": "openalex",
        "id": w.get("id", "").replace("https://openalex.org/", ""),
        "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None,
        "title": w.get("title", "") or "",
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index") or {}),
        "year": w.get("publication_year"),
        "venue": venue.get("display_name"),
        "citation_count": w.get("cited_by_count"),
        "influential_citation_count": None,
        "authors": [a.get("author", {}).get("display_name", "")
                    for a in (w.get("authorships") or [])],
    }
