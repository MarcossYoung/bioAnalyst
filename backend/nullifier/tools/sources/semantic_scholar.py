import os
import time
import requests

from ...config.loader import load_config

BASE = "https://api.semanticscholar.org/graph/v1"
FIELDS = ",".join((
    "paperId", "title", "abstract", "year", "venue", "citationCount",
    "influentialCitationCount", "authors", "externalIds", "tldr",
    "openAccessPdf", "publicationTypes", "publicationDate", "s2FieldsOfStudy",
))
TIMEOUT = (4, 8)


def _headers() -> dict[str, str]:
    api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    return {"x-api-key": api_key} if api_key else {}


def _search_params(query: str, max_results: int) -> dict:
    literature_cfg = load_config().get("literature", {})
    params = {"query": query, "limit": max_results, "fields": FIELDS}
    fields_of_study = str(
        literature_cfg.get("semantic_scholar_fields_of_study", "") or ""
    ).strip()
    min_citations = int(literature_cfg.get("semantic_scholar_min_citations", 0) or 0)
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study
    if min_citations > 0:
        params["minCitationCount"] = min_citations
    return params


def search(query: str, max_results: int = 10) -> list[dict]:
    # One short retry on 429; anything else (or a second 429) propagates so
    # federated_search() can record the failure and trip the host breaker.
    for attempt in range(2):
        r = requests.get(
            f"{BASE}/paper/search",
            params=_search_params(query, max_results),
            headers=_headers(),
            timeout=TIMEOUT,
        )
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(int(r.headers.get("Retry-After", 10)), 20))
            continue
        r.raise_for_status()
        time.sleep(0.4)
        return [_normalize(p) for p in r.json().get("data", []) if p.get("abstract")]
    return []


def match_by_title(title: str) -> dict | None:
    response = requests.get(
        f"{BASE}/paper/search/match",
        params={"query": title, "fields": FIELDS},
        headers=_headers(),
        timeout=TIMEOUT,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    paper = payload.get("data", payload)
    if isinstance(paper, list):
        paper = paper[0] if paper else None
    if not isinstance(paper, dict) or not paper.get("paperId"):
        return None
    normalized = _normalize(paper)
    normalized["match_score"] = paper.get("matchScore", payload.get("matchScore"))
    return normalized


def _normalize(p: dict) -> dict:
    tldr = p.get("tldr") or {}
    open_access_pdf = p.get("openAccessPdf") or {}
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
        "tldr": tldr.get("text", "") if isinstance(tldr, dict) else str(tldr),
        "open_access_pdf": open_access_pdf.get("url") if isinstance(open_access_pdf, dict) else open_access_pdf,
        "publication_types": p.get("publicationTypes") or [],
        "publication_date": p.get("publicationDate"),
        "fields_of_study": [
            field.get("category", "") if isinstance(field, dict) else str(field)
            for field in (p.get("s2FieldsOfStudy") or [])
        ],
    }
